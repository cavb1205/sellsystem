"""Reportes: datos ficticios, SQLite y ningún envío externo."""
from datetime import date, datetime, timezone as utc
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from Tiendas.test_suscripciones import SuscripcionesTests
from Tiendas.reporte_rutas import cumplimiento_dia, partes_resumen, longitud_visible, dinero
from Tiendas.alertas_operativas import resumen_operativo
from Ventas.models import Venta
from Ventas.cobranza_dia import cuotas_del_dia
from Recaudos.models import Recaudo


class CifrasReporteTests(TestCase):
    setUp = SuscripcionesTests.setUp

    def crear(self, **extra):
        datos = dict(tienda=self.ruta, cliente=self.cliente, fecha_venta=date(2026,10,1),
            valor_venta=1000, interes=20, cuotas=20, saldo_actual=1200)
        datos.update(extra)
        return Venta.objects.create(**datos)

    def abono(self, venta, importe, fecha=date(2026,10,9), **extra):
        return Recaudo.objects.create(venta=venta, tienda=venta.tienda,
            valor_recaudo=importe, fecha_recaudo=fecha, **extra)

    def reporte(self, fecha=date(2026,10,9)):
        return cumplimiento_dia(self.ruta, fecha)

    def test_credito_pagado_en_jornada_conserva_cuota(self):
        venta = self.crear(saldo_actual=0, estado_venta='Pagado')
        self.abono(venta, 1200)
        r = self.reporte()
        self.assertEqual(r['esperado'], 120)  # Incluye crédito del fixture.
        self.assertEqual(r['recaudo'], 1200)
        self.assertEqual(r['cobertura_recaudo'], 60)
        self.assertEqual(r['pendiente_recaudo'], 60)
        self.assertEqual(r['cobros_adicionales'], 1140)

    def test_varios_recibos_y_visita_cuentan_un_credito(self):
        self.abono(self.venta, 0)
        self.abono(self.venta, 30)
        self.abono(self.venta, 30)
        r = self.reporte()
        self.assertEqual(r['programadas'], 1)
        self.assertEqual(r['con_abono'], 1)
        self.assertEqual(r['sin_abono'], 0)
        self.assertEqual(r['sin_gestion'], 0)
        self.assertEqual(r['cobertura_recaudo'], 60)

    def test_doble_pago_no_cubre_otro_credito(self):
        self.crear()
        self.abono(self.venta, 200)
        r = self.reporte()
        self.assertEqual(r['esperado'], 120)
        self.assertEqual(r['cobertura_recaudo'], 60)
        self.assertEqual(r['pendiente_recaudo'], 60)
        self.assertEqual(r['sin_gestion'], 1)

    def test_cuota_final_y_pago_posterior_no_cambian_base(self):
        self.venta.saldo_actual = 0; self.venta.estado_venta = 'Pagado'; self.venta.save()
        self.abono(self.venta, 25)
        self.abono(self.venta, 10, fecha=date(2026,10,10))
        r = self.reporte()
        self.assertEqual(r['esperado'], 35)
        self.assertEqual(r['cobertura_recaudo'], 25)
        self.assertEqual(r['pendiente_recaudo'], 10)

    def test_renovacion_no_es_efectivo_y_pagado_antes_no_se_suma(self):
        self.venta.saldo_actual = 0; self.venta.estado_venta = 'Pagado'; self.venta.save()
        self.abono(self.venta, 1100, es_renovacion=True)
        r = self.reporte()
        self.assertEqual(r['recaudo'], 0)
        self.assertEqual(r['esperado'], 60)
        self.assertEqual(r['sin_gestion'], 1)
        Recaudo.objects.filter(venta=self.venta, fecha_recaudo=date(2026,10,9)).update(fecha_recaudo=date(2026,10,8))
        self.assertEqual(self.reporte()['esperado'], 0)

    def test_semanal_anticipo_y_dia_sin_vencimiento(self):
        self.venta.plazo='Semanal'; self.venta.cuotas=4; self.venta.calendario_version=1
        self.venta.saldo_actual=600; self.venta.save()
        self.abono(self.venta, 500, fecha=date(2026,10,7))
        self.abono(self.venta, 50, fecha=date(2026,10,8))
        self.assertEqual(self.reporte(date(2026,10,8))['esperado'], 0)
        r = self.reporte(date(2026,10,9))
        self.assertEqual(r['esperado'], 0)
        self.assertEqual(r['programadas'], 0)

    def test_mensual_calendario_y_legacy(self):
        self.venta.fecha_venta=date(2026,1,31); self.venta.plazo='Mensual'; self.venta.cuotas=1
        self.venta.calendario_version=1; self.venta.save()
        self.assertEqual(self.reporte(date(2026,2,28))['esperado'], 1200)
        self.venta.calendario_version=0; self.venta.save()
        self.assertEqual(self.reporte(date(2026,2,28))['esperado'], 0)
        self.assertEqual(self.reporte(date(2026,3,2))['esperado'], 1200)

    def test_importe_igual_a_base_liquidar_y_finanzas_intactas(self):
        before = list(Venta.objects.values())
        r = self.reporte()
        self.assertEqual(r['esperado'], sum((m for _,m in cuotas_del_dia(self.ruta,date(2026,10,9))),Decimal(0)))
        self.assertEqual(r['esperado'], r['cobertura_recaudo'] + r['pendiente_recaudo'])
        self.assertEqual(list(Venta.objects.values()), before)
        self.ruta.refresh_from_db(); self.assertEqual(self.ruta.caja_inicial,10000)

    def test_incluye_ruta_vacia_sin_mezclar_otro_alcance(self):
        from Tiendas.models import Tienda
        r = resumen_operativo(date(2026,10,9), rutas=Tienda.objects.filter(pk=self.otra.pk))
        self.assertEqual(len(r),1)
        self.assertEqual(r[0]['programadas'],0)
        self.assertNotIn(self.ruta.pk,[x['id'] for x in r])


