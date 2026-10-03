from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from Clientes import test_telegram_vinculacion as fixtures
from Clientes.telegram_views import invitacion, vinculacion


class TelegramEndpointsTests(TestCase):
    setUp = fixtures.VinculacionTests.setUp
    vincular = fixtures.VinculacionTests.vincular
    def request(self, view, method='get', usuario=None, data=None):
        request = getattr(APIRequestFactory(), method)('/', data=data or {}, format='json')
        if usuario:
            force_authenticate(request, user=usuario)
        return view(request, pk=self.clientes[0].pk)

    def test_deshabilitado_por_defecto(self):
        response = self.request(invitacion, 'post', self.admin)
        self.assertEqual(response.status_code, 503)

    @override_settings(TELEGRAM_CLIENTES_ENABLED=True,
                       TELEGRAM_CLIENTES_BOT_USERNAME='clientes_prueba_bot')
    def test_endpoints_restringidos_y_enlace_sin_url_interna(self):
        self.assertIn(self.request(invitacion, 'post').status_code, (401, 403))
        self.assertEqual(self.request(invitacion, 'post', self.otro).status_code, 403)
        response = self.request(invitacion, 'post', self.admin)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['enlace'].startswith('https://t.me/clientes_prueba_bot?start='))
        self.assertEqual(response['Cache-Control'], 'no-store')

    @override_settings(TELEGRAM_CLIENTES_ENABLED=True)
    def test_confirmacion_valida_entrada(self):
        response = self.request(vinculacion, 'post', self.admin,
                                {'chat_id': -1, 'version': 'invalida'})
        self.assertEqual(response.status_code, 400)
        response = self.request(vinculacion, 'post', self.admin,
                                {'chat_id': 12345, 'version': 1})
        self.assertEqual(response.status_code, 409)

    @override_settings(TELEGRAM_CLIENTES_ENABLED=True)
    def test_estado_y_revocacion(self):
        self.vincular(self.clientes[0])
        response = self.request(vinculacion, usuario=self.admin)
        self.assertEqual(response.data['estado'], 'activo')
        response = self.request(vinculacion, 'delete', self.admin)
        self.assertEqual(response.status_code, 204)
        response = self.request(vinculacion, usuario=self.admin)
        self.assertEqual(response.data['estado'], 'sin_vincular')
