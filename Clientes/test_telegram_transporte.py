from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings

from Clientes.telegram_models import TelegramMensaje
from Clientes.telegram_transporte import (
    ResultadoBorrado, eliminar_mensaje, eliminar_mensajes, enviar_texto,
)


@override_settings(TELEGRAM_CLIENTES_ENABLED=True,
                   TELEGRAM_CLIENTES_DELIVERY_ENABLED=True,
                   TELEGRAM_CLIENTES_BOT_TOKEN='123:token_solo_pruebas',
                   TELEGRAM_BOT_TOKEN='456:administrador')
class TransporteTests(TestCase):
    def respuesta(self, status, datos):
        return Mock(status_code=status, json=Mock(return_value=datos))

    @patch('Clientes.telegram_transporte.requests.post')
    def test_exito_texto_plano_sin_url_interna(self, post):
        post.return_value = self.respuesta(200, {'ok': True, 'result': {
            'message_id': 42, 'chat': {'id': 123, 'type': 'private'}}})
        resultado = enviar_texto(123, 'Abono <sin HTML>')
        self.assertEqual(resultado.estado, 'enviado')
        self.assertEqual(resultado.mensaje_id, 42)
        self.assertTrue(TelegramMensaje.objects.filter(
            chat_id=123, mensaje_id=42, direccion='saliente',
        ).exists())
        post.assert_called_once()
        self.assertNotIn('parse_mode', post.call_args.kwargs['json'])
        self.assertFalse(post.call_args.kwargs['allow_redirects'])

    @patch('Clientes.telegram_transporte.requests.post')
    def test_timeout_no_reintenta_ni_expone_token(self, post):
        post.side_effect = requests.Timeout('URL con token privado')
        resultado = enviar_texto(123, 'Abono')
        self.assertEqual(resultado.estado, 'incierto')
        self.assertNotIn('privado', str(resultado))
        post.assert_called_once()

    @patch('Clientes.telegram_transporte.requests.post')
    def test_limite_respeta_retry_after(self, post):
        post.return_value = self.respuesta(429, {'ok': False, 'error_code': 429,
            'parameters': {'retry_after': 35}})
        resultado = enviar_texto(123, 'Abono')
        self.assertEqual((resultado.estado, resultado.reintentar_en), ('pendiente', 35))

    @patch('Clientes.telegram_transporte.requests.post')
    def test_bloqueo_cliente_y_error_servidor(self, post):
        for status, esperado in [(403, 'fallido'), (500, 'incierto')]:
            post.return_value = self.respuesta(status, {'ok': False, 'error_code': status})
            self.assertEqual(enviar_texto(123, 'Abono').estado, esperado)

    @patch('Clientes.telegram_transporte.requests.post')
    def test_destinatario_distinto_no_confirma(self, post):
        post.return_value = self.respuesta(200, {'ok': True, 'result': {
            'message_id': 42, 'chat': {'id': 999, 'type': 'private'}}})
        self.assertEqual(enviar_texto(123, 'Abono').estado, 'incierto')

    @patch('Clientes.telegram_transporte.requests.post')
    def test_configuracion_y_destinos_invalidos_no_usan_red(self, post):
        with override_settings(TELEGRAM_CLIENTES_BOT_TOKEN='456:administrador'):
            self.assertEqual(enviar_texto(123, 'Abono').estado, 'fallido')
        for chat in (-1, True, '123'):
            self.assertEqual(enviar_texto(chat, 'Abono').estado, 'fallido')
        self.assertEqual(enviar_texto(123, 'x' * 4097).estado, 'fallido')
        post.assert_not_called()

    @patch('Clientes.telegram_transporte.requests.post')
    def test_borrar_mensaje_usa_solo_bot_clientes_y_mensaje_privado(self, post):
        post.return_value = self.respuesta(200, {'ok': True, 'result': True})
        resultado = eliminar_mensaje(123, 42)
        self.assertEqual(resultado, ResultadoBorrado('borrado'))
        self.assertTrue(post.call_args.args[0].endswith('/deleteMessage'))
        self.assertEqual(post.call_args.kwargs['json'], {'chat_id': 123, 'message_id': 42})
        self.assertFalse(post.call_args.kwargs['allow_redirects'])

    @patch('Clientes.telegram_transporte.requests.post')
    def test_delete_429_reagenda_y_error_de_mensaje_ya_borrado_es_idempotente(self, post):
        post.return_value = self.respuesta(429, {'ok': False, 'error_code': 429,
            'parameters': {'retry_after': 120}})
        self.assertEqual(eliminar_mensaje(123, 42),
                         ResultadoBorrado('pendiente', reintentar_en=120))
        post.return_value = self.respuesta(400, {'ok': False, 'error_code': 400,
            'description': 'Bad Request: message to delete not found'})
        self.assertEqual(eliminar_mensaje(123, 42), ResultadoBorrado('borrado'))

    @patch('Clientes.telegram_transporte.requests.post')
    def test_borrado_por_lote_valida_hasta_cien_ids(self, post):
        post.return_value = self.respuesta(200, {'ok': True, 'result': True})
        resultado = eliminar_mensajes(123, [41, 42])
        self.assertEqual(resultado, ResultadoBorrado('borrado'))
        self.assertTrue(post.call_args.args[0].endswith('/deleteMessages'))
        self.assertEqual(post.call_args.kwargs['json'], {
            'chat_id': 123, 'message_ids': [41, 42],
        })
        for invalido in ([], [0], list(range(1, 102))):
            self.assertEqual(eliminar_mensajes(123, invalido).estado, 'fallido')
        self.assertEqual(post.call_count, 1)

    @override_settings(TELEGRAM_CLIENTES_DELIVERY_ENABLED=False)
    @patch('Clientes.telegram_transporte.requests.post')
    def test_desactivado_no_usa_red(self, post):
        self.assertEqual(enviar_texto(123, 'Abono').estado, 'omitido')
        post.assert_not_called()
