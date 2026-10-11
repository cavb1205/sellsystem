"""Etapa 3: datos ficticios, SQLite y red externa prohibida."""
from datetime import date, timedelta
from decimal import Decimal
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from Tiendas.models import Tienda, Tienda_Membresia, PagoMembresia, SolicitudPago, CambioSuscripcion, Membresia
from Tiendas.root_metricas import periodos_mes, periodos_anio, comparar_ledger, cohortes_registro, resumen_root
from Tiendas import test_suscripciones as fixtures


@override_settings(ROOT_URLCONF='Tiendas.test_suscripciones')
class RootMetricasTests(TestCase):
    setUp = fixtures.SuscripcionesTests.setUp
    solicitud = fixtures.SuscripcionesTests.solicitud

    def crear_pago(self, fecha, monto, **extra):
        return PagoMembresia.objects.create(tienda=self.ruta, membresia=self.mensual,
            fecha=fecha, monto=monto, origen='manual', **extra)

    def test_mes_parcial_compara_mismos_dias_no_mes_completo(self):
        self.crear_pago(date(2026,9,8), 6000)
        self.crear_pago(date(2026,9,30), 90000)
        data = self.api.get('/tiendas/admin/resumen/').data
        self.assertEqual(data['ingresos_mes_anterior'], 96000)
        self.assertEqual(data['comparacion_mes']['anterior'], 6000)
        self.assertEqual(data['comparacion_mes']['actual'], 12000)
        self.assertEqual(data['comparacion_mes']['variacion_pct'], 100)
        self.assertEqual(data['comparacion_mes']['fin_anterior'], '2026-09-10')

    def test_mes_mas_corto_limita_ambos_periodos_no_total_del_mes(self):
        self.crear_pago(date(2026,3,28),1000); self.crear_pago(date(2026,3,31),4000)
        self.crear_pago(date(2026,2,28),500)
        data = resumen_root(date(2026,3,31))
        self.assertEqual(data['ingresos_mes'],5000)
        self.assertEqual(data['comparacion_mes']['actual'],1000)
        self.assertEqual(periodos_mes(date(2026,3,31)),(date(2026,3,1),date(2026,3,28),date(2026,2,1),date(2026,2,28)))

    def test_anio_actual_parcial_vs_parcial_excluye_diciembre_anterior(self):
        self.crear_pago(date(2025,10,9),6000); self.crear_pago(date(2025,12,30),90000)
        data = self.api.get('/tiendas/ingresos/?year=2026').data
        self.assertEqual(data['total_anio_anterior'],96000)
        self.assertEqual(data['comparacion_anual']['anterior'],6000)
        self.assertEqual(data['comparacion_anual']['variacion_pct'],100)

    def test_anio_terminado_se_compara_completo(self):
        self.assertEqual(periodos_anio(2025,date(2026,10,10)),
            (date(2025,1,1),date(2025,12,31),date(2024,1,1),date(2024,12,31)))
        self.crear_pago(date(2025,12,30),1000);self.crear_pago(date(2024,12,31),500)
        self.assertEqual(self.api.get('/tiendas/ingresos/?year=2025').data['comparacion_anual']['variacion_pct'],100)

    def test_bisiesto_y_enero_no_tienen_fechas_invalidas(self):
        self.assertEqual(periodos_anio(2024,date(2024,2,29)),
            (date(2024,1,1),date(2024,2,28),date(2023,1,1),date(2023,2,28)))
        self.assertEqual(periodos_mes(date(2026,1,10))[2],date(2025,12,1))

    def test_anios_invalidos_rechazados_y_no_escriben(self):
        for year in ('x','0','1900','2027','999999'):
            self.assertEqual(self.api.get('/tiendas/ingresos/?year='+year).status_code,400)
        self.assertEqual(PagoMembresia.objects.count(),1)

    def test_futuro_y_anulados_no_suman_pero_historial_anulado_visible(self):
        anulado=self.crear_pago(date(2026,10,9),99999,estado='anulado',anulado_motivo='Ficticio')
        self.crear_pago(date(2026,10,11),80000)
        data=self.api.get('/tiendas/ingresos/?year=2026').data
        self.assertEqual(data['total_anual'],12000);self.assertEqual(len(data['pagos']),1)
        self.assertEqual(data['pagos_anulados'][0]['id'],anulado.pk)
        self.assertEqual(data['pagos_anulados'][0]['motivo_anulacion'],'Ficticio')
        data=self.api.get('/tiendas/admin/resumen/').data
        self.assertEqual(data['ingresos_mes'],12000);self.assertEqual(data['ingresos_6m'][-1]['total'],12000)

    def test_sin_base_positiva_no_inventa_cero_ni_infinito(self):
        self.assertIsNone(comparar_ledger(periodos_mes(date(2026,10,10)))['variacion_pct'])

    def test_proyeccion_precio_actual_no_reescribe_ledger(self):
        self.tm.membresia=self.anual;self.tm.save()
        Tienda_Membresia.objects.filter(tienda=self.otra).update(estado='Activa')
        self.assertEqual(resumen_root(date(2026,10,10))['mrr_estimado'],22000)
        self.mensual.precio=Decimal(15000);self.mensual.save()
        data=resumen_root(date(2026,10,10))
        self.assertEqual(data['mrr_estimado'],25000);self.assertEqual(data['ingresos_mes'],12000)
        Tienda_Membresia.objects.filter(tienda=self.otra).update(archivada=True)
        self.assertEqual(resumen_root(date(2026,10,10))['mrr_estimado'],10000)

    def test_cohorte_observa_30_dias_sin_suponer_prueba(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(fecha_registro=date(2026,9,1))
        Tienda.objects.filter(pk=self.otra.pk).update(fecha_registro=date(2026,10,1))
        self.crear_pago(date(2026,9,30),12000)
        grupos=cohortes_registro(date(2026,10,10))
        septiembre,octubre=grupos[-2:]
        self.assertEqual((septiembre['evaluadas'],septiembre['con_pago_30d'],septiembre['conversion_pct']),(1,1,100))
        self.assertEqual((octubre['evaluadas'],octubre['en_observacion'],octubre['conversion_pct']),(0,1,None))

    def test_cohorte_no_cuenta_dia30_anulado_cero_o_anterior_al_registro(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(fecha_registro=date(2026,9,1))
        self.crear_pago(date(2026,8,31),12000); self.crear_pago(date(2026,9,2),0)
        self.crear_pago(date(2026,9,3),12000,estado='anulado'); self.crear_pago(date(2026,10,1),12000)
        self.assertEqual(cohortes_registro(date(2026,10,10))[-2]['con_pago_30d'],0)

    def test_cohorte_incluye_archivadas_y_ignora_plan_prueba(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(fecha_registro=date(2026,9,1))
        self.tm.archivada=True;self.tm.save()
        prueba=Membresia.objects.create(nombre='Prueba',precio=0)
        PagoMembresia.objects.create(tienda=self.ruta,membresia=prueba,fecha=date(2026,9,2),monto=100)
        self.assertEqual(cohortes_registro(date(2026,10,10))[-2]['con_pago_30d'],0)
        self.crear_pago(date(2026,9,3),100)
        self.assertEqual(cohortes_registro(date(2026,10,10))[-2]['con_pago_30d'],1)

    def test_suspensiones_solo_eventos_reales_no_expiraciones_inferidas(self):
        self.tm.estado='Vencida';self.tm.fecha_vencimiento=date(2026,10,1);self.tm.save()
        self.assertEqual(resumen_root(date(2026,10,10))['retencion']['suspensiones_manuales_mes'],0)
        for version in (1,2):
            from django.utils import timezone
            CambioSuscripcion.objects.create(tienda=self.ruta,tienda_nombre='Ruta A',version=version,accion='vencimiento_manual',fecha=timezone.now())
        self.tm.estado='Activa';self.tm.save()
        data=resumen_root(date(2026,10,10))['retencion']
        self.assertEqual(data['suspensiones_manuales_mes'],2);self.assertEqual(data['rutas_suspendidas_mes'],1)

    def test_nombre_administrador_usa_usuario_si_nombre_vacio(self):
        self.solicitud()
        data=self.api.get('/tiendas/list/?vista=lista').data
        self.assertEqual(data[0]['tienda']['administrador'],'admin-pruebas')
        self.assertEqual(data[0]['tienda']['administrador_usuario'],'admin-pruebas')
        data=self.api.get('/tiendas/solicitudes/revision/').data
        self.assertEqual(data['pendientes'][0]['administrador_usuario'],'admin-pruebas')
        self.assertEqual(self.api.get('/tiendas/ingresos/?year=2026').data['pagos'][0]['administrador_id'],self.admin.pk)

    def test_indicadores_privados_solo_root(self):
        for user in (self.admin,self.worker,None):
            self.api.force_authenticate(user)
            for endpoint in ('/tiendas/admin/resumen/','/tiendas/ingresos/','/tiendas/solicitudes/revision/'):
                self.assertIn(self.api.get(endpoint).status_code,(401,403))

    def medir(self,endpoint):
        with CaptureQueriesContext(connection) as queries:
            r=self.api.get(endpoint);self.assertEqual(r.status_code,200,r.data)
        return len(queries)

    def test_consultas_no_crecen_por_cada_ruta_sana(self):
        endpoints=('/tiendas/admin/resumen/','/tiendas/list/?vista=lista')
        antes=[self.medir(e) for e in endpoints]
        for i in range(20):
            ruta=Tienda.objects.create(nombre='Extra '+str(i),administrador=self.admin)
            Tienda_Membresia.objects.create(tienda=ruta,membresia=self.mensual,estado='Activa',
                fecha_activacion=date(2026,10,1),fecha_vencimiento=date(2026,12,1))
        despues=[self.medir(e) for e in endpoints]
        self.assertEqual(antes,despues,(antes,despues))
        self.assertLessEqual(despues[0],13);self.assertLessEqual(despues[1],4)

    def test_consultas_ingresos_y_revision_no_crecen_por_cada_solicitud(self):
        self.solicitud()
        endpoints=('/tiendas/ingresos/?year=2026','/tiendas/solicitudes/revision/')
        antes=[self.medir(e) for e in endpoints]
        for i in range(20):
            s=SolicitudPago.objects.create(tienda=self.ruta,membresia=self.mensual,codigo='MM-X'+str(i),
                estado='pendiente_confirmacion',expira=self.solicitud_expira())
            self.crear_pago(date(2026,10,10),1000,solicitud=s)
        despues=[self.medir(e) for e in endpoints]
        self.assertEqual(antes,despues,(antes,despues))
        self.assertLessEqual(despues[0],4);self.assertLessEqual(despues[1],6)

    def solicitud_expira(self):
        from django.utils import timezone
        return timezone.now()+timedelta(days=2)

    def test_lectura_sana_no_muta_caja_cartera_ledger(self):
        antes=list(PagoMembresia.objects.values())
        for endpoint in ('/tiendas/admin/resumen/','/tiendas/ingresos/','/tiendas/list/?vista=lista'):
            self.assertEqual(self.api.get(endpoint).status_code,200)
        self.ruta.refresh_from_db();self.venta.refresh_from_db()
        self.assertEqual(self.ruta.caja_inicial,10000);self.assertEqual(self.venta.saldo_actual,1100)
        self.assertEqual(antes,list(PagoMembresia.objects.values()));self.assertFalse(CambioSuscripcion.objects.exists())
