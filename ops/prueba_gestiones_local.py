"""API de prueba para Liquidar. Solo SQLite en memoria, sin red externa."""
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
from rest_framework.test import APIClient
from Clientes.models import Cliente
from Tiendas import test_suscripciones as fixtures
from Tiendas.models import Tienda, MovimientoCaja
from Tiendas.serializers import TiendaMembresiaSerializer
from Ventas.models import Venta
from Ventas.test_reporte_historico import completar_esquema_legacy_de_pruebas
from Recaudos.models import Recaudo, Visita_Blanco

assert connection.vendor == 'sqlite' and settings.DATABASES['default']['NAME'] == ':memory:'
settings.ROOT_URLCONF = 'Tiendas.test_suscripciones'
connection.features.can_alter_table_drop_column = False
call_command('migrate', verbosity=0)
completar_esquema_legacy_de_pruebas()
fixture = fixtures.SuscripcionesTests()
fixture.setUp()  # Incluye el bloqueo de toda notificación externa.
fixture.venta.estado_venta = 'Pagado'
fixture.venta.saldo_actual = 0
fixture.venta.save()
ahora = datetime(2026, 10, 15, 16, tzinfo=timezone.utc)
for pk, nombre, plazo, cuotas, fecha, saldo in [
    (1001, 'Semanal', 'Semanal', 4, date(2026,10,8), 1200000),
    (1002, 'Mensual', 'Mensual', 1, date(2026,9,15), 1200000),
    (1003, 'Diario', 'Diario', 20, date(2026,10,8), 1200000),
    (1004, 'Adelantado', 'Semanal', 4, date(2026,10,8), 600000),
    (1005, 'Sin vencimiento', 'Semanal', 4, date(2026,10,14), 1200000),
]:
    cliente = Cliente.objects.create(tienda=fixture.ruta, identificacion=str(pk),
        nombres=nombre, apellidos='Prueba', direccion='Local', telefono_principal='000')
    venta = Venta.objects.create(id=pk, tienda=fixture.ruta, cliente=cliente, fecha_venta=fecha,
        plazo=plazo, cuotas=cuotas, valor_venta=1000000, interes=20, saldo_actual=saldo,
        calendario_version=0 if plazo == 'Diario' else 1, fecha_vencimiento=date(2026,11,15))
    if nombre == 'Adelantado':
        Recaudo.objects.create(venta=venta, tienda=fixture.ruta,
            fecha_recaudo=date(2026,10,14), valor_recaudo=600000)

class Handler(BaseHTTPRequestHandler):
    def json(self, status, data):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def ejecutar(self):
        global ahora
        if self.path == '/__estado':
            return self.json(200, {'ventas': list(Venta.objects.filter(pk__gte=1001).values(
                'id', 'saldo_actual', 'valor_venta', 'interes', 'plazo', 'cuotas')),
                'recaudos': list(Recaudo.objects.values('venta_id', 'valor_recaudo', 'fecha_recaudo')),
                'fallas': Visita_Blanco.objects.count(), 'movimientos': MovimientoCaja.objects.count(),
                'caja': list(Tienda.objects.values('id', 'caja_inicial'))})
        if self.path.startswith('/__sesion/'):
            user = {'admin': fixture.admin, 'worker': fixture.worker}[self.path.rsplit('/', 1)[-1]]
            return self.json(200, {'user': {'id': user.pk, 'username': user.username,
                'is_staff': user.is_staff, 'is_superuser': user.is_superuser},
                'store': TiendaMembresiaSerializer(fixture.tm).data,
                'profile': {'id': 1, 'tienda': fixture.ruta.pk}})
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.path == '/__dia' and self.command == 'POST':
            dia = date.fromisoformat(json.loads(body)['fecha'])
            assert dia in (date(2026,10,15), date(2026,10,16))
            ahora = datetime(dia.year, dia.month, dia.day, 16, tzinfo=timezone.utc)
            return self.json(200, {'fecha': dia})
        if not self.path.startswith(('/ventas/', '/recaudos/', '/tiendas/')):
            return self.json(404, {'error': 'Fuera del escenario local'})
        if self.command != 'GET':
            if self.path not in ('/recaudos/create/nopay/t/901/', '/recaudos/create/t/901/'):
                return self.json(405, {'error': 'Escritura fuera del escenario local'})
            if int(json.loads(body).get('venta', 0)) not in (1001,1002,1003,1004,1005):
                return self.json(403, {'error': 'Solo créditos ficticios'})
        api = APIClient()
        user = fixture.worker if self.headers.get('X-Prueba-Rol') == 'worker' else fixture.admin
        api.force_authenticate(user)
        with patch('Tiendas.fecha_operativa.timezone.now', return_value=ahora):
            response = api.generic(self.command, self.path, body, content_type='application/json')
        self.send_response(response.status_code)
        self.send_header('Content-Type', response.get('Content-Type', 'application/json'))
        self.end_headers()
        self.wfile.write(response.content)

    do_GET = ejecutar
    do_POST = ejecutar
    def log_message(self, *args):
        pass

print('API de gestiones: 127.0.0.1:3050; SQLite ficticia; Telegram bloqueado.', flush=True)
HTTPServer(('127.0.0.1', 3050), Handler).serve_forever()
