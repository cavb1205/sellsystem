"""La allowlist de nginx (ops/nginx/django-api.conf) cubre todas las rutas de la API."""
import re
from pathlib import Path
from django.test import SimpleTestCase

RAIZ = Path(__file__).resolve().parent.parent
CONF = RAIZ / 'ops' / 'nginx' / 'django-api.conf'
URLS = RAIZ / 'sell_system' / 'urls.py'


class AllowlistNginxTests(SimpleTestCase):
    def setUp(self):
        texto = CONF.read_text()
        self.allowlist = re.compile(re.search(r'location ~ (\^/\S+)', texto).group(1))
        self.texto = texto

    def test_prefijos_de_urls_pasan_a_django(self):
        # Lectura estática: los settings de prueba no cargan django.contrib.admin.
        rutas = ['/' + r for r in re.findall(r"^\s*path\('([^']*)'", URLS.read_text(), re.M)]
        self.assertGreater(len(rutas), 10)
        faltan = []
        for ruta in rutas:
            if ruta in ('/admin/', '/telegram/clientes/webhook/'):
                continue
            if not self.allowlist.match(ruta):
                faltan.append(ruta)
        self.assertEqual(faltan, [], 'Agregar a la allowlist de ops/nginx/django-api.conf')

    def test_rutas_especiales(self):
        self.assertIn('location ^~ /admin/', self.texto)
        self.assertIn('auth_basic_user_file', self.texto)
        self.assertIn('include /etc/nginx/snippets/telegram-clientes-location.conf;', self.texto)
        for ruta in ('/wp-admin/', '/.env', '/api/', '/', '/media/x'):
            self.assertIsNone(self.allowlist.match(ruta), ruta)
