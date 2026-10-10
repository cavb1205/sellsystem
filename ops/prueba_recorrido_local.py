"""Servidor de prueba SOLO loopback, BD en memoria y clientes ficticios.

PYTHONPATH=/ruta/dependencias python3 ops/prueba_recorrido_local.py
No carga settings de producción, no permite red externa ni guarda archivos.
"""
import json
import os
import sys
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'sell_system.settings_pruebas_reportes'
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from Clientes.models import Cliente
from Tiendas.models import Tienda, OrdenVisita, CambioOrdenVisita
from Trabajadores.models import Perfil
from Ventas.models import Venta
from Ventas.test_reporte_historico import completar_esquema_legacy_de_pruebas

settings.ROOT_URLCONF = 'Tiendas.test_orden_visita'
connection.features.can_alter_table_drop_column = False
call_command('migrate', verbosity=0)
completar_esquema_legacy_de_pruebas()
admin = User.objects.create_user('admin-local', is_staff=True)
worker = User.objects.create_user('worker-local')
ruta = Tienda.objects.create(id=901, nombre='Ruta de prueba', administrador=admin)
Perfil.objects.create(trabajador=worker, tienda=ruta, identificacion='test',telefono='000')
for numero in range(1, 76):
    cliente = Cliente.objects.create(id=numero,tienda=ruta,identificacion=str(numero),
        nombres=f'Cliente {numero:02}',apellidos='Prueba',telefono_principal='000',direccion=f'Sector {numero%5} · Local {numero}')
    Venta.objects.create(id=numero,tienda=ruta,cliente=cliente,fecha_venta=date(2026,10,8),
        valor_venta=1000,interes=20,cuotas=4 if numero == 75 else 20,
        plazo='Semanal' if numero == 75 else 'Diario', calendario_version=1 if numero == 75 else 0,saldo_actual=1200)
Venta.objects.create(id=76,tienda=ruta,cliente_id=1,fecha_venta=date(2026,10,8),valor_venta=2000,interes=20,cuotas=20,saldo_actual=2400)


class Handler(BaseHTTPRequestHandler):
    def ejecutar(self):
        if self.path == '/__estado':
            datos = {'configuraciones':OrdenVisita.objects.count(),'cambios':CambioOrdenVisita.objects.count()}
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(datos).encode()); return
        if not (self.path.startswith('/tiendas/recorrido/') or self.path.startswith('/ventas/activas/liquidar/')):
            self.send_error(404); return
        api = APIClient()
        api.force_authenticate(worker if self.headers.get('X-Local-Role') == 'worker' else admin)
        cuerpo = self.rfile.read(int(self.headers.get('Content-Length','0')))
        response = api.generic(self.command,self.path,cuerpo,content_type='application/json')
        self.send_response(response.status_code)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store'); self.end_headers()
        self.wfile.write(response.content)
    do_GET = ejecutar
    do_PUT = ejecutar


with patch('Tiendas.fecha_operativa.timezone.now',return_value=datetime(2026,10,10,15,tzinfo=timezone.utc)), \
     patch('requests.sessions.Session.request',side_effect=AssertionError('Red externa prohibida')):
    print('API de prueba lista en 127.0.0.1:3040; 75 clientes ficticios; SQLite en memoria.',flush=True)
    HTTPServer(('127.0.0.1',3040),Handler).serve_forever()