class FormatoReporteTests(SimpleTestCase):
    def fila(self, numero=1, **extra):
        r = dict(id=numero,nombre=f'Ruta <{numero}> & 🧭',esperado=120,recaudo=100,
            cobertura_recaudo=60,pendiente_recaudo=60,cobros_adicionales=40,
            programadas=2,con_abono=1,sin_abono=0,sin_gestion=1,cierre=False,
            alertas_criticas=0,alertas_urgentes=0)
        r.update(extra); return r

    def test_todas_las_rutas_partes_validas_y_prioridad(self):
        filas = [self.fila(n) for n in range(1,81)]
        filas[-1]['alertas_criticas']=3
        partes = partes_resumen(filas,date(2026,10,9),datetime(2026,10,10,11,tzinfo=utc.utc))
        self.assertEqual(partes[0]['rutas'][0],80)
        self.assertEqual(sorted(n for p in partes for n in p['rutas']),list(range(1,81)))
        self.assertTrue(all(longitud_visible(p['texto'])<=3500 for p in partes))
        self.assertTrue(all(p['texto'].count('<b>')==p['texto'].count('</b>') for p in partes))
        self.assertIn('&lt;80&gt; &amp;',partes[0]['texto'])
        self.assertNotIn('Umbrales nuevos',partes[0]['texto'])

    def test_sin_cuotas_no_oculta_abonos_y_nombres_largos(self):
        fila=self.fila(nombre='🧭<&>'*500,esperado=0,programadas=0,cobertura_recaudo=0)
        texto=partes_resumen([fila],date(2026,10,9))[0]['texto']
        self.assertIn('Total recaudado: <b>$100</b>',texto)
        self.assertIn('sin cuotas programadas',texto)
        self.assertLess(longitud_visible(texto),3500)

    def test_importes_exactos_y_vacio(self):
        self.assertEqual(dinero('863.20'),'$863,20')
        self.assertEqual(dinero('1529700'),'$1.529.700')
        self.assertIn('No hay rutas autorizadas',partes_resumen([],date(2026,10,9))[0]['texto'])
