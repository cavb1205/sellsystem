from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
import requests
from django.test import TestCase, override_settings
from django.utils import timezone
from Tiendas.test_suscripciones import SuscripcionesTests
from Tiendas.models import ReporteRutasTelegram, Tienda, Tienda_Administrador
from Tiendas.alertas_operativas import resumen_operativo
from Tiendas.entrega_reporte_rutas import preparar_reporte, entregar_reporte, enviar_parte, reintentar_pendientes
from Tiendas.consulta_reporte_rutas import detalle_reporte, menu_reporte
from Tiendas.reporte_rutas import longitud_visible
from Tiendas import telegram_assistant
from Ventas.models import Venta


@override_settings(TELEGRAM_BOT_TOKEN='ficticio-no-enviar', TELEGRAM_ADMIN_CHAT_ID='12345',
    TELEGRAM_ASSISTANT_CHAT_ID='12345', TELEGRAM_ASSISTANT_USERNAME='admin-pruebas',
    TELEGRAM_ALERTAS_USERNAME='admin-pruebas')
class EntregaReporteTests(TestCase):
    setUp = SuscripcionesTests.setUp

    def reporte(self, cantidad=2):
        rutas = []
        for n in range(cantidad):
            ruta = self.ruta if n == 0 else self.otra if n == 1 else Tienda.objects.create(
                nombre=f'Ruta ficticia {n}', administrador=self.admin)
            if n > 1:
                Tienda_Administrador.objects.create(tienda=ruta, administrador=self.admin)
            rutas.append(dict(id=ruta.pk,nombre=ruta.nombre,esperado=60,recaudo=0,
                cobertura_recaudo=0,pendiente_recaudo=60,cobros_adicionales=0,
                programadas=1,con_abono=0,sin_abono=0,sin_gestion=1,cierre=False,
                alertas_criticas=0,alertas_urgentes=0,creditos_detalle=[{
                    'venta_id': self.venta.pk, 'cliente_id': self.cliente.pk,
                    'cliente': 'Persona <ficticia> & prueba', 'cuota':'60','abonado':'0',
                    'pendiente':'60','estado':'sin_registro'}]))
        return preparar_reporte(self.admin,date(2026,10,9),rutas)

    def test_foto_se_conserva_y_no_duplica_envios_confirmados(self):
        r = self.reporte()
        with patch('Tiendas.entrega_reporte_rutas.enviar_parte', return_value={'estado':'enviado','mensaje_id':'100','error':''}) as envio:
            self.assertTrue(entregar_reporte(r.pk)['completo'])
            r2 = self.reporte()
            self.assertEqual(r.pk,r2.pk)
            self.assertTrue(entregar_reporte(r.pk)['completo'])
            self.assertEqual(envio.call_count,1)
        self.assertEqual(ReporteRutasTelegram.objects.count(),1)
        self.venta.saldo_actual=10; self.venta.save()
        self.assertEqual(self.reporte().rutas[0]['esperado'],60)

    def test_reintenta_solo_parte_rechazada_sin_reenviar_confirmadas(self):
        r = self.reporte(16)
        self.assertGreater(len(r.partes),1)
        respuestas = [{'estado':'fallido','error':'rechazo_telegram'}] + [
            {'estado':'enviado','mensaje_id':str(n),'error':''} for n in range(len(r.partes)-1)]
        with patch('Tiendas.entrega_reporte_rutas.enviar_parte',side_effect=respuestas):
            self.assertFalse(entregar_reporte(r.pk)['completo'])
        with patch('Tiendas.entrega_reporte_rutas.enviar_parte',return_value={'estado':'enviado','mensaje_id':'999','error':''}) as envio:
            self.assertTrue(entregar_reporte(r.pk)['completo'])
            self.assertEqual(envio.call_count,1)

    def test_timeout_no_se_reenvia_automaticamente(self):
        r = self.reporte()
        with patch('Tiendas.entrega_reporte_rutas.enviar_parte',return_value={'estado':'incierto','error':'red_sin_confirmacion'}) as envio:
            self.assertEqual(entregar_reporte(r.pk)['inciertas'],1)
            entregar_reporte(r.pk)
            self.assertEqual(envio.call_count,1)

    def test_segundo_proceso_durante_envio_no_reclama_la_misma_parte(self):
        r = self.reporte()
        def durante_envio(parte, chat):
            self.assertFalse(entregar_reporte(r.pk)['completo'])
            return {'estado':'enviado','mensaje_id':'101','error':''}
        with patch('Tiendas.entrega_reporte_rutas.enviar_parte',side_effect=durante_envio) as envio:
            self.assertTrue(entregar_reporte(r.pk)['completo'])
            self.assertEqual(envio.call_count,1)

    def test_envio_en_curso_no_se_duplica_y_expirado_se_marca_incierto(self):
        r = self.reporte()
        r.partes[0].update(estado='enviando',inicio=timezone.now().isoformat(),intentos=1)
        r.save()
        with patch('Tiendas.entrega_reporte_rutas.enviar_parte') as envio:
            entregar_reporte(r.pk); envio.assert_not_called()
            r.partes[0]['inicio']=(timezone.now()-timedelta(minutes=5)).isoformat();r.save()
            self.assertEqual(entregar_reporte(r.pk)['inciertas'],1)
            envio.assert_not_called()

    def test_tres_intentos_y_espera_de_telegram(self):
        r = self.reporte()
        with patch('Tiendas.entrega_reporte_rutas.enviar_parte',return_value={'estado':'fallido','error':'rechazo_telegram'}) as envio:
            for _ in range(5): entregar_reporte(r.pk)
            self.assertEqual(envio.call_count,3)
        r.partes[0].update(estado='fallido',intentos=1,
            proximo_intento=(timezone.now()+timedelta(minutes=1)).isoformat());r.save()
        with patch('Tiendas.entrega_reporte_rutas.enviar_parte') as envio:
            entregar_reporte(r.pk);envio.assert_not_called()

    def test_cambio_de_chat_propietario_y_permisos_no_filtran_datos(self):
        r=self.reporte()
        with patch('Tiendas.entrega_reporte_rutas.enviar_parte') as envio:
            with override_settings(TELEGRAM_ADMIN_CHAT_ID='999'):
                self.assertTrue(entregar_reporte(r.pk)['omitido'])
            with override_settings(TELEGRAM_ALERTAS_USERNAME='root-pruebas'):
                self.assertTrue(entregar_reporte(r.pk)['omitido'])
            Tienda_Administrador.objects.filter(tienda=self.ruta,administrador=self.admin).delete()
            self.assertFalse(entregar_reporte(r.pk)['completo'])
            envio.assert_not_called()
        r.refresh_from_db();self.assertEqual(r.partes[0]['estado'],'bloqueado')

    def test_transporte_respuestas_y_logs_sin_secretos(self):
        parte={'texto':'Reporte ficticio'}
        with patch('Tiendas.entrega_reporte_rutas.requests.post',return_value=Mock(status_code=200,json=lambda:{'ok':True,'result':{'message_id':9}})):
            self.assertEqual(enviar_parte(parte,'12345')['estado'],'enviado')
        with patch('Tiendas.entrega_reporte_rutas.requests.post',return_value=Mock(status_code=429,json=lambda:{'ok':False,'parameters':{'retry_after':120}})):
            self.assertEqual(enviar_parte(parte,'12345')['estado'],'fallido')
        for error in (requests.Timeout('TOKEN-PRIVADO'),requests.ConnectionError('TOKEN-PRIVADO')):
            with patch('Tiendas.entrega_reporte_rutas.requests.post',side_effect=error):
                self.assertEqual(enviar_parte(parte,'12345')['estado'],'incierto')
        with patch('Tiendas.entrega_reporte_rutas.requests.post',return_value=Mock(status_code=500,json=lambda:{'ok':False})):
            self.assertEqual(enviar_parte(parte,'12345')['estado'],'incierto')

    def test_detalle_pagina_fecha_y_corte_sin_modificar_finanzas(self):
        r=self.reporte()
        r.rutas[0]['creditos_detalle'] *= 25;r.save()
        before=list(Venta.objects.values())
        rutas=Tienda.objects.filter(pk__in=(self.ruta.pk,self.otra.pk))
        texto,teclado=detalle_reporte(self.admin,rutas,'12345',r.pk,self.ruta.pk,'s',1)
        self.assertIn('09/10/2026',texto)
        self.assertIn('página 2/4',texto)
        self.assertIn('Persona &lt;ficticia&gt; &amp;',texto)
        self.assertLess(longitud_visible(texto),3500)
        self.assertTrue(all(len(b['callback_data'].encode())<=64 for fila in teclado for b in fila))
        self.assertEqual(list(Venta.objects.values()),before)

    def test_detalle_ajeno_o_ruta_retirada_y_chat_ajeno_no_autorizados(self):
        r=self.reporte()
        rutas=Tienda.objects.filter(pk=self.ruta.pk)
        for usuario,chat,ruta in ((self.root,'12345',self.ruta.pk),(self.admin,'999',self.ruta.pk),
                (self.admin,'12345',self.otra.pk)):
            texto,teclado=detalle_reporte(usuario,rutas,chat,r.pk,ruta)
            self.assertIn('no autorizado',texto);self.assertEqual(teclado,[])
        self.assertIn('no autorizado',menu_reporte(self.admin,rutas,'999')[0])

    def test_callback_malformado_y_consulta_asistente_preservan_fecha(self):
        r=self.reporte()
        cb={'id':'cb-ficticio','message':{'chat':{'id':12345,'type':'private'}},
            'data':f'asst:rep:{r.pk}:{self.ruta.pk}:s:0'}
        with patch.object(telegram_assistant,'_enviar') as enviar,patch.object(telegram_assistant,'_responder_callback'):
            self.assertTrue(telegram_assistant.procesar_callback(cb))
            self.assertIn('09/10/2026',enviar.call_args.args[1])
            enviar.reset_mock();cb['data']='asst:rep:x:no:bad:bad'
            self.assertTrue(telegram_assistant.procesar_callback(cb));enviar.assert_not_called()
            cb['message']['chat']['id']=999
            self.assertFalse(telegram_assistant.procesar_callback(cb))

    def test_menu_paginado_y_boton_condicionado_a_mismo_chat(self):
        r=self.reporte(16)
        rutas=Tienda.objects.filter(administrador=self.admin)
        texto,teclado=menu_reporte(self.admin,rutas,'12345',r.pk,1)
        self.assertIn('página 2/2',texto)
        self.assertEqual(len(teclado),9)
        with override_settings(TELEGRAM_ASSISTANT_CHAT_ID='999'):
            r.delete();nuevo=self.reporte()
            self.assertNotIn('teclado',nuevo.partes[0])

    def test_reintento_anterior_conserva_foto_y_no_toca_inciertos(self):
        r=self.reporte()
        r.partes[0]['estado']='fallido';r.save()
        with patch('Tiendas.entrega_reporte_rutas.enviar_parte',return_value={'estado':'enviado','mensaje_id':'8','error':''}) as envio:
            reintentar_pendientes(self.admin,date(2026,10,10))
            self.assertEqual(envio.call_count,1)
        r.refresh_from_db();self.assertEqual(r.fecha,date(2026,10,9))

    def test_reportes_sin_cuotas_y_snapshots_decimal(self):
        filas=resumen_operativo(date(2026,10,9),rutas=Tienda.objects.filter(pk=self.otra.pk))
        r=preparar_reporte(self.admin,date(2026,10,9),filas)
        self.assertEqual(r.rutas[0]['esperado'],'0')
        self.assertIn('sin cuotas programadas',r.partes[0]['texto'])
