import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

from django.http import HttpResponse
from django.test import SimpleTestCase, RequestFactory, override_settings
from corsheaders.middleware import CorsMiddleware


class SecuritySettingsTests(SimpleTestCase):
    def configuracion(self):
        # Nunca importa variables.py ni utiliza credenciales o bases reales.
        variables = types.ModuleType('sell_system.variables')
        variables.KEY = 'clave-ficticia-para-comprobar-configuracion-local-2026'
        variables.JWT_SIGNING_KEY = 'clave-jwt-ficticia-separada-para-comprobar-configuracion-2026'
        variables.NAME_BD = variables.USER_BD = variables.PASSWORD_BD = 'solo-pruebas'
        source = Path(__file__).resolve().parents[1]/'sell_system/settings.py'
        spec = importlib.util.spec_from_file_location('sell_system.settings_ficticias', source)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'sell_system.variables':variables}):
            spec.loader.exec_module(module)
        return module

    def test_configuracion_activa_revocacion_y_cookies_seguras(self):
        config = self.configuracion()
        self.assertFalse(config.DEBUG)
        self.assertTrue(config.SIMPLE_JWT['CHECK_REVOKE_TOKEN'])
        self.assertTrue(config.SIMPLE_JWT['CHECK_USER_IS_ACTIVE'])
        self.assertEqual(config.SIMPLE_JWT['TOKEN_REFRESH_SERIALIZER'],
                         'Trabajadores.sesiones.TokenRefreshSeguroSerializer')
        self.assertTrue(config.SESSION_COOKIE_SECURE)
        self.assertTrue(config.CSRF_COOKIE_SECURE)
        self.assertNotEqual(config.SIMPLE_JWT['SIGNING_KEY'],config.SECRET_KEY)
        self.assertFalse(any('*' in origin for origin in config.CSRF_TRUSTED_ORIGINS))

    def test_cors_acepta_produccion_y_previews_propias_rechaza_origenes_ajenos(self):
        config = self.configuracion()
        with override_settings(CORS_ALLOWED_ORIGINS=config.CORS_ALLOWED_ORIGINS,
                               CORS_ALLOWED_ORIGIN_REGEXES=config.CORS_ALLOWED_ORIGIN_REGEXES,
                               CORS_ALLOW_ALL_ORIGINS=False):
            middleware = CorsMiddleware(lambda request:HttpResponse('contenido ficticio'))
            for origin in [*config.CORS_ALLOWED_ORIGINS,
                           'https://gestion-financiera-abcdef123-cavb1205s-projects.vercel.app']:
                request = RequestFactory().get('/', HTTP_ORIGIN=origin)
                self.assertEqual(middleware(request).get('Access-Control-Allow-Origin'), origin)
            for origin in ['https://otra-app.vercel.app',
                           'https://gestion-financiera-abc-otro-equipo.vercel.app',
                           'https://gestion-financiera-abc-cavb1205s-projects.vercel.app.ejemplo.invalid',
                           'http://app.carterafinanciera.com']:
                request = RequestFactory().get('/', HTTP_ORIGIN=origin)
                self.assertIsNone(middleware(request).get('Access-Control-Allow-Origin'))
