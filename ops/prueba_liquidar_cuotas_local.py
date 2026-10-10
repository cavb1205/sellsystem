"""Verificación aislada: Django real, SQLite en memoria, red externa prohibida."""
import os
import sys
import json
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'sell_system.settings_pruebas_reportes'
import django
django.setup()
from django.core.management import call_command
from django.db import connection
from django.contrib.auth.models import User
from django.urls import include, path
from rest_framework.test import APIClient
from Clientes.models import Cliente
from Tiendas.models import Tienda, MovimientoCaja
from Ventas.models import Venta
from Recaudos.models import Recaudo, Visita_Blanco
from Ventas import test_reporte_historico as urls

connection.features.can_alter_table_drop_column = False
call_command('migrate', verbosity=0, interactive=False)
urls.completar_esquema_legacy_de_pruebas()
urls.urlpatterns += [path('ventas/', include('Ventas.urls')),
                     path('clientes/', include('Clientes.urls')),
                     path('recaudos/', include('Recaudos.urls'))]
admin = User.objects.create_user('administrador-local', is_staff=True)
ruta = Tienda.objects.create(nombre='Calendario de prueba local', administrador=admin,
                            zona_horaria='America/Santiago', caja_inicial=10000000)
clientes = {}
for nombre in ['Pendiente', 'Adelantado', 'No corresponde', 'Mensual', 'Nuevo']:
    clientes[nombre] = Cliente.objects.create(tienda=ruta, identificacion='local-'+nombre,
        nombres=nombre, apellidos='Prueba', telefono_principal='000', direccion='Local')
for nombre in ['Pendiente', 'Adelantado', 'No corresponde', 'Mensual']:
    mensual = nombre == 'Mensual'
    venta = Venta.objects.create(tienda=ruta, cliente=clientes[nombre],
        fecha_venta=date(2026,10,14 if nombre == 'No corresponde' else 8),
        valor_venta=1000000, interes=20, cuotas=1 if mensual else 4,
        plazo='Mensual' if mensual else 'Semanal', calendario_version=1,
        fecha_vencimiento=date(2026,11,8 if mensual else 11 if nombre == 'No corresponde' else 5),
        saldo_actual=600000 if nombre == 'Adelantado' else 1200000)
    if nombre == 'Adelantado':
        Recaudo.objects.create(tienda=ruta, venta=venta, fecha_recaudo=date(2026,10,14), valor_recaudo=600000)
api = APIClient()
api.force_authenticate(admin)
store = {'tienda': {'id': ruta.pk, 'nombre': ruta.nombre, 'zona_horaria': ruta.zona_horaria},
         'fecha_vencimiento': '2099-12-31', 'estado': 'Activa'}
ahora = datetime(2026,10,15,16,tzinfo=timezone.utc)

class Handler(BaseHTTPRequestHandler):
    def responder(self, status, data):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/pruebas/contexto':
            return self.responder(200, {'store': store, 'clienteId': clientes['Nuevo'].pk,
                                       'django': django.get_version()})
        if self.path == '/pruebas/evidencia':
            return self.responder(200, {'caja': str(Tienda.objects.get(pk=ruta.pk).caja_inicial),
                'ventas': list(Venta.objects.values('id','plazo','cuotas','calendario_version','saldo_actual','fecha_vencimiento','estado_venta')),
                'recaudos': Recaudo.objects.count(), 'fallas': Visita_Blanco.objects.count(),
                'movimientos': MovimientoCaja.objects.count()})
        if self.path.startswith(f'/tiendas/detail/admin/{ruta.pk}/'):
            return self.responder(200, {**store, 'version': 0, 'vencida_manualmente': False, 'caja_inicial': str(Tienda.objects.get(pk=ruta.pk).caja_inicial)})
        with patch('Tiendas.fecha_operativa.timezone.now', return_value=ahora):
            response = api.get(self.path)
        return self.responder(response.status_code, getattr(response, 'data', {'message':'Fuera de la prueba'}))

    def do_POST(self):
        if self.path not in (f'/ventas/create/t/{ruta.pk}/',f'/recaudos/create/t/{ruta.pk}/'):
            return self.responder(403, {'message':'Mutación fuera de alcance'})
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))))
        with patch('Tiendas.fecha_operativa.timezone.now', return_value=ahora):
            response = api.post(self.path, data, format='json')
        return self.responder(response.status_code, response.data)

print(json.dumps({'ready':'127.0.0.1:3014','database':'SQLite :memory:','django':django.get_version()}),flush=True)
with patch('requests.sessions.Session.request', side_effect=AssertionError('Red externa bloqueada')):
    HTTPServer(('127.0.0.1',3014),Handler).serve_forever()
