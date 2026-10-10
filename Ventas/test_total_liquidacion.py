from datetime import date
from decimal import Decimal
from django.test import SimpleTestCase, TestCase, override_settings
from Ventas.calendario import importe_cuota_fecha
from Ventas.test_calendario import CalendarioAPITests
from Ventas.views import list_ventas_a_liquidar
from Recaudos.models import Recaudo
from Tiendas.models import Tienda
from Clientes.models import Cliente
from Tiendas.test_suscripciones import SuscripcionesTests


class CuotaFechaTests(SimpleTestCase):
    def importe(self, **extra):
        datos = dict(fecha_venta=date(2026, 10, 8), cuotas=4, plazo='Semanal',
                     total='1200000', saldo_inicio='1200000',
                     consulta=date(2026, 10, 15), version=1)
        datos.update(extra)
        return importe_cuota_fecha(**datos)

    def test_cuota_semanal_no_acumula_atrasos(self):
        self.assertEqual(self.importe(consulta=date(2026, 10, 22)), Decimal('300000'))

    def test_dia_sin_vencimiento_no_suma_atrasos(self):
        self.assertEqual(self.importe(consulta=date(2026, 10, 16)), 0)

    def test_anticipo_anterior_completo_y_parcial(self):
        self.assertEqual(self.importe(saldo_inicio='600000'), 0)
        self.assertEqual(self.importe(saldo_inicio='1100000'), Decimal('200000'))

    def test_mensual_calendario_y_legacy(self):
        datos = dict(fecha_venta=date(2026, 1, 31), cuotas=1, plazo='Mensual')
        self.assertEqual(self.importe(**datos, consulta=date(2026, 2, 28)), Decimal('1200000'))
        self.assertEqual(self.importe(**datos, consulta=date(2026, 2, 28), version=0), 0)
        self.assertEqual(self.importe(**datos, consulta=date(2026, 3, 2), version=0), Decimal('1200000'))

    def test_diario_saldo_final_menor_que_cuota(self):
        self.assertEqual(self.importe(plazo='Diario', cuotas=20), Decimal('60000'))
        self.assertEqual(self.importe(plazo='Diario', cuotas=20, saldo_inicio='10000'), Decimal('10000'))

    def test_cero_saldo_y_fecha_creacion(self):
        self.assertEqual(self.importe(saldo_inicio=0), 0)
        self.assertEqual(self.importe(consulta=date(2026, 10, 8)), 0)

    def test_centavos_repartidos_no_suman_mas_que_el_contrato(self):
        importes = [self.importe(total='100.01', saldo_inicio='100.01', cuotas=3,
                                consulta=date(2026, 10, d)) for d in (15, 22, 29)]
        self.assertEqual(sum(importes), Decimal('100.01'))


