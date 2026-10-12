"""Servidor de pruebas de cartera: solo GET, SQLite en memoria y datos ficticios."""
import json
import os
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'sell_system.settings_pruebas_reportes'
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.contrib.auth.models import User
from django.db import connection
from rest_framework.test import APIClient
from Tiendas import test_suscripciones as fixtures
from Tiendas.models import Tienda, Tienda_Membresia, Tienda_Administrador
from Tiendas.serializers import TiendaMembresiaSerializer
from Ventas.models import Venta
from Ventas.test_reporte_historico import completar_esquema_legacy_de_pruebas
from Clientes.models import Cliente
from Recaudos.models import Recaudo
settings.ROOT_URLCONF = 'Tiendas.test_suscripciones'
assert connection.vendor == 'sqlite' and settings.DATABASES['default']['NAME'] == ':memory:'
connection.features.can_alter_table_drop_column = False
call_command('migrate', verbosity=0)
completar_esquema_legacy_de_pruebas()
fixture = fixtures.SuscripcionesTests(); fixture.setUp()
fixture.admin.username = 'cavb1205'; fixture.admin.save()
other = User.objects.create_user('otro-administrador', is_staff=True)
third = Tienda.objects.create(id=903, nombre='Ruta independiente', administrador=other)
Tienda_Administrador.objects.create(tienda=third, administrador=other)
tm_other = Tienda_Membresia.objects.create(tienda=third, membresia=fixture.mensual,
    estado='Activa', fecha_activacion=date(2026,10,1), fecha_vencimiento=date(2026,12,1))

def crear(ruta, i, name=None, fecha=date(2026,9,1), paid=0, client=None):
    client = client or Cliente.objects.create(tienda=ruta, identificacion=f'F-{ruta.pk}-{i}',
        nombres=name or f'Cliente ficticio {i:02d}', apellidos='Álvarez', telefono_principal='000', direccion='Ficticia')
    v = Venta.objects.create(tienda=ruta, cliente=client, fecha_venta=fecha, valor_venta=100000,
        interes=20, cuotas=20, saldo_actual=120000-paid)
    if paid:
        Recaudo.objects.create(tienda=ruta, venta=v, fecha_recaudo=date(2026,10,10), valor_recaudo=paid)
    return v

for i in range(24): crear(fixture.ruta, i, name='=SUM(A1)' if i == 23 else None)
shared = crear(fixture.ruta, 30, name='Cliente combinado', paid=3000)
crear(fixture.ruta, 31, client=shared.cliente, fecha=date(2026,10,9), paid=6000)
crear(fixture.ruta, 32, name='Cliente combinado')
for i in range(105):
    v = crear(fixture.ruta, 100+i, fecha=date(2026,8,1), paid=0)
    Venta.objects.filter(pk=v.pk).update(estado_venta='Pagado', saldo_actual=0)
crear(fixture.otra, 0, name='Exclusivo ruta B')
crear(third, 0, name='Exclusivo independiente')

class Handler(BaseHTTPRequestHandler):
    def json(self, status, data):
        self.send_response(status); self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store'); self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())
    def do_GET(self):
        if self.path == '/__estado':
            return self.json(200, {'ventas': list(Venta.objects.values('id','estado_venta','saldo_actual')),
                'recaudos': list(Recaudo.objects.values('id','valor_recaudo','fecha_recaudo','venta_id')),
                'rutas': list(Tienda.objects.values('id','caja_inicial'))})
        roles = {'admin': fixture.admin, 'worker': fixture.worker, 'other': other}
        if self.path.startswith('/__sesion/'):
            role = self.path.rsplit('/',1)[-1]; user = roles[role]
            return self.json(200, {'user': {'id': user.pk, 'username': user.username, 'is_staff': user.is_staff, 'is_superuser': user.is_superuser},
                'store': TiendaMembresiaSerializer(tm_other if role == 'other' else fixture.tm).data,
                'profile': {'id': 1, 'tienda': third.pk if role == 'other' else fixture.ruta.pk}})
        if not self.path.startswith(('/tiendas/','/ventas/','/clientes/','/recaudos/','/trabajadores/')):
            return self.json(404, {'error':'Fuera de alcance'})
        api = APIClient(); api.force_authenticate(roles[self.headers.get('X-Local-Role','admin')])
        response = api.get(self.path)
        self.send_response(response.status_code); self.send_header('Content-Type', response.get('Content-Type','application/json'))
        self.end_headers(); self.wfile.write(response.content)
    def log_message(self, *args):
        pass

print('Cartera: 127.0.0.1:3043; SQLite ficticia; solo GET; red externa prohibida.', flush=True)
HTTPServer(('127.0.0.1',3043), Handler).serve_forever()
