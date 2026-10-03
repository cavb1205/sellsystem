from datetime import date
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from Clientes import test_telegram_vinculacion as fixtures
from Clientes.telegram_models import TelegramMensaje, TelegramVinculo
from Clientes.telegram_vinculacion import cliente_autorizado, crear_invitacion
from Clientes.telegram_webhook import webhook
from Ventas.models import Venta


@override_settings(
    TELEGRAM_CLIENTES_ENABLED=True,
    TELEGRAM_CLIENTES_DELIVERY_ENABLED=True,
    TELEGRAM_CLIENTES_WEBHOOK_SECRET='secret-prueba',
    TELEGRAM_CLIENTES_BOT_TOKEN='123:bot-clientes',
    TELEGRAM_BOT_TOKEN='456:bot-admin',
)
class WebhookTests(TestCase):
    def setUp(self):
        fixtures.VinculacionTests.setUp(self)
        self.token, _ = crear_invitacion(self.clientes[1].pk, self.admin)
        self.factory = APIRequestFactory()

    def request(self, update, secret='secret-prueba'):
        return self.factory.post(
            '/telegram/clientes/webhook/', update, format='json',
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret,
        )

    @staticmethod
    def ok_response(message_id, chat_id):
        response = Mock(status_code=200)
        response.json.return_value = {
            'ok': True,
            'result': {'message_id': message_id, 'chat': {'id': chat_id, 'type': 'private'}},
        }
        return response

    @patch('Clientes.telegram_webhook.requests', create=True)
    def test_secreto_incorrecto_no_procesa_ni_usa_red(self, requests):
        response = webhook(self.request({'message': {'text': '/start ' + self.token,
            'chat': {'id': 77, 'type': 'private'}}}, secret='incorrecto'))
        self.assertEqual(response.status_code, 403)
        requests.post.assert_not_called()

    @patch('Clientes.telegram_transporte.requests.post')
    def test_start_valido_solicita_consentimiento_sin_datos_financieros(self, post):
        post.return_value = self.ok_response(10, 77)
        response = webhook(self.request({'message': {'text': '/start ' + self.token,
            'chat': {'id': 77, 'type': 'private'}}}))
        self.assertEqual(response.status_code, 200)
        payload = post.call_args.kwargs['json']
        self.assertIn('reply_markup', payload)
        self.assertNotIn('saldo', payload['text'].lower())
        self.assertFalse(TelegramVinculo.objects.filter(cliente=self.clientes[1]).exists())

    @patch('Clientes.telegram_transporte.requests.post')
    def test_grupo_no_recibe_consentimiento(self, post):
        response = webhook(self.request({'message': {'text': '/start ' + self.token,
            'chat': {'id': -77, 'type': 'group'}}}))
        self.assertEqual(response.status_code, 200)
        post.assert_not_called()

    @patch('Clientes.telegram_transporte.requests.post')
    def test_aceptar_activa_vinculo_sin_confirmacion_manual(self, post):
        post.side_effect = [self.ok_response(11, 77), self.ok_response(12, 77)]
        response = webhook(self.request({'callback_query': {
            'id': 'callback-1',
            'data': 'tgcliente:aceptar:' + self.token,
            'message': {'chat': {'id': 77, 'type': 'private'}},
        }}))
        self.assertEqual(response.status_code, 200)
        vinculo = TelegramVinculo.objects.get(cliente=self.clientes[1])
        self.assertTrue(vinculo.activo)
        self.assertIsNotNone(vinculo.consentimiento_en)
        self.assertIsNotNone(vinculo.confirmado_en)
        self.assertIsNone(vinculo.confirmado_por_id)
        self.assertEqual(vinculo.chat_id, 77)
        self.assertEqual(cliente_autorizado(77, self.clientes[1].pk), self.clientes[1])
        self.assertIn('vinculado correctamente', post.call_args_list[-1].kwargs['json']['text'].lower())
        for call in post.call_args_list:
            self.assertNotIn('saldo', str(call.kwargs.get('json', {})).lower())

    @patch('Clientes.telegram_transporte.requests.post')
    def test_rechazar_no_crea_vinculo(self, post):
        post.side_effect = [self.ok_response(11, 77), self.ok_response(12, 77)]
        response = webhook(self.request({'callback_query': {
            'id': 'callback-2',
            'data': 'tgcliente:rechazar:' + self.token,
            'message': {'chat': {'id': 77, 'type': 'private'}},
        }}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TelegramVinculo.objects.filter(cliente=self.clientes[1]).exists())

    @patch('Clientes.telegram_transporte.requests.post')
    def test_token_consumido_no_se_acepta_de_nuevo(self, post):
        post.return_value = self.ok_response(13, 77)
        webhook(self.request({'callback_query': {
            'id': 'callback-3', 'data': 'tgcliente:aceptar:' + self.token,
            'message': {'chat': {'id': 77, 'type': 'private'}},
        }}))
        post.reset_mock()
        response = webhook(self.request({'message': {'text': '/start ' + self.token,
            'chat': {'id': 77, 'type': 'private'}}}))
        self.assertEqual(response.status_code, 200)
        post.assert_called_once()
        self.assertNotIn('reply_markup', post.call_args.kwargs['json'])

    @patch('Clientes.telegram_transporte.requests.post')
    def test_menu_muestra_credito_y_primer_nombre_sin_ruta(self, post):
        fixtures.VinculacionTests.vincular(self, self.clientes[0], chat=77)
        Venta.objects.create(
            cliente=self.clientes[0], tienda=self.clientes[0].tienda,
            fecha_venta=date(2026, 9, 22), valor_venta=100,
            interes=20, cuotas=20, saldo_actual=120,
        )
        post.return_value = self.ok_response(14, 77)
        response = webhook(self.request({'message': {
            'message_id': 55, 'text': '/creditos',
            'chat': {'id': 77, 'type': 'private'},
        }}))
        self.assertEqual(response.status_code, 200)
        payload = post.call_args.kwargs['json']
        self.assertIn('Camilo', payload['text'])
        self.assertIn('Crédito #', payload['text'])
        self.assertNotIn('Ruta:', payload['text'])
        self.assertIn('reply_markup', payload)
        self.assertIn('tgmenu:credito:', str(payload['reply_markup']))
        self.assertTrue(TelegramMensaje.objects.filter(
            chat_id=77, mensaje_id=55, direccion='entrante',
        ).exists())
        self.assertTrue(TelegramMensaje.objects.filter(
            chat_id=77, mensaje_id=14, direccion='saliente',
        ).exists())

    @patch('Clientes.telegram_transporte.requests.post')
    def test_boton_de_credito_consulta_detalle_vinculado(self, post):
        fixtures.VinculacionTests.vincular(self, self.clientes[0], chat=77)
        venta = Venta.objects.create(
            cliente=self.clientes[0], tienda=self.clientes[0].tienda,
            fecha_venta=date(2026, 9, 22), valor_venta=100,
            interes=20, cuotas=20, saldo_actual=120,
        )
        post.side_effect = [self.ok_response(15, 77), self.ok_response(16, 77)]
        response = webhook(self.request({'callback_query': {
            'id': 'callback-menu-1',
            'data': f'tgmenu:credito:{venta.pk}',
            'message': {'chat': {'id': 77, 'type': 'private'}},
        }}))
        self.assertEqual(response.status_code, 200)
        payload = post.call_args_list[-1].kwargs['json']
        self.assertIn('Cliente: Camilo', payload['text'])
        self.assertIn('Cuotas cubiertas: 0 de 20', payload['text'])
        self.assertNotIn('Ruta:', payload['text'])

    @override_settings(TELEGRAM_CLIENTES_DELIVERY_ENABLED=False)
    @patch('Clientes.telegram_transporte.requests.post')
    def test_bandera_apagada_no_procesa(self, post):
        response = webhook(self.request({'message': {'text': '/start ' + self.token,
            'chat': {'id': 77, 'type': 'private'}}}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['procesado'])
        post.assert_not_called()
