"""Cierres anteriores: API real, SQLite desechable y caja auditada."""
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.db import transaction
from django.contrib.auth.models import User
from Tiendas.test_suscripciones import SuscripcionesTests
from Tiendas.models import Tienda, Cierre_Caja, MovimientoCaja
from Tiendas.caja import registrar_movimiento_caja
from Aportes.models import Aporte
from Recaudos.models import Recaudo


@override_settings(ROOT_URLCONF='Tiendas.test_suscripciones')
class CierresAnterioresTests(TestCase):
    def setUp(self):
        SuscripcionesTests.setUp(self)
        clock = patch('Tiendas.fecha_operativa.timezone.now', return_value=datetime(2026,11,2,15,tzinfo=timezone.utc))
        clock.start(); self.addCleanup(clock.stop)
        self.api.force_authenticate(self.admin)
        Tienda.objects.filter(pk__in=[self.ruta.pk, self.otra.pk]).update(fecha_registro=date(2026,10,10))
        self.ruta.refresh_from_db(); self.otra.refresh_from_db()
        self.movimiento(datetime(2026,10,29,15,tzinfo=timezone.utc), 0)

    def movimiento(self, cuando, valor, tipo='AJUSTE', accion='CREACION'):
        with patch('Tiendas.fecha_operativa.timezone.now', return_value=cuando), transaction.atomic():
            t = Tienda.objects.select_for_update().get(pk=self.ruta.pk)
            return registrar_movimiento_caja(t, valor, tipo=tipo, accion=accion, usuario=self.admin)

    def cerrar(self, fecha='2026-10-30', ruta=None):
        return self.api.post(f'/tiendas/cierre/post/{fecha}/t/{ruta or self.ruta.pk}/')

    def resumen(self, fecha='2026-10-30'):
        r = self.api.get(f'/tiendas/cierre/resumen/{fecha}/t/{self.ruta.pk}/')
        self.assertEqual(r.status_code, 200, r.data)
        return r.data['validacion_cierre']

    def test_lunes_cierra_viernes_sin_actividad_sabado_domingo_ni_lunes(self):
        info = self.resumen()
        self.assertTrue(info['puede_registrar']); self.assertEqual(info['saldo_a_registrar'], '10000.00')
        r = self.cerrar(); self.assertEqual(r.status_code, 200, r.data)
        cierre = Cierre_Caja.objects.get(tienda=self.ruta)
        self.assertEqual(cierre.fecha_cierre, date(2026,10,30)); self.assertEqual(cierre.valor, 10000)
        self.assertEqual(self.resumen()['codigo'], 'cierre_existente')
        self.ruta.refresh_from_db(); self.venta.refresh_from_db()
        self.assertEqual(self.ruta.caja_inicial, 10000); self.assertEqual(self.venta.saldo_actual, 1100)
        self.assertEqual(MovimientoCaja.objects.count(), 1)

    def test_no_hay_limite_de_un_dia_si_el_historial_cubre_la_fecha(self):
        with patch('Tiendas.fecha_operativa.timezone.now', return_value=datetime(2026,12,20,15,tzinfo=timezone.utc)):
            self.assertEqual(self.cerrar().status_code, 200)

    def test_movimiento_intermedio_bloquea_aunque_lunes_no_tenga_actividad(self):
        self.movimiento(datetime(2026,11,1,15,tzinfo=timezone.utc), 100, 'APORTE')
        info = self.resumen(); self.assertEqual(info['codigo'], 'movimientos_posteriores')
        r = self.cerrar(); self.assertEqual(r.status_code, 409, r.data)
        self.assertFalse(Cierre_Caja.objects.exists())

    def test_entradas_y_salidas_compensadas_tambien_bloquean(self):
        self.movimiento(datetime(2026,10,31,15,tzinfo=timezone.utc), 100, 'APORTE')
        self.movimiento(datetime(2026,11,1,15,tzinfo=timezone.utc), -100, 'GASTO')
        self.ruta.refresh_from_db(); self.assertEqual(self.ruta.caja_inicial, 10000)
        self.assertEqual(self.cerrar().data['code'], 'movimientos_posteriores')
        self.assertFalse(Cierre_Caja.objects.exists())

    def test_correccion_o_anulacion_posterior_no_se_pierde_aunque_no_quede_registro_original(self):
        for accion in ('CORRECCION', 'REVERSA'):
            with self.subTest(accion=accion):
                self.movimiento(datetime(2026,11,1,15,tzinfo=timezone.utc), -50, 'RECAUDO', accion)
                self.assertEqual(self.cerrar().data['code'], 'movimientos_posteriores')
        self.assertFalse(Cierre_Caja.objects.exists())

    def test_registro_con_fecha_posterior_sin_auditoria_no_se_ignora(self):
        Aporte.objects.create(tienda=self.ruta, trabajador=self.worker.perfil, fecha=date(2026,11,1), valor=100)
        self.assertEqual(self.cerrar().data['code'], 'movimientos_posteriores')

    def test_fallas_y_movimientos_de_delta_cero_no_bloquean_el_cierre(self):
        Recaudo.objects.create(tienda=self.ruta, venta=self.venta, fecha_recaudo=date(2026,11,1), valor_recaudo=0)
        self.movimiento(datetime(2026,11,1,15,tzinfo=timezone.utc), 0, 'RECAUDO')
        self.assertTrue(self.resumen()['puede_registrar'])
        self.assertEqual(self.cerrar().status_code, 200)

    def test_no_inventa_historia_para_una_fecha_anterior_al_primer_movimiento_auditado(self):
        self.assertEqual(self.cerrar('2026-10-28').data['code'], 'historial_insuficiente')
        self.assertFalse(Cierre_Caja.objects.exists())

    def test_ruta_sin_historial_no_habilita_un_cierre_antiguo_pero_si_hoy(self):
        self.assertEqual(self.cerrar(ruta=self.otra.pk).data['code'], 'historial_insuficiente')
        self.assertEqual(self.cerrar('2026-11-02', ruta=self.otra.pk).status_code, 200)

    def test_saldo_sin_respaldo_de_la_bitacora_no_se_cierra_historicamente(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(caja_inicial=15000)
        self.assertEqual(self.cerrar().data['code'], 'saldo_sin_respaldo')
        self.assertFalse(Cierre_Caja.objects.exists())

    def test_no_acepta_futuro_fecha_invalida_ni_antes_de_crear_la_ruta(self):
        for fecha, codigo in [('2026-11-03', 'fecha_futura'), ('2026-10-09', 'fecha_anterior_ruta')]:
            r = self.cerrar(fecha); self.assertEqual(r.status_code, 400, r.data); self.assertEqual(r.data['code'], codigo)
        self.assertEqual(self.cerrar('2026-02-30').status_code, 400)
        self.assertFalse(Cierre_Caja.objects.exists())

    def test_trabajador_solo_hoy_incluso_llamando_directamente_a_api(self):
        self.api.force_authenticate(self.worker)
        self.assertEqual(self.resumen()['codigo'], 'solo_hoy_trabajador')
        self.assertEqual(self.cerrar().status_code, 403)
        self.assertEqual(self.cerrar('2026-11-02').status_code, 200)

    def test_root_puede_hacer_el_cierre_anterior_y_otra_persona_no_accede_a_la_ruta(self):
        self.api.force_authenticate(self.root); self.assertEqual(self.cerrar().status_code, 200)
        otro = User.objects.create_user('otro-admin-cierres', is_staff=True)
        self.api.force_authenticate(otro); self.assertEqual(self.cerrar('2026-10-31').status_code, 403)
        self.assertEqual(Cierre_Caja.objects.count(), 1)

    def test_doble_envio_no_duplica_y_la_consulta_no_escribe(self):
        before = MovimientoCaja.objects.count()
        self.resumen(); self.assertFalse(Cierre_Caja.objects.exists())
        self.assertEqual(self.cerrar().status_code, 200)
        self.assertEqual(self.cerrar().status_code, 409)
        self.assertEqual(Cierre_Caja.objects.count(), 1)
        self.assertEqual(MovimientoCaja.objects.count(), before)

    def test_movimiento_entre_consulta_y_guardado_obliga_a_revalidar(self):
        self.assertTrue(self.resumen()['puede_registrar'])
        self.movimiento(datetime(2026,11,2,15,tzinfo=timezone.utc), 25, 'GASTO')
        self.assertEqual(self.cerrar().status_code, 409)
        self.assertFalse(Cierre_Caja.objects.exists())

    def test_corte_por_zona_de_ruta_no_por_fecha_utc(self):
        # 02:30 UTC del sábado aún es viernes en Santiago (UTC-3).
        self.movimiento(datetime(2026,10,31,2,30,tzinfo=timezone.utc), 100)
        self.assertEqual(self.cerrar().status_code, 200)
        # El sábado local comienza a las 03:00 UTC.
        self.movimiento(datetime(2026,10,31,3,30,tzinfo=timezone.utc), 25)
        r = self.cerrar('2026-10-29'); self.assertEqual(r.status_code, 409)

    def test_hoy_y_fechas_futuras_respetan_la_zona_de_la_ruta(self):
        with patch('Tiendas.fecha_operativa.timezone.now', return_value=datetime(2026,11,2,1,tzinfo=timezone.utc)):
            self.assertEqual(self.resumen()['hoy'], '2026-11-01')
            self.assertEqual(self.cerrar('2026-11-02').data['code'], 'fecha_futura')

    def test_hoy_se_puede_cerrar_aunque_el_registro_de_creacion_sea_del_dia_utc_siguiente(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(fecha_registro=date(2026,11,3))
        with patch('Tiendas.fecha_operativa.timezone.now', return_value=datetime(2026,11,3,1,tzinfo=timezone.utc)):
            self.assertEqual(self.cerrar('2026-11-02').status_code, 200)
