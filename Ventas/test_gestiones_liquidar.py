"""Regresiones de recorrido/deuda; SQLite desechable y red bloqueada."""
from datetime import date, datetime, timezone
from unittest.mock import patch
from django.test import TestCase
from Ventas import test_calendario as fixture
from Ventas.views import list_ventas_a_liquidar
from Recaudos.models import Recaudo, Visita_Blanco
from Tiendas.models import MovimientoCaja


class GestionesLiquidarTests(TestCase):
    setUp = fixture.CalendarioAPITests.setUp
    crear = fixture.CalendarioAPITests.crear
    request = fixture.CalendarioAPITests.request
    abonar = fixture.CalendarioAPITests.abonar
    falla = fixture.CalendarioAPITests.falla

    def lista(self, fecha='2026-10-15', user=None, incluir=True):
        return self.request('get', list_ventas_a_liquidar,
            {'resumen': '1', 'incluir_gestionados': '1' if incluir else '0'},
            date=fecha, tienda_id=str(self.ruta.pk), user=user)

    def test_falla_gestionada_conserva_deuda_meta_y_caja_en_tres_modalidades(self):
        for plazo, cuotas, fecha in [('Semanal', 4, date(2026,10,8)),
                                     ('Mensual', 1, date(2026,9,15)),
                                     ('Diario', 20, date(2026,10,8))]:
            venta = self.crear(plazo=plazo, cuotas=cuotas, fecha_venta=fecha)
            antes = self.lista().data['resumen_cobranza']
            saldo = venta.saldo_actual
            self.assertEqual(self.falla(venta).status_code, 200)
            respuesta = self.lista().data
            fila = next(v for v in respuesta['creditos'] if v['id'] == venta.pk)
            self.assertTrue(fila['gestionado_hoy'])
            self.assertTrue(fila['falla_registrada_hoy'])
            self.assertGreater(float(fila['importe_pendiente_jornada']), 0)
            if fila['calendario_pago']:
                self.assertFalse(fila['calendario_pago']['permite_falla'])
                self.assertFalse(fila['calendario_pago']['pendiente_operativo'])
            self.assertEqual(respuesta['resumen_cobranza'], antes)
            self.assertEqual(self.falla(venta).status_code, 409)
            venta.refresh_from_db()
            self.assertEqual(venta.saldo_actual, saldo)
        self.assertEqual(MovimientoCaja.objects.count(), 0)
        self.assertEqual(Recaudo.objects.count(), 3)
        self.assertEqual(Visita_Blanco.objects.count(), 3)

    def test_abono_despues_de_falla_sigue_permitido(self):
        venta = self.crear()
        self.assertEqual(self.falla(venta).status_code, 200)
        self.assertEqual(self.abonar(venta, 100000).status_code, 200)
        fila = self.lista().data['creditos'][0]
        self.assertEqual(fila['importe_pendiente_jornada'], '200000.00')
        self.assertTrue(fila['gestionado_hoy'])
        self.assertTrue(fila['falla_registrada_hoy'])
        self.assertEqual(self.falla(venta).status_code, 409)

    def test_anticipo_sin_gestion_hoy_sigue_disponible(self):
        venta = self.crear()
        self.assertEqual(self.abonar(venta, 600000, '2026-10-14').status_code, 200)
        fila = self.lista().data['creditos'][0]
        self.assertFalse(fila['gestionado_hoy'])
        self.assertTrue(fila['calendario_pago']['cobro_voluntario'])
        self.assertEqual(fila['importe_pendiente_jornada'], '0.00')

    def test_falla_ayer_no_bloquea_gestion_de_hoy(self):
        venta = self.crear()
        self.falla(venta)
        with patch('Tiendas.fecha_operativa.timezone.now', return_value=datetime(2026,10,16,16,tzinfo=timezone.utc)):
            fila = self.lista('2026-10-16').data['creditos'][0]
            self.assertFalse(fila['gestionado_hoy'])
            self.assertFalse(fila['falla_registrada_hoy'])
            self.assertTrue(fila['calendario_pago']['permite_falla'])
            self.assertEqual(fila['estado_venta'], 'Atrasado')
            self.assertEqual(self.falla(venta, '2026-10-16').status_code, 200)

    def test_diario_gestionado_opcional_para_consumidores_anteriores(self):
        venta = self.crear(plazo='Diario', cuotas=20)
        self.falla(venta)
        self.assertEqual(self.lista(incluir=False).data['creditos'], [])
        self.assertEqual(len(self.lista().data['creditos']), 1)

    def test_diario_abono_parcial_no_infla_pendiente_y_conserva_base(self):
        venta = self.crear(plazo='Diario', cuotas=20)
        antes = self.lista().data['resumen_cobranza']
        self.abonar(venta, 10000)
        fila = self.lista().data['creditos'][0]
        self.assertEqual(fila['importe_pendiente_jornada'], '50000.00')
        self.assertEqual(self.lista().data['resumen_cobranza'], antes)
        self.abonar(venta, 50000)
        self.assertEqual(self.lista().data['creditos'][0]['importe_pendiente_jornada'], '0.00')

    def test_historia_por_fecha_no_confunde_visitas_y_renovaciones(self):
        venta = self.crear()
        self.falla(venta)
        self.assertFalse(self.lista('2026-10-14').data['creditos'])
        otro = self.crear()
        Recaudo.objects.create(venta=otro, tienda=self.ruta, fecha_recaudo=date(2026,10,15),
            valor_recaudo=0, es_renovacion=True)
        fila = next(v for v in self.lista().data['creditos'] if v['id'] == otro.pk)
        self.assertFalse(fila['gestionado_hoy'])
        self.assertFalse(fila['falla_registrada_hoy'])

    def test_otra_ruta_no_puede_consultar_gestiones(self):
        self.crear()
        self.assertEqual(self.lista(user=self.other).status_code, 403)

    def test_fecha_malformada_no_es_error_500(self):
        venta = self.crear()
        self.assertEqual(self.falla(venta, 'incorrecta').status_code, 400)
        self.assertEqual(Recaudo.objects.count(), 0)
