from datetime import timedelta
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from Clientes import test_telegram_vinculacion as fixtures
from Clientes.telegram_views import invitacion, vinculacion
from Clientes.telegram_models import TelegramInvitacion
from Clientes.telegram_vinculacion import aceptar_invitacion, cliente_autorizado, crear_invitacion, invitacion_pendiente


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
        self.assertIsInstance(response.data['invitacion_id'], int)

    @override_settings(TELEGRAM_CLIENTES_ENABLED=True, TELEGRAM_CLIENTES_BOT_USERNAME='clientes_prueba_bot')
    def test_estado_muestra_vigencia_sin_revelar_token(self):
        token, expira = crear_invitacion(self.clientes[0].pk, self.admin)
        response = self.request(vinculacion, usuario=self.admin)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response.data['invitacion']['expira_en'], expira)
        self.assertNotIn(token, str(response.data))
        self.assertNotIn('token_hash', str(response.data))
        TelegramInvitacion.objects.update(expira_en=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(self.request(vinculacion, usuario=self.admin).data['invitacion'])

    @override_settings(TELEGRAM_CLIENTES_ENABLED=True)
    def test_cancelar_invita_correcta_sin_cancelar_otra_ficha(self):
        token, _ = crear_invitacion(self.clientes[0].pk, self.admin)
        otro_token, _ = crear_invitacion(self.clientes[1].pk, self.admin)
        invitacion_id = invitacion_pendiente(token).pk
        self.assertEqual(self.request(invitacion, 'delete', self.otro, {'invitacion_id': invitacion_id}).status_code, 403)
        self.assertEqual(self.request(invitacion, 'delete', self.admin, {}).status_code, 400)
        response = self.request(invitacion, 'delete', self.admin, {'invitacion_id': invitacion_id})
        self.assertEqual(response.status_code, 204)
        with self.assertRaises(ValidationError):
            aceptar_invitacion(token, 12345, True)
        self.assertIsNotNone(invitacion_pendiente(otro_token))

    @override_settings(TELEGRAM_CLIENTES_ENABLED=True)
    def test_cancelacion_antigua_no_cancela_invita_nueva(self):
        viejo, _ = crear_invitacion(self.clientes[0].pk, self.admin)
        viejo_id = invitacion_pendiente(viejo).pk
        nuevo, _ = crear_invitacion(self.clientes[0].pk, self.admin)
        self.request(invitacion, 'delete', self.admin, {'invitacion_id': viejo_id})
        self.assertIsNotNone(invitacion_pendiente(nuevo))
        self.assertIsNone(invitacion_pendiente(viejo))

    @override_settings(TELEGRAM_CLIENTES_ENABLED=True)
    def test_cancelar_invita_ya_aceptada_no_revoca_vinculo(self):
        token, _ = crear_invitacion(self.clientes[0].pk, self.admin)
        invitacion_id = invitacion_pendiente(token).pk
        aceptar_invitacion(token, 12345, True)
        self.request(invitacion, 'delete', self.admin, {'invitacion_id': invitacion_id})
        self.assertEqual(cliente_autorizado(12345, self.clientes[0].pk), self.clientes[0])
        self.assertEqual(self.request(vinculacion, usuario=self.admin).data['estado'], 'activo')

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
