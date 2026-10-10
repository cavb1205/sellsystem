"""API de integración solo loopback, SQLite en memoria y personas ficticias.

Nunca importa variables.py ni settings de producción. La red está prohibida.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'sell_system.settings_pruebas_reportes'
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from rest_framework.test import APIClient
from Tiendas.test_suscripciones import SuscripcionesTests
from Tiendas.models import PagoMembresia, CambioSuscripcion, Tienda_Membresia
from Tiendas.serializers import TiendaMembresiaSerializer
from Ventas.test_reporte_historico import completar_esquema_legacy_de_pruebas

settings.ROOT_URLCONF = 'Tiendas.test_suscripciones'
assert connection.vendor == 'sqlite' and settings.DATABASES['default']['NAME'] == ':memory:'
connection.features.can_alter_table_drop_column = False
call_command('migrate', verbosity=0)
completar_esquema_legacy_de_pruebas()
caso = SuscripcionesTests()
caso.setUp()


class Handler(BaseHTTPRequestHandler):
    def ejecutar(self):
        if self.path == '/__pendiente' and self.command == 'POST':
            solicitud=caso.solicitud()
            self.send_response(201);self.end_headers()
            self.wfile.write(json.dumps({'codigo':solicitud.codigo}).encode());return
        if self.path == '/__estado':
            caso.ruta.refresh_from_db(); caso.venta.refresh_from_db(); caso.tm.refresh_from_db()
            data = {'version': caso.tm.version, 'manual': caso.tm.vencida_manualmente,
                'caja': str(caso.ruta.caja_inicial), 'saldo': str(caso.venta.saldo_actual),
                'clientes': caso.ruta.cliente_set.count(), 'recaudos': caso.ruta.recaudo_set.count(),
                'cambios': CambioSuscripcion.objects.count(),
                'pagos': list(PagoMembresia.objects.values('id','estado','origen'))}
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(data).encode()); return
        if self.path.startswith('/__sesion/'):
            role = self.path.rsplit('/',1)[-1]
            user = {'root': caso.root, 'admin': caso.admin, 'worker': caso.worker}[role]
            store = dict(TiendaMembresiaSerializer(Tienda_Membresia.objects.get(pk=caso.tm.pk)).data)
            data = {'user': {'id':user.pk,'username':user.username,'is_staff':user.is_staff,'is_superuser':user.is_superuser},
                'store': store, 'profile': {'id':1,'tienda':caso.ruta.pk}}
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(data,default=str).encode()); return
        if not self.path.startswith(('/tiendas/','/ventas/','/clientes/','/recaudos/','/aportes/','/gastos/','/utilidades/','/publicidad/','/trabajadores/')):
            self.send_error(404); return
        role = self.headers.get('X-Local-Role','root')
        api = APIClient(); api.force_authenticate({'root':caso.root,'admin':caso.admin,'worker':caso.worker}[role])
        body = self.rfile.read(int(self.headers.get('Content-Length','0')))
        response = api.generic(self.command,self.path,body,content_type=self.headers.get('Content-Type','application/json'))
        self.send_response(response.status_code)
        self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(response.content)
    do_GET = ejecutar
    do_POST = ejecutar
    do_PUT = ejecutar
    do_DELETE = ejecutar


print('Suscripciones: API aislada en 127.0.0.1:3041; SQLite en memoria; red externa prohibida.',flush=True)
HTTPServer(('127.0.0.1',3041),Handler).serve_forever()
