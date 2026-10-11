"""Etapa 2: red prohibida, SQLite desechable y datos ficticios."""
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from Tiendas.models import (SolicitudPago, CuentaDestino, CambioConfiguracionRoot,
    PagoMembresia, CambioSuscripcion)
from Tiendas.test_suscripciones import SuscripcionesTests
from Tiendas.views import _confirmar_solicitud, _normalizar_solicitud_pago, _datos_solicitud_pago
from Tiendas.suscripciones import snapshot, registrar_cambio


@override_settings(ROOT_URLCONF='Tiendas.test_suscripciones')
class RootEstadosTests(TestCase):
    setUp = SuscripcionesTests.setUp
    solicitud = SuscripcionesTests.solicitud

    def antigua(self):
        s = self.solicitud()
        SolicitudPago.objects.filter(pk=s.pk).update(creada=timezone.now()-timedelta(days=5), comprobante='ficticio.jpg')
        s.refresh_from_db()
        return s

    def revisar(self, s, **data):
        return self.api.post(f'/tiendas/solicitud/{s.codigo}/revisar/', data, format='json')

    def datos_cuenta(self, **extra):
        return {'banco':'Banco Nuevo', 'numero':'002-PRUEBA', 'titular':'Titular Nuevo',
            'tipo':'Cuenta Ficticia', 'version':0, 'motivo':'Cambio verificado', **extra}

    def test_web_no_aprueba_expiradas_sin_excepcion_y_lista_las_separa(self):
        s=self.antigua()
        response=self.revisar(s, resultado='confirmar')
        self.assertEqual(response.status_code,409,response.data)
        s.refresh_from_db();self.assertEqual(s.estado,'expirada')
        self.assertFalse(PagoMembresia.objects.filter(solicitud=s).exists())
        response=self.api.get('/tiendas/solicitudes/revision/')
        self.assertEqual(response.data['pendientes'],[])
        self.assertEqual(response.data['vencidas'][0]['codigo'],s.codigo)

    def test_helper_interno_y_telegram_no_eluden_la_regla(self):
        s=self.antigua()
        with self.assertRaises(ValueError):_confirmar_solicitud(s,self.root)
        with override_settings(TELEGRAM_WEBHOOK_SECRET='solo-prueba',TELEGRAM_ADMIN_CHAT_ID='123'), \
            patch('Tiendas.telegram_assistant.procesar_callback',return_value=False), \
            patch('Tiendas.telegram_bot.procesar_callback_alerta',return_value=False), \
            patch('Tiendas.telegram_bot.responder_callback') as answer:
            r=self.api.post('/tiendas/telegram/webhook/',{'callback_query':{
                'id':'ficticio','data':'confirmar:'+s.codigo,'message':{'chat':{'id':123}}}},
                format='json',HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='solo-prueba')
        self.assertEqual(r.status_code,200,r.data)
        self.assertIn('expirada',answer.call_args.args[1])
        self.assertFalse(PagoMembresia.objects.filter(solicitud=s).exists())

    def test_excepcion_exige_booleano_motivo_comprobante_y_root(self):
        s=self.antigua()
        for data in ({'confirmar_expirada':'true','motivo':'Ingreso verificado'},
            {'confirmar_expirada':True,'motivo':''},{'confirmar_expirada':True,'motivo':123}):
            self.assertEqual(self.revisar(s,resultado='confirmar',**data).status_code,400)
        for user in (self.admin,self.worker,None):
            self.api.force_authenticate(user)
            self.assertIn(self.revisar(s,resultado='confirmar',confirmar_expirada=True,motivo='Ingreso verificado').status_code,(401,403))
        self.api.force_authenticate(self.root)
        SolicitudPago.objects.filter(pk=s.pk).update(comprobante='')
        self.assertEqual(self.revisar(s,resultado='confirmar',confirmar_expirada=True,motivo='Ingreso verificado').status_code,409)
        self.assertFalse(PagoMembresia.objects.filter(solicitud=s).exists())

    def test_excepcion_registra_precio_original_motivo_actor_y_sin_duplicados(self):
        s=self.antigua()
        self.mensual.precio=15000;self.mensual.save()
        r=self.revisar(s,resultado='confirmar',confirmar_expirada=True,motivo='Transferencia verificada fuera de plazo')
        self.assertEqual(r.status_code,200,r.data)
        s.refresh_from_db();self.assertTrue(s.confirmada_fuera_plazo)
        self.assertEqual(s.revisada_por,self.root);self.assertIn('fuera de plazo',s.motivo_revision)
        self.assertEqual(PagoMembresia.objects.get(solicitud=s).monto,12000)
        change=CambioSuscripcion.objects.get(accion='confirmacion_fuera_plazo')
        self.assertEqual(change.usuario,self.root);self.assertEqual(change.motivo,s.motivo_revision)
        self.assertEqual(self.revisar(s,resultado='confirmar',confirmar_expirada=True,motivo='Intento repetido').status_code,409)
        self.assertEqual(PagoMembresia.objects.filter(solicitud=s).count(),1)
        self.ruta.refresh_from_db();self.venta.refresh_from_db()
        self.assertEqual(self.ruta.caja_inicial,10000);self.assertEqual(self.venta.saldo_actual,1100)

    def test_expirar_solicitud_antigua_no_oculta_preactivacion_nueva(self):
        vieja=self.antigua()
        nueva=SolicitudPago.objects.create(tienda=self.ruta,membresia=self.mensual,codigo='MM-NUEVA',
            estado='pendiente_confirmacion',expira=timezone.now()+timedelta(days=1))
        anterior=snapshot(self.tm)
        self.tm.estado='Pre-activada';self.tm.pre_activada_hasta=timezone.localdate()+timedelta(days=3);self.tm.save()
        registrar_cambio(self.tm,anterior,'comprobante_recibido',self.admin,solicitud_codigo=nueva.codigo)
        _normalizar_solicitud_pago(vieja)
        self.tm.refresh_from_db();self.assertEqual(self.tm.estado,'Pre-activada');self.assertEqual(self.tm.version,1)

    def test_expiracion_propia_versiona_y_audita_efecto_temporal(self):
        s=self.antigua();anterior=snapshot(self.tm)
        self.tm.estado='Pre-activada';self.tm.pre_activada_hasta=timezone.localdate()+timedelta(days=1);self.tm.save()
        registrar_cambio(self.tm,anterior,'comprobante_recibido',self.admin,solicitud_codigo=s.codigo)
        _normalizar_solicitud_pago(s)
        self.tm.refresh_from_db();self.assertEqual(self.tm.estado,'Pendiente Pago');self.assertEqual(self.tm.version,2)
        self.assertTrue(CambioSuscripcion.objects.filter(accion='solicitud_expirada').exists())

    def test_reversion_exige_motivo_y_devuelve_estado_real_no_pendiente_falso(self):
        s=self.solicitud();_confirmar_solicitud(s,self.root)
        self.assertEqual(self.revisar(s,resultado='revertir').status_code,400)
        r=self.revisar(s,resultado='revertir',motivo='Ingreso bancario no localizado')
        self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(r.data['estado_membresia'],'Activa')
        self.assertEqual(r.data['fecha_vencimiento'],'2026-12-01')
        self.assertIn('no se realizó un reembolso',r.data['message'])
        self.assertEqual(PagoMembresia.objects.get(solicitud=s).estado,'anulado')
        data=self.api.get('/tiendas/solicitudes/revision/').data
        self.assertEqual(data['revisadas'][0]['motivo_revision'],'Ingreso bancario no localizado')

    def test_payload_y_periodo_invalidos_no_hacen_una_revision(self):
        s=self.solicitud()
        self.assertEqual(self.api.post(f'/tiendas/solicitud/{s.codigo}/revisar/',[],format='json').status_code,400)
        for dias in ('x','0','1000'):
            self.assertEqual(self.api.get('/tiendas/solicitudes/revision/?dias='+dias).status_code,400)
        s.refresh_from_db();self.assertEqual(s.estado,'pendiente_confirmacion')

    def test_cuenta_incompleta_invalida_no_guarda_y_vacia_inicial_no_es_operativa(self):
        for key in ('banco','numero','titular','tipo'):
            for value in ('','   ',None,123,'x'*101):
                self.assertEqual(self.api.put('/tiendas/cuenta-destino/',self.datos_cuenta(**{key:value}),format='json').status_code,400)
        cuenta=CuentaDestino.get();self.assertEqual(cuenta.version,0)
        self.assertFalse(CambioConfiguracionRoot.objects.exists())
        cuenta.banco=cuenta.numero=cuenta.titular=cuenta.tipo='';cuenta.save()
        self.assertFalse(self.api.get('/tiendas/cuenta-destino/').data['configurada'])
        self.api.force_authenticate(self.admin)
        r=self.api.post('/tiendas/solicitar-pago/',{'plan':'Mensual','tienda_id':self.ruta.pk},format='json')
        self.assertEqual(r.status_code,409);self.assertFalse(SolicitudPago.objects.exists())

    def test_cuenta_versionada_auditada_noop_y_conflicto(self):
        r=self.api.put('/tiendas/cuenta-destino/',self.datos_cuenta(),format='json')
        self.assertEqual(r.status_code,200,r.data);self.assertEqual(r.data['version'],1)
        log=CambioConfiguracionRoot.objects.get(tipo='cuenta')
        self.assertEqual(log.anterior['numero'],'000-PRUEBA');self.assertEqual(log.nuevo['numero'],'002-PRUEBA')
        self.assertEqual(log.usuario,self.root);self.assertEqual(log.motivo,'Cambio verificado')
        self.assertEqual(self.api.put('/tiendas/cuenta-destino/',self.datos_cuenta(numero='003-PRUEBA'),format='json').status_code,409)
        self.assertEqual(CuentaDestino.get().numero,'002-PRUEBA')
        self.assertEqual(self.api.put('/tiendas/cuenta-destino/',self.datos_cuenta(version=1),format='json').status_code,200)
        self.assertEqual(CambioConfiguracionRoot.objects.count(),1)

    def test_fallo_auditoria_no_cambia_cuenta(self):
        with patch('Tiendas.views.registrar_configuracion',side_effect=RuntimeError('Error ficticio')):
            with self.assertRaises(RuntimeError):self.api.put('/tiendas/cuenta-destino/',self.datos_cuenta(),format='json')
        cuenta=CuentaDestino.get();self.assertEqual(cuenta.numero,'000-PRUEBA');self.assertEqual(cuenta.version,0)
        self.assertFalse(CambioConfiguracionRoot.objects.exists())

    def test_cuenta_original_se_conserva_y_la_historica_no_se_inventa(self):
        self.api.force_authenticate(self.admin)
        a=self.api.post('/tiendas/solicitar-pago/',{'plan':'Mensual','tienda_id':self.ruta.pk},format='json')
        self.assertEqual(a.status_code,201,a.data)
        self.api.force_authenticate(self.root)
        self.assertEqual(self.api.put('/tiendas/cuenta-destino/',self.datos_cuenta(),format='json').status_code,200)
        s=SolicitudPago.objects.get(codigo=a.data['codigo'])
        self.assertEqual(_datos_solicitud_pago(s)['cuenta']['numero'],'000-PRUEBA')
        self.assertNotIn('historial',_datos_solicitud_pago(s)['cuenta'])
        self.api.force_authenticate(self.admin)
        b=self.api.post('/tiendas/solicitar-pago/',{'plan':'Mensual','tienda_id':self.otra.pk},format='json')
        self.assertEqual(b.data['cuenta']['numero'],'002-PRUEBA')
        legacy=self.solicitud();self.assertIsNone(_datos_solicitud_pago(legacy)['cuenta'])
        self.assertFalse(_datos_solicitud_pago(legacy)['cuenta_historica_verificable'])

    def test_tarifas_auditan_cambios_completos_y_no_auditan_invalidos(self):
        payload={'planes':[{'id':self.mensual.pk,'precio':15000,'version':0},
            {'id':self.anual.pk,'precio':130000,'version':0}], 'motivo':'Nueva tarifa verificada'}
        self.assertEqual(self.api.put('/tiendas/planes/',payload,format='json').status_code,200)
        log=CambioConfiguracionRoot.objects.get(tipo='planes')
        self.assertEqual(log.anterior[str(self.mensual.pk)]['precio'],'12000')
        self.assertEqual(log.nuevo[str(self.anual.pk)]['precio'],'130000')
        self.assertEqual(log.usuario,self.root)
        payload['planes'][1]['precio']='inválido'
        self.assertEqual(self.api.put('/tiendas/planes/',payload,format='json').status_code,400)
        self.assertEqual(CambioConfiguracionRoot.objects.count(),1)

    def test_fallo_auditoria_tarifas_revierte_precios_y_versiones(self):
        with patch('Tiendas.views.registrar_configuracion',side_effect=RuntimeError('Error ficticio')):
            with self.assertRaises(RuntimeError):self.api.put('/tiendas/planes/',[{'id':self.mensual.pk,'precio':15000,'version':0}],format='json')
        self.mensual.refresh_from_db();self.assertEqual(self.mensual.precio,12000);self.assertEqual(self.mensual.version,0)
        self.assertFalse(CambioConfiguracionRoot.objects.exists())

    def test_bitacora_y_cuenta_solo_root(self):
        for user in (self.admin,self.worker,None):
            self.api.force_authenticate(user)
            for path in ('/tiendas/configuracion/historial/?tipo=cuenta','/tiendas/cuenta-destino/'):
                self.assertIn(self.api.get(path).status_code,(401,403))
            self.assertIn(self.api.put('/tiendas/cuenta-destino/',self.datos_cuenta(),format='json').status_code,(401,403))