class TotalLiquidacionAPITests(TestCase):
    setUp = CalendarioAPITests.setUp
    crear = CalendarioAPITests.crear
    request = CalendarioAPITests.request
    abonar = CalendarioAPITests.abonar
    falla = CalendarioAPITests.falla

    def resumen(self, fecha='2026-10-15', **extra):
        response = self.request('get', list_ventas_a_liquidar,
            {'resumen': '1', **extra}, date=fecha, tienda_id=str(self.ruta.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resumen_cobranza']['criterio'], 'cuotas_del_dia')
        self.assertEqual(response.data['resumen_cobranza']['fecha_consulta'], fecha)
        return response.data

    def total(self, **extra):
        return Decimal(self.resumen(**extra)['resumen_cobranza']['total_a_cobrar'])

    def test_pago_parcial_doble_y_cancelacion_no_cambian_base(self):
        venta = self.crear()
        self.assertEqual(self.total(), 300000)
        for pago in (100000, 500000, 600000):
            self.assertEqual(self.abonar(venta, pago).status_code, 200)
            self.assertEqual(self.total(), 300000)
        self.assertEqual(self.resumen()['creditos'], [])
        self.assertEqual(self.total(fecha='2026-10-22', vista='previa'), 0)

    def test_falla_no_cambia_base(self):
        venta = self.crear()
        self.assertEqual(self.falla(venta).status_code, 200)
        self.assertEqual(self.total(), 300000)

    def test_pagado_antes_de_hoy_no_se_suma(self):
        venta = self.crear(saldo_actual=0, estado_venta='Pagado')
        Recaudo.objects.create(venta=venta, tienda=self.ruta, fecha_recaudo=date(2026, 10, 14), valor_recaudo=1200000)
        self.assertEqual(self.total(), 0)

    def test_renovacion_hoy_no_borra_cuota_y_ayer_si(self):
        venta = self.crear(saldo_actual=0, estado_venta='Pagado')
        cierre = Recaudo.objects.create(venta=venta, tienda=self.ruta,
            fecha_recaudo=date(2026, 10, 15), valor_recaudo=1200000, es_renovacion=True)
        self.assertEqual(self.total(), 300000)
        cierre.fecha_recaudo = date(2026, 10, 14)
        cierre.save(update_fields=['fecha_recaudo'])
        self.assertEqual(self.total(), 0)

    def test_abono_posterior_no_reescribe_base_fecha_anterior(self):
        venta = self.crear()
        self.assertEqual(self.abonar(venta, 100000).status_code, 200)
        self.assertEqual(self.total(fecha='2026-10-14'), 0)
        self.assertEqual(self.total(), 300000)

    def test_abono_posterior_conserva_base_historica_positiva(self):
        venta = self.crear(fecha_venta=date(2026, 10, 1))
        self.assertEqual(self.total(fecha='2026-10-08'), 300000)
        self.assertEqual(self.abonar(venta, 1200000).status_code, 200)
        self.assertEqual(self.total(fecha='2026-10-08'), 300000)

    def test_diario_pagado_hoy_permanece_en_base(self):
        venta = self.crear(plazo='Diario', cuotas=20, calendario_version=0)
        self.assertEqual(self.total(), 60000)
        self.assertEqual(self.abonar(venta, 1200000).status_code, 200)
        self.assertEqual(self.total(), 60000)

    def test_anticipo_voluntario_no_agrega_meta(self):
        venta = self.crear(saldo_actual=600000)
        Recaudo.objects.create(venta=venta, tienda=self.ruta, fecha_recaudo=date(2026, 10, 14), valor_recaudo=600000)
        self.assertEqual(self.total(), 0)
        self.assertEqual(self.abonar(venta, 100000).status_code, 200)
        self.assertEqual(self.total(), 0)

    def test_creditos_ajenos_perdidas_y_nuevos_no_se_suman(self):
        otra = Tienda.objects.create(nombre='Ruta ajena', administrador=self.other)
        cliente = Cliente.objects.create(tienda=otra, identificacion='ajeno', nombres='Ajeno', apellidos='Prueba')
        self.crear(tienda=otra, cliente=cliente)
        self.crear(tienda=self.ruta, estado_venta='Perdida')
        self.crear(fecha_venta=date(2026, 10, 15))
        self.assertEqual(self.total(), 0)

    def test_atrasos_fuera_de_fecha_no_se_suman_a_cuotas(self):
        self.crear()
        self.assertEqual(self.total(fecha='2026-10-16', vista='previa'), 0)
        datos = self.resumen(fecha='2026-10-22', vista='previa')
        self.assertEqual(Decimal(datos['resumen_cobranza']['total_a_cobrar']), 300000)
        self.assertEqual(Decimal(datos['creditos'][0]['calendario_pago']['importe_pendiente_hoy']), 600000)

    def test_contrato_anterior_compatible_y_get_no_persiste(self):
        venta = self.crear()
        anterior = dict(venta.__dict__)
        compat = self.request('get', list_ventas_a_liquidar, date='2026-10-15', tienda_id=str(self.ruta.pk))
        self.assertIsInstance(compat.data, list)
        self.resumen()
        venta.refresh_from_db()
        for campo in ('saldo_actual', 'estado_venta', 'fecha_vencimiento', 'calendario_version'):
            self.assertEqual(getattr(venta, campo), anterior[campo])
        self.assertEqual(Recaudo.objects.count(), 0)

    def test_otro_administrador_no_puede_ver_resumen(self):
        response = self.request('get', list_ventas_a_liquidar, {'resumen': '1'},
            date='2026-10-15', tienda_id=str(self.ruta.pk), user=self.other)
        self.assertEqual(response.status_code, 403)

    def test_resumen_no_habilita_futuro_sin_previa(self):
        response = self.request('get', list_ventas_a_liquidar, {'resumen': '1'},
            date='2026-10-16', tienda_id=str(self.ruta.pk))
        self.assertEqual(response.status_code, 400)


@override_settings(ROOT_URLCONF='Tiendas.test_suscripciones')
class TotalLiquidacionPermisosTests(TestCase):
    setUp = SuscripcionesTests.setUp

    def test_trabajador_accede_solo_a_su_ruta_y_no_previa(self):
        self.api.force_authenticate(self.worker)
        response = self.api.get(f'/ventas/activas/liquidar/2026-10-10/t/{self.ruta.pk}/?resumen=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['resumen_cobranza']['total_a_cobrar'], '60.00')
        ajena = self.api.get(f'/ventas/activas/liquidar/2026-10-10/t/{self.otra.pk}/?resumen=1')
        self.assertEqual(ajena.status_code, 403)
        previa = self.api.get(f'/ventas/activas/liquidar/2026-10-11/t/{self.ruta.pk}/?resumen=1&vista=previa')
        self.assertEqual(previa.status_code, 403)

    def test_resumen_no_omite_bloqueo_de_suscripcion_manual(self):
        self.tm.vencida_manualmente = True
        self.tm.save(update_fields=['vencida_manualmente'])
        for user in (self.admin, self.worker):
            self.api.force_authenticate(user)
            response = self.api.get(f'/ventas/activas/liquidar/2026-10-10/t/{self.ruta.pk}/?resumen=1')
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.data['code'], 'membresia_vencida_manual')
