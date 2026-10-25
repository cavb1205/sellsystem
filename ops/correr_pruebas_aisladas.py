"""Ejecuta pruebas sin variables.py, sin PostgreSQL y sin conexiones de red."""
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path.insert(0,str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE']='sell_system.settings_pruebas_reportes'
import django
with patch('socket.socket.connect',side_effect=AssertionError('Red externa prohibida en pruebas aisladas')), \
     patch('socket.getaddrinfo',side_effect=AssertionError('DNS externo prohibido en pruebas aisladas')):
    django.setup()
    from django.conf import settings
    from django.test.utils import get_runner
    assert settings.DATABASES['default']['ENGINE']=='django.db.backends.sqlite3'
    assert settings.DATABASES['default']['NAME']==':memory:'
    failures=get_runner(settings)(verbosity=1).run_tests(sys.argv[1:])
raise SystemExit(bool(failures))
