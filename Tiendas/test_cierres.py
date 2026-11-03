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

    def movimiento(self, cuando, valor, tipo='AJUSTE', accion='CREACION', **origen):
        with patch('Tiendas.fecha_operativa.timezone.now', return_value=cuando), transaction.atomic():
            t = Tienda.objects.select_for_update().get(pk=self.ruta.pk)
            return registrar_movimiento_caja(t, valor, tipo=tipo, accion=accion, usuario=self.admin, **origen)

    def abono_tardio(self, valor=100, fecha='2026-10-30', cuando=None):
        cuando = cuando or datetime(2026,10,31,15,tzinfo=timezone.utc)
        with patch('Tiendas.fecha_operativa.timezone.now', return_value=cuando):
            r = self.api.post(f'/recaudos/create/t/{self.ruta.pk}/', {
                'venta': self.venta.pk, 'fecha_recaudo': fecha, 'valor_recaudo': valor,
            }, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        return Recaudo.objects.get(pk=r.data['id'])

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

    def test_abonos_del_viernes_ingresados_el_sabado_permiten_cerrar_el_viernes(self):
        # Reproduce los importes y horas de R4 con datos exclusivamente locales.
        self.venta.valor_venta = 1000000
        self.venta.saldo_actual = 1200000
        self.venta.save(update_fields=['valor_venta', 'saldo_actual'])
        self.abono_tardio(100000, cuando=datetime(2026,10,31,3,26,tzinfo=timezone.utc))
        self.abono_tardio(30000, cuando=datetime(2026,10,31,17,27,tzinfo=timezone.utc))
        auditoria = list(MovimientoCaja.objects.values())
        info = self.resumen()
        self.assertTrue(info['puede_registrar'], info)
        self.assertEqual(info['saldo_a_registrar'], '140000.00')
        self.assertFalse(Cierre_Caja.objects.exists())
        self.assertEqual(self.cerrar().status_code, 200)
        self.assertEqual(Cierre_Caja.objects.get().valor, 140000)
        self.assertEqual(list(MovimientoCaja.objects.values()), auditoria)
        self.ruta.refresh_from_db(); self.venta.refresh_from_db()
        self.assertEqual(self.ruta.caja_inicial, 140000)
        self.assertEqual(self.venta.saldo_actual, 1070000)

    def test_abono_tardio_de_un_dia_anterior_al_cierre_tambien_esta_incluido(self):
        self.abono_tardio(fecha='2026-10-29')
        self.assertTrue(self.resumen()['puede_registrar'])
        self.assertEqual(self.cerrar().status_code, 200)

    def test_abono_que_corresponde_al_sabado_no_permite_cerrar_viernes(self):
        self.abono_tardio(fecha='2026-10-31')
        self.assertEqual(self.resumen()['codigo'], 'movimientos_posteriores')
        self.assertEqual(self.cerrar().status_code, 409)

    def test_correccion_posterior_del_importe_de_abono_tardio_bloquea(self):
        pago = self.abono_tardio()
        self.assertTrue(self.resumen()['puede_registrar'])
        r = self.api.put(f'/recaudos/{pago.pk}/update/', {
            'fecha_recaudo': '2026-10-30', 'valor_recaudo': 50,
        }, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.cerrar().data['code'], 'movimientos_posteriores')
        self.assertFalse(Cierre_Caja.objects.exists())

    def test_cambiar_solo_la_fecha_de_un_pago_no_sirve_para_saltar_el_bloqueo(self):
        pago = self.abono_tardio(fecha='2026-10-31')
        self.ruta.refresh_from_db(); saldo = self.ruta.caja_inicial
        r = self.api.put(f'/recaudos/{pago.pk}/update/', {
            'fecha_recaudo': '2026-10-30', 'valor_recaudo': 100,
        }, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        correccion = MovimientoCaja.objects.get(accion='CORRECCION')
        self.assertEqual(correccion.delta, 0)
        self.assertEqual(correccion.origen_id, pago.pk)
        self.assertEqual(correccion.saldo_anterior, saldo)
        self.assertEqual(correccion.saldo_posterior, saldo)
        self.ruta.refresh_from_db(); self.assertEqual(self.ruta.caja_inicial, saldo)
        self.assertEqual(self.resumen()['codigo'], 'movimientos_posteriores')
        self.assertEqual(self.cerrar().status_code, 409)

    def test_guardar_abono_sin_cambios_no_crea_una_correccion_ni_bloquea(self):
        pago = self.abono_tardio()
        r = self.api.put(f'/recaudos/{pago.pk}/update/', {
            'fecha_recaudo': '2026-10-30', 'valor_recaudo': 100,
        }, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(MovimientoCaja.objects.filter(accion='CORRECCION').exists())
        self.assertTrue(self.resumen()['puede_registrar'])

    def test_anulacion_posterior_de_abono_tardio_mantiene_bloqueo(self):
        pago = self.abono_tardio()
        r = self.api.delete(f'/recaudos/{pago.pk}/delete/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(Recaudo.objects.filter(pk=pago.pk).exists())
        self.assertEqual(self.cerrar().data['code'], 'movimientos_posteriores')

    def test_abono_tardio_sin_origen_verificable_no_se_acepta(self):
        pago = self.abono_tardio()
        Recaudo.objects.filter(pk=pago.pk).delete()
        self.assertEqual(self.cerrar().data['code'], 'movimientos_posteriores')

    def test_origen_de_otra_ruta_importe_distinto_o_renovacion_no_se_aceptan(self):
        pago = self.abono_tardio()
        for campos in ({'tienda': self.otra}, {'valor_recaudo': 99}, {'es_renovacion': True}):
            with self.subTest(campos=campos):
                with transaction.atomic():
                    Recaudo.objects.filter(pk=pago.pk).update(**campos)
                    self.assertEqual(self.cerrar().data['code'], 'movimientos_posteriores')
                    transaction.set_rollback(True)
        self.assertTrue(self.resumen()['puede_registrar'])

    def test_asientos_duplicados_del_mismo_abono_tardio_no_se_aceptan(self):
        pago = self.abono_tardio()
        self.movimiento(datetime(2026,11,1,15,tzinfo=timezone.utc), 100, 'RECAUDO', origen=pago)
        self.assertEqual(self.cerrar().data['code'], 'movimientos_posteriores')

    def test_abono_tardio_no_reemplaza_historial_anterior_al_dia_solicitado(self):
        self.abono_tardio(fecha='2026-10-27')
        self.assertEqual(self.cerrar('2026-10-28').data['code'], 'historial_insuficiente')

    def test_abono_tardio_no_oculta_otro_movimiento_posterior(self):
        self.abono_tardio()
        self.assertTrue(self.resumen()['puede_registrar'])
        self.movimiento(datetime(2026,11,1,15,tzinfo=timezone.utc), 50, 'APORTE')
        self.assertEqual(self.cerrar().data['code'], 'movimientos_posteriores')

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
