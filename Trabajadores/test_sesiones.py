"""Sesiones reales JWT sobre cuentas ficticias y SQLite desechable."""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

from Clientes import views as clientes_views
from Tiendas import test_suscripciones as fixtures
from Tiendas.models import MovimientoCaja
from Trabajadores.models import Perfil, AsignacionTrabajador
from Trabajadores.token_views import TokenObtainSeguroView, TokenRefreshSeguroView
from Trabajadores.views import Login


@api_view(['GET'])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def protegido(request):
    return Response({'usuario_id': request.user.pk})


urlpatterns = [*fixtures.urlpatterns,
    path('login/', Login.as_view()),
    path('token/', TokenObtainSeguroView.as_view()),
    path('token/refresh/', TokenRefreshSeguroView.as_view()),
    path('privado/', protegido),
]


@override_settings(ROOT_URLCONF=__name__)
class SesionesTests(TestCase):
    def setUp(self):
        fixtures.SuscripcionesTests.setUp(self)
        cache.clear()
        self.addCleanup(cache.clear)
        self.worker.set_password('Clave-Ficticia-Inicial-2026!')
        self.worker.save()
        Perfil.objects.create(trabajador=self.admin, tienda=self.ruta)
        self.api = APIClient()

    def login(self, path='/login/'):
        response = self.api.post(path, {'username': self.worker.username,
            'password': 'Clave-Ficticia-Inicial-2026!'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def privado(self, access):
        self.api.force_authenticate(None)
        self.api.credentials(HTTP_AUTHORIZATION='Bearer '+access)
        return self.api.get('/privado/')

    def refresh(self, token):
        self.api.force_authenticate(None)
        self.api.credentials()
        return self.api.post('/token/refresh/', {'refresh': str(token)}, format='json')

    def test_login_y_token_emiten_sesiones_validas_sin_hash_de_password_del_usuario(self):
        for endpoint in ('/login/', '/token/'):
            data = self.login(endpoint)
            self.assertNotIn('password', data.get('user', {}))
            self.assertEqual(self.privado(data.get('token', data.get('access'))).status_code, 200)
            self.assertEqual(self.refresh(data['refresh']).status_code, 200)

    def test_cambio_de_password_revoca_acceso_y_refresh_anteriores(self):
        data = self.login()
        self.assertEqual(self.privado(data['token']).status_code, 200)
        self.worker.set_password('Clave-Ficticia-Cambiada-2026!')
        self.worker.save()
        self.assertEqual(self.privado(data['token']).status_code, 401)
        response = self.refresh(data['refresh'])
        self.assertEqual(response.status_code, 401, response.data)
        self.assertEqual(response.data['detail'].code, 'password_changed')
        nuevo = RefreshToken.for_user(self.worker)
        self.assertEqual(self.privado(str(nuevo.access_token)).status_code, 200)
        self.assertEqual(self.refresh(nuevo).status_code, 200)

    def test_restablecimiento_desde_endpoint_admin_revoca_sesion_del_worker(self):
        data = self.login()
        self.api.force_authenticate(self.admin)
        response = self.api.post(f'/trabajadores/password/{self.worker.perfil.pk}/',
            {'passwordNuevo':'Clave-Nueva-Por-Administrador-2026!'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.privado(data['token']).status_code, 401)
        self.assertEqual(self.refresh(data['refresh']).status_code, 401)

    def test_sesion_anterior_sin_huella_no_puede_renovarse_ni_usarse(self):
        token = RefreshToken.for_user(self.worker)
        del token['hash_password']
        self.assertEqual(self.privado(str(token.access_token)).status_code, 401)
        self.assertEqual(self.refresh(token).status_code, 401)

    def test_usuario_desactivado_no_puede_usar_ni_renovar_sesion(self):
        data = self.login()
        self.worker.is_active = False
        self.worker.save()
        self.assertEqual(self.privado(data['token']).status_code, 401)
        self.assertEqual(self.refresh(data['refresh']).status_code, 401)

    def test_usuario_eliminado_o_desconocido_devuelve_401_sin_error_500(self):
        token = RefreshToken.for_user(self.worker)
        self.worker.delete()
        self.assertEqual(self.privado(str(token.access_token)).status_code, 401)
        self.assertEqual(self.refresh(token).status_code, 401)

    def test_refresh_rechaza_usuario_invalido_o_ausente(self):
        for user_id in (None, [], True, 'no-es-un-id', 9999999):
            token = RefreshToken.for_user(self.worker)
            if user_id is None: del token['user_id']
            else: token['user_id'] = user_id
            self.assertEqual(self.refresh(token).status_code, 401)

    def test_token_malformado_expirado_o_access_usado_como_refresh_no_se_acepta(self):
        token = RefreshToken.for_user(self.worker)
        self.assertEqual(self.refresh(token.access_token).status_code, 401)
        token.set_exp(lifetime=timedelta(seconds=-1))
        for value in (token, 'token-ficticio-invalido'):
            self.assertEqual(self.refresh(value).status_code, 401)

    def test_login_y_token_no_omiten_bloqueo_de_ruta_inactiva(self):
        self.ruta.estado = False
        self.ruta.save()
        for endpoint in ('/login/', '/token/'):
            response = self.api.post(endpoint, {'username':self.worker.username,
                'password':'Clave-Ficticia-Inicial-2026!'}, format='json')
            self.assertEqual(response.status_code, 403, response.data)

    def test_retirar_asignacion_impide_renovar_y_operar_por_endpoint_implicito(self):
        token = RefreshToken.for_user(self.worker)
        AsignacionTrabajador.objects.create(perfil=self.worker.perfil, tienda=self.ruta, activa=False)
        self.assertEqual(self.refresh(token).status_code, 403)
        self.api.credentials(HTTP_AUTHORIZATION='Bearer '+str(token.access_token))
        with patch.object(clientes_views.list_clientes.cls, 'authentication_classes', [JWTAuthentication]):
            response = self.api.get('/clientes/')
        self.assertEqual(response.status_code, 403, response.data)

    def test_ruta_secundaria_activa_no_rehabilita_la_principal_retirada(self):
        token = RefreshToken.for_user(self.worker)
        AsignacionTrabajador.objects.create(perfil=self.worker.perfil, tienda=self.ruta, activa=False)
        AsignacionTrabajador.objects.create(perfil=self.worker.perfil, tienda=self.otra, activa=True)
        self.assertEqual(self.refresh(token).status_code, 200)
        self.api.credentials(HTTP_AUTHORIZATION='Bearer '+str(token.access_token))
        with patch.object(clientes_views.list_clientes.cls, 'authentication_classes', [JWTAuthentication]):
            self.assertEqual(self.api.get('/clientes/').status_code, 403)
            self.assertEqual(self.api.get(f'/clientes/tienda/{self.otra.pk}/').status_code, 200)

    def test_sesion_legitima_conserva_tiempos_y_operacion_del_cliente(self):
        token = RefreshToken.for_user(self.worker)
        self.assertEqual(token['exp']-token['iat'], 3600)
        self.assertEqual(token.access_token['exp']-token.access_token['iat'], 3600)
        for _ in range(3):
            response = self.refresh(token)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertNotIn('refresh', response.data)
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_login_y_token_comparten_limite_de_intentos(self):
        for number in range(20):
            endpoint = '/login/' if number%2 else '/token/'
            response = self.api.post(endpoint, {'username':'cuenta-ficticia-ausente',
                'password':'Clave-Incorrecta-2026!'}, format='json')
            self.assertIn(response.status_code, (400,401))
        self.assertEqual(self.api.post('/token/', {'username':'x','password':'y'}, format='json').status_code, 429)

    def test_validacion_de_credenciales_no_escribe_movimientos_financieros(self):
        self.login()
        self.ruta.refresh_from_db()
        self.venta.refresh_from_db()
        self.assertEqual(self.ruta.caja_inicial, 10000)
        self.assertEqual(self.venta.saldo_actual, 1100)
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_password_de_tipo_invalido_no_produce_500_ni_modifica_clave(self):
        before=self.worker.password
        self.api.force_authenticate(self.admin)
        for value in [None,True,12345678,[],{},'corta']:
            response=self.api.post(f'/trabajadores/password/{self.worker.perfil.pk}/',
                {'passwordNuevo':value},format='json')
            self.assertEqual(response.status_code,400)
            self.worker.refresh_from_db()
            self.assertEqual(self.worker.password,before)
