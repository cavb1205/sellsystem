"""Pruebas aisladas: nunca importa variables ni conexiones de producción."""
SECRET_KEY = 'solo-pruebas-locales-no-utilizar-en-produccion'
DEBUG = False
ALLOWED_HOSTS = ['testserver', '127.0.0.1']
INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
    'rest_framework', 'Tiendas', 'Clientes', 'Trabajadores', 'Aportes',
    'Gastos', 'Utilidades', 'Ventas', 'Recaudos', 'Publicidad',
]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
MIDDLEWARE = []
ROOT_URLCONF = 'Ventas.test_reporte_historico'
TEST_RUNNER = 'Ventas.test_reporte_historico.ReportesTestRunner'
TIME_ZONE = 'America/Santiago'
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
}
TELEGRAM_CLIENTES_ENABLED = False
TELEGRAM_CLIENTES_DELIVERY_ENABLED = False
TELEGRAM_ALERTAS_USERNAME = ''
TELEGRAM_BOT_TOKEN = ''
TELEGRAM_ADMIN_CHAT_ID = ''
