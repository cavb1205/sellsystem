"""Regresiones de cartera diaria. Datos ficticios, red prohibida y sin migraciones."""
from datetime import date
from decimal import Decimal
from django.test import TestCase, SimpleTestCase, override_settings
from Tiendas import test_suscripciones as fixtures
from Tiendas.alertas_operativas import _perfil_riesgo
from Ventas.models import Venta
from Ventas.views import _anotar_ventas_lista
from Ventas.serializers import VentaListaSerializer, VentaDetailSerializer
from Ventas.riesgo import importe_atrasado_registrado
from Clientes.telegram_resumen import resumen_credito
from Recaudos.models import Recaudo


class ImporteRegistradoTests(SimpleTestCase):
    def test_importe_directo_no_multiplica_cuotas_redondeadas(self):
        self.assertEqual(importe_atrasado_registrado(18000, 1, 100, 89900), Decimal('17900.00'))

    def test_adelantos_cero_y_tope_saldo(self):
        self.assertEqual(importe_atrasado_registrado(6000, 1, 18000, 102000), 0)
        self.assertEqual(importe_atrasado_registrado(6000, 80, 0, 120000), 120000)
        self.assertEqual(importe_atrasado_registrado(0, 1, 0, 100), 0)


@override_settings(ROOT_URLCONF='Tiendas.test_suscripciones')
class CarteraDiariaTests(TestCase):
    setUp = fixtures.SuscripcionesTests.setUp

    def venta_diaria(self, **extra):
        data = dict(tienda=self.ruta, cliente=self.cliente, fecha_venta=date(2026,10,1),
            valor_venta=100000, interes=20, cuotas=20, saldo_actual=120000, plazo='Diario')
        data.update(extra)
        return Venta.objects.create(**data)

    def registrar_abono(self, venta, monto, day=10, renovacion=False):
        Recaudo.objects.create(tienda=venta.tienda, venta=venta,
            fecha_recaudo=date(2026,10,day), valor_recaudo=monto, es_renovacion=renovacion)
        venta.saldo_actual -= Decimal(str(monto)); venta.save(update_fields=['saldo_actual'])

    def lista(self, venta):
        annotated = _anotar_ventas_lista(Venta.objects.filter(pk=venta.pk)).get()
        return VentaListaSerializer(annotated).data

    def assert_atraso(self, venta, expected):
        data = self.lista(venta)
        self.assertEqual(Decimal(str(data['dias_atrasados'])), Decimal(str(expected)))
        self.assertEqual(venta.dias_atrasados(), Decimal(str(expected)))
        return data

    def test_un_pago_o_dos_en_la_misma_fecha_son_equivalentes(self):
        uno = self.venta_diaria(); dos = self.venta_diaria()
        self.registrar_abono(uno,6000); self.registrar_abono(dos,2000); self.registrar_abono(dos,4000)
        a, b = self.assert_atraso(uno,0), self.assert_atraso(dos,0)
        self.assertEqual(a['riesgo_cartera'],b['riesgo_cartera'])
        self.assertEqual(a['importe_atrasado_estimado'],b['importe_atrasado_estimado'])

    def test_falla_y_pago_el_mismo_dia_no_exigen_dos_cuotas(self):
        venta = self.venta_diaria()
        self.registrar_abono(venta,0); self.registrar_abono(venta,6000)
        self.assert_atraso(venta,0)

    def test_abonos_parciales_conservan_fraccion(self):
        venta = self.venta_diaria()
        self.registrar_abono(venta,1000); self.registrar_abono(venta,2000)
        data = self.assert_atraso(venta,'.5')
        self.assertEqual(Decimal(str(data['importe_atrasado_estimado'])),3000)

    def test_doble_cuota_cubre_dos_jornadas(self):
        venta = self.venta_diaria()
        self.registrar_abono(venta,12000,day=7); self.registrar_abono(venta,0,day=8)
        self.assert_atraso(venta,0)
        self.registrar_abono(venta,0,day=9); self.assert_atraso(venta,1)

    def test_anticipo_se_consume_por_jornadas_no_por_cantidad_de_recibos(self):
        venta = self.venta_diaria()
        for amount in (5000,5000,8000): self.registrar_abono(venta,amount,day=7)
        for day in (8,9): self.registrar_abono(venta,0,day=day)
        self.assert_atraso(venta,0)
        self.registrar_abono(venta,0,day=10); self.assert_atraso(venta,1)

    def test_sin_visita_no_impone_calendario_nuevo_y_sigue_senal_de_seguimiento(self):
        venta = self.venta_diaria()
        self.registrar_abono(venta,6000,day=2)
        data = self.assert_atraso(venta,0)
        self.assertTrue(data['riesgo_cartera']['en_mora'])
        self.assertEqual(Decimal(str(data['importe_atrasado_estimado'])),0)

    def test_sin_primer_abono_conserva_inferencia_y_tope(self):
        venta = self.venta_diaria()
        data = self.lista(venta)
        self.assertEqual(Decimal(str(data['importe_atrasado_estimado'])),48000)
        self.assertEqual(data['riesgo_cartera']['cuotas_atrasadas'],8)

    def test_renovacion_no_crea_jornada(self):
        venta = self.venta_diaria()
        self.registrar_abono(venta,6000,day=7); self.registrar_abono(venta,1000,day=8,renovacion=True)
        self.assert_atraso(venta,0)

    def test_importe_directo_lista_detalle_y_modelo(self):
        venta = self.venta_diaria(valor_venta=300000, cuotas=20, saldo_actual=360000)
        self.registrar_abono(venta,100)
        data = self.assert_atraso(venta,'.99')
        self.assertEqual(Decimal(str(data['importe_atrasado_estimado'])),17900)
        self.assertEqual(Decimal(str(VentaDetailSerializer(venta).data['importe_atrasado_estimado'])),17900)
        self.assertEqual(venta.importe_atrasado_estimado(),17900)

    def test_monitor_y_resumen_cliente_comparten_jornadas(self):
        venta = self.venta_diaria()
        self.registrar_abono(venta,2000); self.registrar_abono(venta,1000)
        data = self.lista(venta)
        self.assertEqual(_perfil_riesgo(venta), data['riesgo_cartera'])
        self.assertEqual(Decimal(resumen_credito(venta)['cuotas_atrasadas']),Decimal('.5'))

    def test_promedio_por_recibo_no_cambia(self):
        venta = self.venta_diaria()
        self.registrar_abono(venta,2000); self.registrar_abono(venta,4000)
        self.assertEqual(self.lista(venta)['promedio_pago'],3000)
        self.assertEqual(venta.promedio_pago(),3000)

    def test_no_consulta_por_cada_fila(self):
        for i in range(8): self.registrar_abono(self.venta_diaria(),1000)
        ventas = list(_anotar_ventas_lista(Venta.objects.filter(tienda=self.ruta)))
        with self.assertNumQueries(0):
            self.assertEqual(len(VentaListaSerializer(ventas,many=True).data),9)

    def test_api_liquidar_expone_adelanto_diario_sin_atraso_negativo(self):
        venta = self.venta_diaria()
        self.registrar_abono(venta, 18000, day=7)
        self.api.force_authenticate(self.admin)
        before = list(Venta.objects.values()), list(Recaudo.objects.values())
        response = self.api.get(f'/ventas/activas/liquidar/2026-10-10/t/{self.ruta.pk}/?resumen=1')
        self.assertEqual(response.status_code, 200, response.data)
        credito = next(v for v in response.json()['creditos'] if v['id'] == venta.pk)
        self.assertIsNone(credito['calendario_pago'])
        self.assertEqual(credito['dias_atrasados'], 0)
        self.assertEqual(credito['cuotas_adelantadas'], 2)
        self.assertEqual((list(Venta.objects.values()), list(Recaudo.objects.values())), before)

    def test_adelanto_diario_suma_parciales_de_una_fecha_sin_contar_dos_ciclos(self):
        venta = self.venta_diaria()
        self.registrar_abono(venta, 6000, day=7)
        self.registrar_abono(venta, 9000, day=7)
        self.assertEqual(self.lista(venta)['cuotas_adelantadas'], Decimal('1.5'))
        self.registrar_abono(venta, 0, day=8)
        self.assertEqual(self.lista(venta)['cuotas_adelantadas'], Decimal('.5'))
        self.registrar_abono(venta, 0, day=9)
        data = self.lista(venta)
        self.assertEqual(data['cuotas_adelantadas'], 0)
        self.assertEqual(data['dias_atrasados'], Decimal('.5'))

    def test_adelanto_semanal_y_mensual_usan_el_anticipo_del_calendario(self):
        for plazo, cuotas, esperado in [('Semanal', 4, Decimal('1')), ('Mensual', 1, Decimal('.5'))]:
            venta = self.venta_diaria(plazo=plazo, cuotas=cuotas, calendario_version=1)
            self.registrar_abono(venta, 60000, day=7)
            self.assertEqual(self.lista(venta)['cuotas_adelantadas'], esperado)

    def test_creditos_cerrados_no_muestran_adelanto_y_anticipo_pequeno_no_se_pierde(self):
        venta = self.venta_diaria()
        self.registrar_abono(venta, 6001, day=7)
        self.assertGreater(self.lista(venta)['cuotas_adelantadas'], 0)
        self.assertLess(self.lista(venta)['cuotas_adelantadas'], Decimal('.01'))
        for estado in ('Pagado', 'Perdida'):
            venta.estado_venta = estado
            venta.save(update_fields=['estado_venta'])
            self.assertEqual(self.lista(venta)['cuotas_adelantadas'], 0)

    def test_permisos_y_get_sin_escrituras_financieras(self):
        self.api.force_authenticate(self.admin)
        before = list(Venta.objects.values()), list(Recaudo.objects.values())
        for path in (f'/ventas/activas/t/{self.ruta.pk}/',f'/tiendas/controles/inconsistencias/t/{self.ruta.pk}/'):
            self.assertEqual(self.api.get(path).status_code,200)
        self.assertEqual((list(Venta.objects.values()),list(Recaudo.objects.values())),before)
        self.api.force_authenticate(self.worker)
        self.assertEqual(self.api.get(f'/ventas/activas/t/{self.otra.pk}/').status_code,403)
        self.assertEqual(self.api.get(f'/tiendas/controles/inconsistencias/t/{self.ruta.pk}/').status_code,200)
        self.assertEqual(self.api.get(f'/tiendas/controles/inconsistencias/t/{self.otra.pk}/').status_code,403)

    def test_semanal_y_mensual_mantienen_calendario(self):
        for plazo in ('Semanal','Mensual'):
            venta = self.venta_diaria(plazo=plazo,cuotas=4 if plazo == 'Semanal' else 1,calendario_version=1)
            self.registrar_abono(venta,6000); self.registrar_abono(venta,6000)
            data = self.lista(venta)
            self.assertIsNotNone(data['calendario_pago'])
            self.assertIsNone(data['importe_atrasado_estimado'])
            self.assertEqual(Decimal(str(data['dias_atrasados'])), Decimal(data['calendario_pago']['cuotas_atrasadas']))
