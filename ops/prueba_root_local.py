"""Servidor de integración root: loopback, SQLite desechable, sin red externa."""
import io
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'sell_system.settings_pruebas_reportes'
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.core.files.base import ContentFile
from django.db import connection
from PIL import Image
from rest_framework.test import APIClient
from Tiendas.test_suscripciones import SuscripcionesTests
from Tiendas.models import (Tienda, Tienda_Membresia, SolicitudPago,
    PagoMembresia, CambioSuscripcion, Membresia)
from Tiendas.serializers import TiendaMembresiaSerializer
from Ventas.test_reporte_historico import completar_esquema_legacy_de_pruebas
settings.ROOT_URLCONF = 'Tiendas.test_suscripciones'
assert connection.vendor == 'sqlite' and settings.DATABASES['default']['NAME'] == ':memory:'
media = tempfile.TemporaryDirectory(prefix='root-comprobantes-ficticios-')
settings.MEDIA_ROOT = media.name
connection.features.can_alter_table_drop_column = False
call_command('migrate', verbosity=0)
completar_esquema_legacy_de_pruebas()
caso = SuscripcionesTests(); caso.setUp()
vacia = Tienda.objects.create(nombre='Ruta sin historial',administrador=caso.admin)
Tienda_Membresia.objects.create(tienda=vacia,membresia=caso.mensual,
    fecha_activacion=caso.tm.fecha_activacion,fecha_vencimiento=caso.tm.fecha_vencimiento)
legacy = caso.solicitud()
buf=io.BytesIO();Image.new('RGB',(180,100),'white').save(buf,format='JPEG')
legacy.comprobante.save('ficticio.jpg',ContentFile(buf.getvalue()))
SolicitudPago.objects.filter(pk=legacy.pk).update(monto_acordado=None)


class Handler(BaseHTTPRequestHandler):
    def ejecutar(self):
        if self.path == '/__estado':
            caso.ruta.refresh_from_db();caso.venta.refresh_from_db();caso.tm.refresh_from_db()
            return self.json(200,{'caja':str(caso.ruta.caja_inicial),'saldo':str(caso.venta.saldo_actual),
                'clientes':caso.ruta.cliente_set.count(),'recaudos':caso.ruta.recaudo_set.count(),
                'archivada':caso.tm.archivada,'version':caso.tm.version,
                'planes':list(Membresia.objects.values('id','precio','version')),
                'pagos':list(PagoMembresia.objects.values('monto','estado','solicitud_id')),
                'cambios':list(CambioSuscripcion.objects.values('accion','version'))})
        if self.path.startswith('/__sesion/'):
            role=self.path.rsplit('/',1)[-1];user={'root':caso.root,'admin':caso.admin,'worker':caso.worker}[role]
            return self.json(200,{'user':{'id':user.pk,'username':user.username,'is_staff':user.is_staff,'is_superuser':user.is_superuser},
                'store':TiendaMembresiaSerializer(caso.tm).data,'profile':{'id':1,'tienda':caso.ruta.pk}})
        if not self.path.startswith(('/tiendas/','/ventas/','/trabajadores/','/clientes/','/recaudos/')):
            return self.json(404,{'error':'Fuera del alcance de la prueba'})
        role=self.headers.get('X-Local-Role','root')
        api=APIClient();api.force_authenticate({'root':caso.root,'admin':caso.admin,'worker':caso.worker}[role])
        body=self.rfile.read(int(self.headers.get('Content-Length','0')))
        response=api.generic(self.command,self.path,body,content_type=self.headers.get('Content-Type','application/json'))
        data=b''.join(response.streaming_content) if response.streaming else response.content
        self.send_response(response.status_code);self.send_header('Content-Type',response.get('Content-Type','application/json'))
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    def json(self,status,data):
        self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(json.dumps(data,default=str).encode())
    do_GET=ejecutar
    do_POST=ejecutar
    do_PUT=ejecutar
    do_DELETE=ejecutar


print('Root etapa 1: 127.0.0.1:3041; SQLite en memoria; datos ficticios; red externa prohibida.',flush=True)
HTTPServer(('127.0.0.1',3041),Handler).serve_forever()
