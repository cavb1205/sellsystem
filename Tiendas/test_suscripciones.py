"""Suscripciones administrativas con SQLite desechable y red prohibida."""
from datetime import date, datetime, timedelta, timezone as utc
from decimal import Decimal
from unittest.mock import patch
import tempfile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient
from rest_framework.test import APIRequestFactory
from Clientes.models import Cliente
from Recaudos.models import Recaudo
from Tiendas.models import (Tienda, Tienda_Membresia, Tienda_Administrador, Membresia,
    PagoMembresia, CambioSuscripcion, SolicitudPago, MovimientoCaja, CuentaDestino)
from Tiendas.serializers import TiendaMembresiaListaSerializer
from Tiendas.views import (_confirmar_solicitud, _revertir_solicitud,
    _actualizar_estados_membresias, comprobar_estado_membresia)
from Trabajadores.models import Perfil, AsignacionTrabajador
from Trabajadores.views import Login
from Ventas.models import Venta

urlpatterns = [path(prefix+'/', include(prefix.capitalize()+'.urls'))
    for prefix in ('tiendas', 'ventas', 'clientes', 'recaudos', 'gastos', 'aportes', 'utilidades', 'publicidad', 'trabajadores')]


@override_settings(ROOT_URLCONF=__name__)
class SuscripcionesTests(TestCase):
    def setUp(self):
        self.red = patch('requests.sessions.Session.request', side_effect=AssertionError('Red externa prohibida'))
        self.red.start(); self.addCleanup(self.red.stop)
        self.reloj = patch('Tiendas.fecha_operativa.timezone.now', return_value=datetime(2026,10,10,15,tzinfo=utc.utc))
        self.reloj.start(); self.addCleanup(self.reloj.stop)
        self.root = User.objects.create_user('root-pruebas', is_staff=True, is_superuser=True)
        self.admin = User.objects.create_user('admin-pruebas', is_staff=True)
        self.worker = User.objects.create_user('worker-pruebas')
        self.ruta = Tienda.objects.create(id=901, nombre='Ruta A', administrador=self.admin, caja_inicial=10000)
        self.otra = Tienda.objects.create(id=902, nombre='Ruta B', administrador=self.admin, caja_inicial=9000)
        Perfil.objects.create(trabajador=self.worker, tienda=self.ruta, identificacion='001', telefono='000')
        Tienda_Administrador.objects.create(tienda=self.ruta, administrador=self.admin)
        Tienda_Administrador.objects.create(tienda=self.otra, administrador=self.admin)
        self.mensual = Membresia.objects.create(nombre='Mensual', precio=12000)
        self.anual = Membresia.objects.create(nombre='Anual', precio=120000)
        CuentaDestino.objects.update_or_create(pk=1, defaults={'banco':'Banco Ficticio', 'numero':'000-PRUEBA', 'titular':'Titular Ficticio', 'tipo':'Cuenta de prueba'})
        self.tm = Tienda_Membresia.objects.create(tienda=self.ruta, membresia=self.mensual,
            estado='Activa', fecha_activacion=date(2026,10,1), fecha_vencimiento=date(2026,12,1))
        Tienda_Membresia.objects.create(tienda=self.otra, membresia=self.mensual,
            fecha_activacion=date(2026,10,1), fecha_vencimiento=date(2026,12,1))
        self.cliente = Cliente.objects.create(tienda=self.ruta, identificacion='c1', nombres='Cliente',
            apellidos='Prueba', telefono_principal='000', direccion='Dirección de prueba')
        self.venta = Venta.objects.create(tienda=self.ruta, cliente=self.cliente,
            fecha_venta=date(2026,10,1), valor_venta=1000, interes=20, cuotas=20, saldo_actual=1100)
        self.recaudo = Recaudo.objects.create(tienda=self.ruta, venta=self.venta,
            fecha_recaudo=date(2026,10,2), valor_recaudo=100)
        self.pago = PagoMembresia.objects.create(tienda=self.ruta, membresia=self.mensual,
            fecha=date(2026,10,10), origen='manual', monto=12000, registrado_por=self.root)
        self.api = APIClient(); self.api.force_authenticate(self.root)

    def vencer(self, **datos):
        cuerpo = {'motivo': 'Activación de ruta equivocada', 'version': self.tm.version, **datos}
        return self.api.post(f'/tiendas/{self.ruta.pk}/admin/vencer/', cuerpo, format='json')

    def activar(self, version=None, anual=False, legacy=False):
        return self.api.post(f'/tiendas/activate/{"year" if anual else "mounth"}/{self.tm.pk}/',
            {} if legacy else {'version': self.tm.version if version is None else version}, format='json')

    def solicitud(self, estado='pendiente_confirmacion'):
        return SolicitudPago.objects.create(tienda=self.ruta, membresia=self.mensual,
            codigo='MM-PRUEBA', estado=estado, expira=datetime(2026,10,12,tzinfo=utc.utc))

    def test_vencer_conserva_cartera_caja_ingreso_y_fecha_previa_auditada(self):
        res = self.vencer(); self.assertEqual(res.status_code,200,res.data)
        self.tm.refresh_from_db(); self.ruta.refresh_from_db(); self.venta.refresh_from_db()
        self.assertTrue(self.tm.vencida_manualmente); self.assertEqual(self.tm.estado,'Vencida')
        self.assertEqual(self.tm.fecha_vencimiento,date(2026,10,10)); self.assertEqual(self.tm.version,1)
        self.assertEqual(self.ruta.caja_inicial,10000); self.assertEqual(self.venta.saldo_actual,1100)
        self.assertEqual(Cliente.objects.count(),1); self.assertEqual(Recaudo.objects.count(),1)
        self.assertFalse(MovimientoCaja.objects.exists()); self.assertEqual(PagoMembresia.objects.count(),1)
        cambio = CambioSuscripcion.objects.get(); self.assertEqual(cambio.usuario,self.root)
        self.assertEqual(cambio.anterior['fecha_vencimiento'],'2026-12-01')
        self.assertTrue(cambio.nuevo['vencida_manualmente']); self.assertIsNone(cambio.pago_id)
        self.pago.refresh_from_db(); self.assertEqual(self.pago.estado,'vigente')

    def test_anular_conserva_identidad_y_excluye_reportes(self):
        self.assertEqual(self.vencer(pago_id=self.pago.pk).status_code,200)
        self.pago.refresh_from_db(); self.assertEqual(self.pago.estado,'anulado')
        self.assertEqual(self.pago.monto,12000); self.assertEqual(self.pago.anulado_por,self.root)
        res = self.api.get(f'/tiendas/{self.ruta.pk}/admin/detalle/')
        self.assertEqual(res.status_code,200,res.data); self.assertEqual(res.data['total_pagado'],0)
        self.assertEqual(res.data['pagos'][0]['estado'],'anulado')
        self.assertEqual(res.data['pagos'][0]['id'],self.pago.pk)
        self.assertEqual(res.data['historial_suscripcion'][0]['pago_id'],self.pago.pk)
        informe = self.api.get('/tiendas/ingresos/?year=2026')
        self.assertEqual(informe.status_code,200); self.assertEqual(informe.data['total_anual'],0)
        resumen = self.api.get('/tiendas/admin/resumen/')
        self.assertEqual(resumen.status_code,200,resumen.data)
        self.assertEqual(resumen.data['ingresos_mes'],0)
        self.tm.refresh_from_db()
        self.assertEqual(TiendaMembresiaListaSerializer(self.tm).data['version'],1)

    def test_no_puede_anular_pagos_ajenos_no_manuales_ni_anulados(self):
        otro = PagoMembresia.objects.create(tienda=self.otra,membresia=self.mensual,
            fecha=date(2026,10,10),origen='manual',monto=12000)
        for campos in ({'origen':'panel'}, {'estado':'anulado'}, {'solicitud':self.solicitud()}):
            pago = PagoMembresia.objects.create(tienda=self.ruta,membresia=self.mensual,
                fecha=date(2026,10,10),monto=12000,**campos)
            self.assertEqual(self.vencer(pago_id=pago.pk,confirmar_solicitudes_pendientes=True).status_code,400)
        self.assertEqual(self.vencer(pago_id=otro.pk,confirmar_solicitudes_pendientes=True).status_code,400)
        self.tm.refresh_from_db(); self.assertFalse(self.tm.vencida_manualmente)
        self.assertFalse(CambioSuscripcion.objects.exists())

    def test_permisos_metodo_y_datos_invalidos_no_escriben(self):
        for user in (self.admin,self.worker,None):
            self.api.force_authenticate(user); self.assertIn(self.vencer().status_code,(401,403))
        self.api.force_authenticate(self.root)
        for datos in ({'motivo':''},{'motivo':'abc'},{'motivo':'x'*501},{'motivo':1},
            {'version':'0'},{'version':True},{'version':-1},{'pago_id':True},{'pago_id':'1'},
            {'confirmar_solicitudes_pendientes':'true'}):
            self.assertEqual(self.vencer(**datos).status_code,400,datos)
        self.assertEqual(self.api.get(f'/tiendas/{self.ruta.pk}/admin/vencer/').status_code,405)
        self.assertEqual(self.api.post('/tiendas/99999/admin/vencer/',
            {'motivo':'Prueba válida','version':0},format='json').status_code,404)
        self.assertFalse(CambioSuscripcion.objects.exists())

    def test_conflicto_version_no_anula_ni_vence(self):
        self.assertEqual(self.activar().status_code,200)
        self.assertEqual(self.vencer(pago_id=self.pago.pk).status_code,409)
        self.tm.refresh_from_db(); self.assertFalse(self.tm.vencida_manualmente)
        self.pago.refresh_from_db(); self.assertEqual(self.pago.estado,'vigente')
        self.assertEqual(CambioSuscripcion.objects.count(),1)

    def test_solicitudes_pendientes_requieren_confirmacion_y_se_conservan(self):
        s = self.solicitud()
        self.assertEqual(self.vencer().status_code,409)
        res = self.api.get(f'/tiendas/{self.ruta.pk}/admin/detalle/')
        self.assertEqual(res.data['solicitudes_pendientes'][0]['codigo'],s.codigo)
        self.assertEqual(self.vencer(confirmar_solicitudes_pendientes=True).status_code,200)
        s.refresh_from_db(); self.assertEqual(s.estado,'pendiente_confirmacion')

    def test_reintento_no_duplica_correccion_y_activar_no_duplica(self):
        self.assertEqual(self.vencer(pago_id=self.pago.pk).status_code,200)
        self.assertEqual(self.vencer(pago_id=self.pago.pk).status_code,409)
        self.tm.refresh_from_db()
        self.assertEqual(self.vencer().status_code,200)
        self.assertEqual(CambioSuscripcion.objects.count(),1)
        version = self.tm.version
        self.assertEqual(self.activar(version).status_code,200)
        self.assertEqual(self.activar(version).status_code,409)
        self.assertEqual(PagoMembresia.objects.filter(estado='vigente').count(),1)
        self.tm.refresh_from_db(); self.assertEqual(self.tm.fecha_vencimiento,date(2026,11,9))
        self.assertFalse(self.tm.vencida_manualmente); self.assertEqual(self.tm.motivo,'')
        self.assertEqual(self.activar(legacy=True).status_code,200)
        self.tm.refresh_from_db(); self.assertEqual(self.tm.fecha_vencimiento,date(2026,11,9))

    def test_renovacion_anual_desde_hoy_y_mensual_vigente_desde_vencimiento(self):
        self.assertEqual(self.activar().status_code,200)
        self.tm.refresh_from_db(); self.assertEqual(self.tm.fecha_vencimiento,date(2026,12,31))
        self.assertEqual(self.vencer().status_code,200)
        self.tm.refresh_from_db()
        self.assertEqual(self.activar(anual=True).status_code,200)
        self.tm.refresh_from_db(); self.assertEqual(self.tm.fecha_vencimiento,date(2027,10,10))

    def test_precio_congelado_tras_cambio_de_plan(self):
        self.mensual.precio=15000; self.mensual.save()
        self.assertEqual(self.vencer(pago_id=self.pago.pk).status_code,200)
        self.pago.refresh_from_db(); self.assertEqual(self.pago.monto,12000)

    def test_error_auditoria_revierte_toda_operacion(self):
        with patch('Tiendas.suscripciones.registrar_cambio',side_effect=RuntimeError('Error simulado')):
            with self.assertRaises(RuntimeError): self.vencer(pago_id=self.pago.pk)
        self.tm.refresh_from_db(); self.pago.refresh_from_db()
        self.assertFalse(self.tm.vencida_manualmente); self.assertEqual(self.pago.estado,'vigente')
        self.assertFalse(CambioSuscripcion.objects.exists())

    def test_manualmente_vencida_no_recupera_gracia_con_recalculo(self):
        self.tm.estado='Pre-activada'; self.tm.pre_activada_hasta=date(2026,10,20); self.tm.save()
        self.assertEqual(self.vencer().status_code,200)
        _actualizar_estados_membresias(); comprobar_estado_membresia(self.ruta.pk)
        self.tm.refresh_from_db(); self.assertEqual(self.tm.estado,'Vencida')
        self.assertTrue(self.tm.vencida_manualmente); self.assertIsNone(self.tm.pre_activada_hasta)

    def test_renovacion_confirmada_y_reversion_preservan_historial(self):
        s = self.solicitud()
        self.assertEqual(self.vencer(confirmar_solicitudes_pendientes=True).status_code,200)
        tm = _confirmar_solicitud(s,self.root)
        self.assertFalse(tm.vencida_manualmente); self.assertEqual(tm.fecha_vencimiento,date(2026,11,9))
        _revertir_solicitud(s,'Pago incorrecto',self.root)
        self.tm.refresh_from_db(); self.assertTrue(self.tm.vencida_manualmente)
        self.assertEqual(PagoMembresia.objects.get(solicitud=s).estado,'anulado')
        self.assertEqual(CambioSuscripcion.objects.count(),3)

    def test_reversion_antigua_no_sobrescribe_renovacion_posterior(self):
        s = self.solicitud(); _confirmar_solicitud(s,self.root)
        self.tm.refresh_from_db(); self.assertEqual(self.activar().status_code,200)
        with self.assertRaises(ValueError): _revertir_solicitud(s,'Reversión antigua',self.root)
        s.refresh_from_db(); self.assertEqual(s.estado,'confirmada')
        self.assertEqual(PagoMembresia.objects.get(solicitud=s).estado,'vigente')

    def test_bloqueo_operativo_por_ruta_objeto_y_perfil_para_admin_y_worker(self):
        self.assertEqual(self.vencer().status_code,200)
        paths = [f'/ventas/activas/t/{self.ruta.pk}/', f'/ventas/{self.venta.pk}/',
            f'/ventas/activas/{self.cliente.pk}/', f'/clientes/{self.cliente.pk}/',
            f'/recaudos/list/{self.venta.pk}/',f'/recaudos/{self.recaudo.pk}/',
            f'/aportes/t/{self.ruta.pk}/',f'/gastos/t/{self.ruta.pk}/',
            f'/utilidades/t/{self.ruta.pk}/', f'/publicidad/list/2026-10-10/t/{self.ruta.pk}/',
            f'/tiendas/recorrido/t/{self.ruta.pk}/', f'/tiendas/cierres/t/{self.ruta.pk}/']
        for user in (self.admin,self.worker):
            self.api.force_authenticate(user)
            for url in paths:
                res=self.api.get(url)
                self.assertEqual(res.status_code,403,(url,res.data))
                self.assertEqual(res.data['code'],'membresia_vencida_manual',url)
            for prefix in ('clientes','ventas','recaudos','aportes','gastos','utilidades'):
                res=self.api.post(f'/{prefix}/create/t/{self.ruta.pk}/',{},format='json')
                self.assertEqual(res.status_code,403,(prefix,res.data))
        self.api.force_authenticate(self.worker)
        for prefix in ('clientes','ventas','recaudos','aportes','gastos','utilidades'):
            self.assertEqual(self.api.post(f'/{prefix}/create/',{},format='json').status_code,403,prefix)
        for url in ('/ventas/activas/','/clientes/','/recaudos/'):
            self.assertEqual(self.api.get(url).status_code,403,url)
        self.assertEqual(Recaudo.objects.count(),1); self.assertEqual(Venta.objects.count(),1)

    def test_root_y_otra_ruta_siguen_operando_renovacion_sigue_accesible(self):
        self.assertEqual(self.vencer().status_code,200)
        self.assertEqual(self.api.get(f'/ventas/activas/t/{self.ruta.pk}/').status_code,200)
        self.api.force_authenticate(self.admin)
        self.assertEqual(self.api.get(f'/ventas/activas/t/{self.otra.pk}/').status_code,200)
        detalle=self.api.get(f'/tiendas/detail/admin/{self.ruta.pk}/')
        self.assertEqual(detalle.status_code,200,detalle.data)
        self.assertTrue(detalle.data['vencida_manualmente'])
        self.assertEqual(self.api.get('/tiendas/list/admin/').status_code,200)
        self.assertEqual(self.api.get('/tiendas/planes/').status_code,200)
        self.assertEqual(self.api.get(f'/tiendas/solicitud-pago/actual/t/{self.ruta.pk}/').status_code,200)
        self.assertEqual(self.api.get(f'/ventas/activas/t/{self.otra.pk}/').status_code,200)

    def test_bloqueo_no_afecta_vencimientos_ordinarios_ni_ruta_sin_membresia(self):
        self.api.force_authenticate(self.admin)
        self.tm.estado='Vencida'; self.tm.fecha_vencimiento=date(2026,1,1); self.tm.save()
        self.assertEqual(self.api.get(f'/ventas/activas/t/{self.ruta.pk}/').status_code,200)
        self.tm.delete()
        self.assertEqual(self.api.get(f'/ventas/activas/t/{self.ruta.pk}/').status_code,200)

    def test_fecha_operativa_de_ruta_no_fecha_utc(self):
        self.ruta.zona_horaria='America/Lima'; self.ruta.save()
        with patch('Tiendas.fecha_operativa.timezone.now',return_value=datetime(2026,10,11,1,tzinfo=utc.utc)):
            self.assertEqual(self.vencer().status_code,200)
        self.tm.refresh_from_db(); self.assertEqual(self.tm.fecha_vencimiento,date(2026,10,10))

    def test_login_de_ruta_vencida_manualmente_sigue_serializable(self):
        self.worker.set_password('solo-pruebas'); self.worker.save()
        self.assertEqual(self.vencer().status_code,200)
        request = APIRequestFactory().post('/login/',
            {'username':self.worker.username,'password':'solo-pruebas'},format='json')
        response = Login.as_view(throttle_classes=[])(request)
        self.assertEqual(response.status_code,200,response.data)
        response.render()
        self.assertIsNone(response.data['membresia'])
        self.assertTrue(response.data['token'])

    def test_pago_historico_no_revierte_cambios_nuevos_sin_snapshot(self):
        s=self.solicitud('confirmada'); s.fecha_vencimiento_previa=date(2026,9,1); s.save()
        self.pago.solicitud=s; self.pago.origen='panel'; self.pago.save()
        self.assertEqual(self.vencer().status_code,200)
        self.tm.refresh_from_db(); self.assertEqual(self.activar().status_code,200)
        with self.assertRaises(ValueError):_revertir_solicitud(s,'Reversión antigua',self.root)
        self.tm.refresh_from_db(); self.assertEqual(self.tm.fecha_vencimiento,date(2026,11,9))
        self.pago.refresh_from_db(); self.assertEqual(self.pago.estado,'vigente')

    def test_rechazar_pendiente_no_degrada_renovacion_pagada_posterior(self):
        s=self.solicitud()
        self.assertEqual(self.activar().status_code,200)
        _revertir_solicitud(s,'Comprobante incorrecto',self.root)
        self.tm.refresh_from_db(); self.assertEqual(self.tm.estado,'Activa')
        self.assertEqual(self.tm.fecha_vencimiento,date(2026,12,31))

    def test_retencion_excluye_anulados_y_respeta_pagos_mixtos(self):
        trial=Membresia.objects.create(nombre='Prueba',precio=0)
        self.tm.membresia=trial; self.tm.save()
        self.assertEqual(self.vencer(pago_id=self.pago.pk).status_code,200)
        resumen=self.api.get('/tiendas/admin/resumen/')
        self.assertEqual(resumen.data['retencion']['conversion_trial'],0)
        self.assertEqual(resumen.data['retencion']['trials_perdidos'],1)
        PagoMembresia.objects.create(tienda=self.ruta,membresia=self.mensual,
            fecha=date(2026,10,10),monto=12000,origen='panel')
        resumen=self.api.get('/tiendas/admin/resumen/')
        self.assertEqual(resumen.data['retencion']['conversion_trial'],100)
        self.assertEqual(resumen.data['retencion']['trials_perdidos'],0)

    def test_recalculo_no_guarda_bandera_manual_version_ni_fecha(self):
        self.tm.fecha_vencimiento=date(2026,1,1); self.tm.save()
        original=Tienda_Membresia.save
        def interceptar(tm,*args,**kwargs):
            if kwargs.get('update_fields') and 'estado' in kwargs['update_fields']:
                self.assertNotIn('vencida_manualmente',kwargs['update_fields'])
                self.assertNotIn('version',kwargs['update_fields'])
                self.assertNotIn('fecha_vencimiento',kwargs['update_fields'])
            return original(tm,*args,**kwargs)
        with patch.object(Tienda_Membresia,'save',interceptar):comprobar_estado_membresia(self.ruta.pk)
        self.tm.refresh_from_db(); self.assertEqual(self.tm.estado,'Vencida')
        self.assertEqual(self.vencer().status_code,200)
        _actualizar_estados_membresias()
        self.tm.refresh_from_db(); self.assertTrue(self.tm.vencida_manualmente);self.assertEqual(self.tm.version,1)

    def test_snapshot_sesion_es_liviano_sin_escrituras_y_sin_cartera(self):
        self.api.force_authenticate(self.admin)
        with patch('Tiendas.views._metricas_dashboard_tienda',side_effect=AssertionError('No consultar cartera')), \
            patch('Tiendas.views.comprobar_estado_membresia',side_effect=AssertionError('No recalcular por GET')):
            res=self.api.get(f'/tiendas/detail/admin/{self.ruta.pk}/?vista=membresia')
        self.assertEqual(res.status_code,200,res.data)
        self.assertEqual(res.data['version'],0)
        self.assertNotIn('tienda',res.data); self.assertEqual(res['Cache-Control'],'no-store')
        self.assertFalse(CambioSuscripcion.objects.exists())

    def test_rechazos_sin_efecto_no_ocultan_preactivacion_de_otra_solicitud(self):
        from Tiendas.suscripciones import snapshot, registrar_cambio
        a=self.solicitud()
        b=SolicitudPago.objects.create(tienda=self.ruta,membresia=self.mensual,
            codigo='MM-PRUEBA2',estado='pendiente_confirmacion',expira=a.expira)
        anterior=snapshot(self.tm)
        self.tm.estado='Pre-activada';self.tm.pre_activada_hasta=date(2026,10,13);self.tm.save()
        registrar_cambio(self.tm,anterior,'comprobante_recibido',self.admin,solicitud_codigo=b.codigo)
        _revertir_solicitud(a,'Comprobante A incorrecto',self.root)
        self.tm.refresh_from_db();self.assertEqual(self.tm.estado,'Pre-activada')
        self.assertEqual(self.tm.version,1)
        _revertir_solicitud(b,'Comprobante B incorrecto',self.root)
        self.tm.refresh_from_db();self.assertIsNone(self.tm.pre_activada_hasta)
        self.assertNotEqual(self.tm.estado,'Pre-activada')

    def test_reversion_restauracion_de_archivado_completa(self):
        from Tiendas.suscripciones import snapshot
        s=self.solicitud()
        self.assertEqual(self.vencer(confirmar_solicitudes_pendientes=True).status_code,200)
        self.tm.refresh_from_db();self.tm.archivada=True;self.tm.fecha_archivado=date(2026,10,10);self.tm.save()
        previo=snapshot(self.tm)
        _confirmar_solicitud(s,self.root);_revertir_solicitud(s,'Pago incorrecto',self.root)
        self.tm.refresh_from_db();self.assertEqual(snapshot(self.tm),previo)

    def test_anular_despues_de_vencer_no_cambia_fecha_original(self):
        self.assertEqual(self.vencer().status_code,200)
        self.tm.refresh_from_db();fecha=self.tm.fecha_vencimiento;hora=self.tm.vencimiento_manual_fecha
        with patch('Tiendas.fecha_operativa.timezone.now',return_value=datetime(2026,10,12,15,tzinfo=utc.utc)):
            self.assertEqual(self.vencer(pago_id=self.pago.pk).status_code,200)
        self.tm.refresh_from_db();self.assertEqual(self.tm.fecha_vencimiento,fecha)
        self.assertEqual(self.tm.vencimiento_manual_fecha,hora)
        self.assertEqual(CambioSuscripcion.objects.order_by('-version').first().accion,'anulacion_ingreso_manual')

    def test_comprobante_no_reabre_una_ruta_vencida_manualmente(self):
        s=self.solicitud('pendiente')
        self.assertEqual(self.vencer(confirmar_solicitudes_pendientes=True).status_code,200)
        self.api.force_authenticate(self.admin)
        with tempfile.TemporaryDirectory(prefix='suscripciones-pruebas-') as media, \
            override_settings(MEDIA_ROOT=media), \
            patch('Tiendas.telegram_bot.comprimir_imagen',return_value=(b'imagen-ficticia','prueba.jpg')), \
            patch('Tiendas.telegram_bot.notificar_solicitud',return_value=None):
            res=self.api.post(f'/tiendas/solicitud-pago/{s.codigo}/comprobante/',
                {'comprobante':SimpleUploadedFile('prueba.jpg',b'imagen-ficticia',content_type='image/jpeg')},format='multipart')
        self.assertEqual(res.status_code,200,res.data)
        self.tm.refresh_from_db();s.refresh_from_db()
        self.assertTrue(self.tm.vencida_manualmente);self.assertEqual(self.tm.estado,'Vencida')
        self.assertIsNone(self.tm.pre_activada_hasta);self.assertEqual(s.estado,'pendiente_confirmacion')
        self.assertEqual(self.api.get(f'/ventas/activas/t/{self.ruta.pk}/').status_code,403)
