"""Chrome → Django → SQLite ficticia. Escrituras solo de la edición probada."""
import json
import os
import re
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE']='sell_system.settings_pruebas_reportes'
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from rest_framework.test import APIClient
from Tiendas import test_suscripciones as fixtures
from Tiendas.models import Tienda, Cierre_Caja, MovimientoCaja
from Tiendas.serializers import TiendaMembresiaSerializer
from Ventas.models import Venta
from Ventas.test_reporte_historico import completar_esquema_legacy_de_pruebas
from Recaudos.models import Recaudo
assert connection.vendor=='sqlite' and settings.DATABASES['default']['NAME']==':memory:'
settings.ROOT_URLCONF='Tiendas.test_suscripciones'
connection.features.can_alter_table_drop_column=False
call_command('migrate',verbosity=0)
completar_esquema_legacy_de_pruebas()
fixture=fixtures.SuscripcionesTests();fixture.setUp()

def crear(pk,**extra):
    datos=dict(id=pk,tienda=fixture.ruta,cliente=fixture.cliente,
        fecha_venta=date(2026,10,8),valor_venta=1000000,interes=20,
        cuotas=20,plazo='Diario',calendario_version=0,saldo_actual=1200000)
    datos.update(extra)
    return Venta.objects.create(**datos)

crear(1001)
abonos=crear(1002,saldo_actual=1100000)
Recaudo.objects.create(venta=abonos,tienda=fixture.ruta,fecha_recaudo=date(2026,10,9),valor_recaudo=100000)
visita=crear(1003)
Recaudo.objects.create(venta=visita,tienda=fixture.ruta,fecha_recaudo=date(2026,10,9),valor_recaudo=0)
crear(1004,fecha_venta=date(2026,1,31),plazo='Mensual',cuotas=1,calendario_version=0)
crear(1005,tienda=fixture.otra)
crear(1006)  # Movimiento concurrente se añade desde el test local.

class Handler(BaseHTTPRequestHandler):
    def json(self,status,data):
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.end_headers();self.wfile.write(json.dumps(data,default=str).encode())
    def ejecutar(self):
        if self.path=='/__estado':
            return self.json(200,{'ventas':list(Venta.objects.values('id','tienda_id','plazo','cuotas',
                'calendario_version','fecha_vencimiento','valor_venta','interes','saldo_actual')),
                'recaudos':list(Recaudo.objects.values('id','venta_id','valor_recaudo','fecha_recaudo')),
                'caja':list(Tienda.objects.values('id','caja_inicial')),
                'movimientos':MovimientoCaja.objects.count()})
        if self.path.startswith('/__sesion/'):
            role=self.path.rsplit('/',1)[-1]
            user={'admin':fixture.admin,'worker':fixture.worker}[role]
            return self.json(200,{'user':{'id':user.pk,'username':user.username,
                'is_staff':user.is_staff,'is_superuser':user.is_superuser},
                'store':TiendaMembresiaSerializer(fixture.tm).data,
                'profile':{'id':1,'tienda':fixture.ruta.pk}})
        if self.path=='/__movimiento/1006' and self.command=='POST':
            Recaudo.objects.create(venta_id=1006,tienda=fixture.ruta,
                fecha_recaudo=date(2026,10,9),valor_recaudo=0)
            return self.json(200,{'creado':True})
        if not self.path.startswith(('/ventas/','/recaudos/','/clientes/','/tiendas/','/trabajadores/')):
            return self.json(404,{'error':'Fuera de alcance'})
        if self.command!='GET' and not re.fullmatch(r'/ventas/\d+/(?:update|correccion-administrativa)/t/901/',self.path):
            return self.json(405,{'error':'Escritura fuera del escenario ficticio'})
        api=APIClient();api.force_authenticate(fixture.admin)
        body=self.rfile.read(int(self.headers.get('Content-Length','0')))
        response=api.generic(self.command,self.path,body,content_type='application/json')
        self.send_response(response.status_code)
        self.send_header('Content-Type',response.get('Content-Type','application/json'))
        self.end_headers();self.wfile.write(response.content)
    do_GET=ejecutar
    do_PUT=ejecutar
    do_PATCH=ejecutar
    do_POST=ejecutar
    def log_message(self,*args): pass

print('API de edición: 127.0.0.1:3047; SQLite ficticia; Telegram bloqueado.',flush=True)
HTTPServer(('127.0.0.1',3047),Handler).serve_forever()
