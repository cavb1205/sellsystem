"""Regresiones etapa 1. SQLite desechable, usuarios ficticios y red prohibida."""
from datetime import date
from importlib import import_module
from unittest.mock import patch
from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from Tiendas.test_suscripciones import SuscripcionesTests
from Tiendas.models import (Tienda, Tienda_Membresia, Membresia, PagoMembresia,
    SolicitudPago, CambioSuscripcion, Cierre_Caja)
from Tiendas.views import _confirmar_solicitud, _datos_solicitud_pago
from Tiendas.serializers import SolicitudPagoSerializer
from Tiendas.telegram_bot import _caption
from Trabajadores.models import Perfil
from Aportes.models import Aporte
from Gastos.models import Gasto, Tipo_Gasto
from Utilidades.models import Utilidad
from Publicidad.models import Publicidad


@override_settings(ROOT_URLCONF='Tiendas.test_suscripciones')
class RootIntegridadTests(TestCase):
    setUp = SuscripcionesTests.setUp
    solicitud = SuscripcionesTests.solicitud

    def vacia(self):
        t = Tienda.objects.create(nombre='Vacía real', administrador=self.admin, caja_inicial=0)
        Tienda_Membresia.objects.create(tienda=t,membresia=self.mensual,
            fecha_activacion=date(2026,10,1),fecha_vencimiento=date(2026,12,1))
        return t

    def payload(self):
        return [
            {'id':self.mensual.pk,'precio':15000,'version':self.mensual.version},
            {'id':self.anual.pk,'precio':130000,'version':self.anual.version}]

    def test_los_dos_borrados_conservan_cartera(self):
        for endpoint in ('delete','admin/delete'):
            r=self.api.delete(f'/tiendas/{self.ruta.pk}/{endpoint}/')
            self.assertEqual(r.status_code,409,r.data)
        self.assertEqual(self.ruta.cliente_set.count(),1)
        self.assertEqual(self.ruta.venta_set.count(),1)
        self.assertEqual(self.ruta.recaudo_set.count(),1)
        self.ruta.refresh_from_db();self.assertEqual(self.ruta.caja_inicial,10000)

    def test_nombre_root_sin_rol_no_autoriza(self):
        ordinary=User.objects.create_user('root')
        t=self.vacia()
        for user in (ordinary,self.admin,self.worker,None):
            self.api.force_authenticate(user)
            for endpoint in ('delete','admin/delete'):
                self.assertIn(self.api.delete(f'/tiendas/{t.pk}/{endpoint}/').status_code,(401,403))
        self.assertTrue(Tienda.objects.filter(pk=t.pk).exists())

    def test_superusuario_otro_nombre_puede_eliminar_sin_historial(self):
        for endpoint in ('delete','admin/delete'):
            t=self.vacia()
            self.assertEqual(self.api.delete(f'/tiendas/{t.pk}/{endpoint}/').status_code,200)
            self.assertFalse(Tienda.objects.filter(pk=t.pk).exists())

    def test_gastos_aportes_utilidades_cierres_y_caja_protegen(self):
        perfil=Perfil.objects.get(trabajador=self.worker)
        factories=[
            lambda t:Gasto.objects.create(tienda=t,trabajador=perfil,tipo_gasto=Tipo_Gasto.objects.create(tipo_gasto='Ficticio'),fecha=date(2026,10,10),valor=500),
            lambda t:Aporte.objects.create(tienda=t,trabajador=perfil,fecha=date(2026,10,10),valor=500),
            lambda t:Utilidad.objects.create(tienda=t,trabajador=perfil,fecha=date(2026,10,10),valor=500),
            lambda t:Cierre_Caja.objects.create(tienda=t,fecha_cierre=date(2026,10,10),valor=0),
        ]
        for i,create in enumerate(factories):
            t=self.vacia();t.nombre=f'Historial {i}';t.save()
            record=create(t)
            self.assertEqual(self.api.delete(f'/tiendas/{t.pk}/admin/delete/').status_code,409)
            self.assertTrue(type(record).objects.filter(pk=record.pk).exists())
        t=self.vacia();t.nombre='Con caja';t.caja_inicial=1;t.save()
        self.assertEqual(self.api.delete(f'/tiendas/{t.pk}/delete/').status_code,409)

    def test_historial_suscripcion_y_solicitudes_no_se_borran(self):
        t=self.vacia()
        s=SolicitudPago.objects.create(tienda=t,membresia=self.mensual,codigo='MM-OTRA',expira=self.solicitud().expira)
        self.assertEqual(self.api.delete(f'/tiendas/{t.pk}/admin/delete/').status_code,409)
        self.assertTrue(SolicitudPago.objects.filter(pk=s.pk).exists())
        self.assertTrue(PagoMembresia.objects.filter(pk=self.pago.pk).exists())

    def test_publicidad_y_auditoria_solas_protegen_la_ruta(self):
        t=self.vacia()
        punto=Publicidad.objects.create(tienda=t,latitud=-22,longitud=-68,registrado_por=self.admin)
        self.assertEqual(self.api.delete(f'/tiendas/{t.pk}/delete/').status_code,409)
        self.assertTrue(Publicidad.objects.filter(pk=punto.pk).exists())
        t.nombre='Publicidad conservada';t.save(update_fields=['nombre'])
        otra=self.vacia()
        CambioSuscripcion.objects.create(tienda=otra,tienda_nombre=otra.nombre,version=1,accion='prueba')
        self.assertEqual(self.api.delete(f'/tiendas/{otra.pk}/admin/delete/').status_code,409)

    def test_fallo_auditoria_archivado_revierte_archivado_y_version(self):
        with patch('Tiendas.views.registrar_cambio',side_effect=RuntimeError('Auditoría ficticia fallida')):
            with self.assertRaises(RuntimeError):
                self.api.post(f'/tiendas/{self.tm.pk}/archivar/',{'archivar':True,'version':0},format='json')
        self.tm.refresh_from_db()
        self.assertFalse(self.tm.archivada)
        self.assertIsNone(self.tm.fecha_archivado)
        self.assertEqual(self.tm.version,0)
        self.assertFalse(CambioSuscripcion.objects.exists())

    def test_lista_ofrece_borrado_solo_si_politica_lo_permite(self):
        t=self.vacia()
        r=self.api.get('/tiendas/list/?vista=lista')
        rows={x['tienda']['id']:x for x in r.data}
        self.assertTrue(rows[t.pk]['puede_eliminar'])
        self.assertFalse(rows[self.ruta.pk]['puede_eliminar'])
        self.assertFalse(rows[self.otra.pk]['puede_eliminar'])

    def test_precio_acordado_no_cambia_en_api_telegram_ni_ledger(self):
        s=self.solicitud()
        self.assertEqual(self.api.put('/tiendas/planes/',self.payload(),format='json').status_code,200)
        s.refresh_from_db()
        self.assertEqual(s.monto_acordado,12000)
        self.assertEqual(SolicitudPagoSerializer(s).data['monto_plan'],'12000')
        self.assertEqual(_datos_solicitud_pago(s)['monto'],'12000')
        self.assertIn('$12.000',_caption(s))
        _confirmar_solicitud(s,self.root)
        self.assertEqual(PagoMembresia.objects.get(solicitud=s).monto,12000)
        self.assertEqual(self.pago.monto,12000)

    def test_solicitud_nueva_usa_tarifa_actual_y_cero_explicito(self):
        self.api.force_authenticate(self.admin)
        r=self.api.post('/tiendas/solicitar-pago/',{'plan':'Mensual','tienda_id':self.ruta.pk},format='json')
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(SolicitudPago.objects.get(codigo=r.data['codigo']).monto_acordado,12000)
        self.api.force_authenticate(self.root)
        p=[{'id':self.anual.pk,'precio':0,'version':0}]
        self.assertEqual(self.api.put('/tiendas/planes/',p,format='json').status_code,200)
        self.anual.refresh_from_db();self.assertEqual(self.anual.precio,0)

    def test_precio_invalido_no_guarda_parcialmente(self):
        for value in ('',-1,True,1.5,'12.5','10000000000',None):
            p=self.payload();p[1]['precio']=value
            self.assertEqual(self.api.put('/tiendas/planes/',p,format='json').status_code,400,value)
            self.mensual.refresh_from_db();self.assertEqual(self.mensual.precio,12000)
            self.assertEqual(self.mensual.version,0)

    def test_payload_invalido_y_plan_inexistente_no_escriben(self):
        for p in (None, 'texto',{},[],[1],[{'id':True,'precio':1,'version':0}],
                  [self.payload()[0],self.payload()[0]], [{'id':self.mensual.pk,'precio':1}]):
            self.assertEqual(self.api.put('/tiendas/planes/',p,format='json').status_code,400,p)
        p=self.payload();p[1]['id']=99999
        self.assertEqual(self.api.put('/tiendas/planes/',p,format='json').status_code,404)
        self.mensual.refresh_from_db();self.assertEqual(self.mensual.precio,12000)

    def test_conflicto_de_tarifas_no_sobrescribe_otra_sesion(self):
        p=self.payload()
        self.assertEqual(self.api.put('/tiendas/planes/',p,format='json').status_code,200)
        p[0]['precio']=16000;p[1]['precio']=140000
        self.assertEqual(self.api.put('/tiendas/planes/',p,format='json').status_code,409)
        self.mensual.refresh_from_db();self.anual.refresh_from_db()
        self.assertEqual((self.mensual.precio,self.anual.precio),(15000,130000))

    def test_error_durante_segundo_guardado_revierte_todo(self):
        original=Membresia.save
        def fail(plan,*args,**kwargs):
            if plan.pk==self.anual.pk:raise RuntimeError('Fallo de prueba')
            return original(plan,*args,**kwargs)
        with patch.object(Membresia,'save',fail):
            with self.assertRaises(RuntimeError):self.api.put('/tiendas/planes/',self.payload(),format='json')
        self.mensual.refresh_from_db();self.assertEqual(self.mensual.precio,12000)
        self.assertEqual(self.mensual.version,0)

    def test_admin_y_worker_no_editan_tarifas_ni_archivan(self):
        for user in (self.admin,self.worker,None):
            self.api.force_authenticate(user)
            self.assertIn(self.api.put('/tiendas/planes/',self.payload(),format='json').status_code,(401,403))
            self.assertIn(self.api.post(f'/tiendas/{self.tm.pk}/archivar/',{'archivar':True,'version':0},format='json').status_code,(401,403))

    def test_archivar_versiona_audita_y_no_toca_finanzas(self):
        r=self.api.post(f'/tiendas/{self.tm.pk}/archivar/',{'archivar':True,'version':0},format='json')
        self.assertEqual(r.status_code,200,r.data);self.tm.refresh_from_db()
        self.assertEqual(self.tm.version,1);self.assertTrue(self.tm.archivada)
        self.assertEqual(CambioSuscripcion.objects.get().accion,'archivado')
        self.assertEqual(self.ruta.recaudo_set.count(),1);self.pago.refresh_from_db();self.assertEqual(self.pago.estado,'vigente')
        self.assertEqual(self.api.post(f'/tiendas/{self.tm.pk}/archivar/',{'archivar':True,'version':1},format='json').status_code,200)
        self.assertEqual(CambioSuscripcion.objects.count(),1)
        self.assertEqual(self.api.post(f'/tiendas/{self.tm.pk}/archivar/',{'archivar':False,'version':1},format='json').status_code,200)
        self.tm.refresh_from_db();self.assertEqual(self.tm.version,2)
        self.assertEqual(CambioSuscripcion.objects.order_by('-version').first().accion,'restauracion')

    def test_reversion_no_deshace_archivado_posterior(self):
        s=self.solicitud();_confirmar_solicitud(s,self.root)
        self.tm.refresh_from_db()
        self.assertEqual(self.api.post(f'/tiendas/{self.tm.pk}/archivar/',{'archivar':True,'version':self.tm.version},format='json').status_code,200)
        r=self.api.post(f'/tiendas/solicitud/{s.codigo}/revisar/',{'resultado':'rechazar'},format='json')
        self.assertEqual(r.status_code,409,r.data)
        self.tm.refresh_from_db();self.assertTrue(self.tm.archivada)
        self.assertEqual(PagoMembresia.objects.get(solicitud=s).estado,'vigente')

    def test_archivado_invalido_o_con_version_antigua_no_escribe(self):
        for p in ({},{'archivar':'false','version':0},{'archivar':True},{'archivar':True,'version':True}):
            self.assertEqual(self.api.post(f'/tiendas/{self.tm.pk}/archivar/',p,format='json').status_code,400)
        self.assertEqual(self.api.post(f'/tiendas/{self.tm.pk}/archivar/',{'archivar':True,'version':10},format='json').status_code,409)
        self.assertFalse(CambioSuscripcion.objects.exists())

    def test_solicitud_legacy_no_inventa_precio_y_exige_revision_root(self):
        s=self.solicitud();SolicitudPago.objects.filter(pk=s.pk).update(monto_acordado=None);s.refresh_from_db()
        self.assertIsNone(_datos_solicitud_pago(s)['monto'])
        self.assertTrue(SolicitudPagoSerializer(s).data['requiere_revision_monto'])
        with self.assertRaises(ValueError):_confirmar_solicitud(s)
        self.assertEqual(self.api.post(f'/tiendas/solicitud/{s.codigo}/revisar/',{'resultado':'confirmar'},format='json').status_code,409)
        r=self.api.post(f'/tiendas/solicitud/{s.codigo}/revisar/',{'resultado':'confirmar','monto_confirmado':11000},format='json')
        self.assertEqual(r.status_code,200,r.data)
        s.refresh_from_db();self.assertEqual(s.monto_acordado,11000)
        self.assertEqual(PagoMembresia.objects.get(solicitud=s).monto,11000)
        _confirmar_solicitud(s,self.root);self.assertEqual(PagoMembresia.objects.filter(solicitud=s).count(),1)

    def test_importe_legacy_invalido_y_cotizacion_nueva_no_se_sobrescriben(self):
        s=self.solicitud()
        with self.assertRaises(ValueError):_confirmar_solicitud(s,self.root,11000)
        SolicitudPago.objects.filter(pk=s.pk).update(monto_acordado=None);s.refresh_from_db()
        for v in ('',True,-1,1.5,10000000000):
            with self.assertRaises(ValueError):_confirmar_solicitud(s,self.root,v)
        s.refresh_from_db();self.assertEqual(s.estado,'pendiente_confirmacion')
        self.assertIsNone(s.monto_acordado)

    def test_migracion_recupera_solo_monto_de_ledger_sin_cambiar_ingresos(self):
        s=self.solicitud();SolicitudPago.objects.filter(pk=s.pk).update(monto_acordado=None)
        ledger=PagoMembresia.objects.create(solicitud=s,tienda=self.ruta,membresia=self.mensual,monto=10000,fecha=date(2026,10,10))
        otro=SolicitudPago.objects.create(tienda=self.otra,membresia=self.anual,codigo='AA-LEGACY',expira=s.expira)
        SolicitudPago.objects.filter(pk=otro.pk).update(monto_acordado=None)
        import_module('Tiendas.migrations.0027_root_integridad').recuperar_montos_verificables(apps,type('Editor',(),{'connection':type('Connection',(),{'alias':'default'})()})())
        s.refresh_from_db();otro.refresh_from_db();ledger.refresh_from_db()
        self.assertEqual(s.monto_acordado,10000);self.assertIsNone(otro.monto_acordado)
        self.assertEqual(ledger.monto,10000);self.assertEqual(self.pago.monto,12000)
