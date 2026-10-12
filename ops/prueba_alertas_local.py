"""Chrome → Django → SQLite en memoria. Datos ficticios, solo loopback."""
import json
import os
import sys
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'sell_system.settings_pruebas_reportes'
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient
from Tiendas import test_suscripciones as fixtures
from Tiendas.models import AlertaOperativa, Tienda_Membresia, Cierre_Caja, MovimientoCaja
from Tiendas.serializers import TiendaMembresiaSerializer
from Ventas.models import Venta
from Ventas.test_reporte_historico import completar_esquema_legacy_de_pruebas
from Clientes.models import Cliente
from Recaudos.models import Recaudo
settings.ROOT_URLCONF = 'Tiendas.test_suscripciones'
settings.TELEGRAM_ALERTAS_USERNAME = 'cavb1205'
assert connection.vendor == 'sqlite' and settings.DATABASES['default']['NAME'] == ':memory:'
connection.features.can_alter_table_drop_column = False
call_command('migrate', verbosity=0)
completar_esquema_legacy_de_pruebas()
fixture=fixtures.SuscripcionesTests(); fixture.setUp()
fixture.admin.username='cavb1205'; fixture.admin.save()

def alerta(**extra):
    fields=dict(tipo='VENTA_CON_ALERTAS',severidad='media',estado='nueva',
        clave_dedupe=f'local:{AlertaOperativa.objects.count()}',titulo='Revisión ficticia',
        detalle='Solo datos de prueba',tienda_id_ref=fixture.ruta.pk,
        cliente_id_ref=fixture.cliente.pk,venta_id_ref=fixture.venta.pk)
    fields.update(extra)
    return AlertaOperativa.objects.create(**fields)

for nombre in ('escritorio','movil'):
    a=alerta(titulo=f'Excepción Ántigua {nombre}',severidad='critica')
    AlertaOperativa.objects.filter(pk=a.pk).update(creada=timezone.now()-timedelta(days=45))
for i in range(205): alerta(titulo=f'Crédito ficticio {i}')
for day in (4,5,6): alerta(tipo='CIERRE_AUSENTE',venta_id_ref=None,datos={'fecha':f'2026-10-0{day}'},severidad='alta')
alerta(tipo='CIERRE_AUSENTE',venta_id_ref=None,datos={'fecha':'2026-10-03'})
Cierre_Caja.objects.create(tienda=fixture.ruta,fecha_cierre=date(2026,10,3),valor=10000)
pagada=Venta.objects.create(tienda=fixture.ruta,cliente=fixture.cliente,fecha_venta=date(2026,8,1),
    valor_venta=1000,interes=20,cuotas=20,saldo_actual=0,estado_venta='Pagado')
Recaudo.objects.create(tienda=fixture.ruta,venta=pagada,fecha_recaudo=date(2026,8,20),valor_recaudo=1200)
alerta(tipo='RIESGO_CARTERA',venta_id_ref=pagada.pk,severidad='critica',titulo='NO MOSTRAR: crédito cerrado')
alerta(tipo='RIESGO_CARTERA',severidad='critica',titulo='Gestión actual ficticia')
otro_cliente=Cliente.objects.create(tienda=fixture.otra,identificacion='fic-B',nombres='Cliente B',
    apellidos='Ficticio',telefono_principal='000',direccion='Ficticia')
otra_venta=Venta.objects.create(tienda=fixture.otra,cliente=otro_cliente,fecha_venta=date(2026,10,1),
    valor_venta=1000,interes=20,cuotas=20,saldo_actual=1200)
alerta(titulo='Caso de otra ruta',tienda_id_ref=fixture.otra.pk,cliente_id_ref=otro_cliente.pk,
    venta_id_ref=otra_venta.pk,severidad='alta')
MovimientoCaja.objects.create(tienda=fixture.ruta,delta=-300000,saldo_anterior=310000,
    saldo_posterior=10000,tipo='VENTA')

class Handler(BaseHTTPRequestHandler):
    def json(self,status,data):
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.end_headers();self.wfile.write(json.dumps(data,default=str).encode())
    def ejecutar(self):
        if self.path=='/__estado':
            return self.json(200,{'finanzas':{
                'rutas':list(fixture.ruta.__class__.objects.values('id','caja_inicial')),
                'ventas':list(Venta.objects.values('id','estado_venta','saldo_actual')),
                'recaudos':list(Recaudo.objects.values('id','valor_recaudo','tienda_id')),
                'cierres':list(Cierre_Caja.objects.values('id','fecha_cierre','valor')),
                'movimientos':list(MovimientoCaja.objects.values('id','delta','saldo_posterior'))},
                'alertas':list(AlertaOperativa.objects.values('id','estado','datos'))})
        if self.path.startswith('/__sesion/'):
            role=self.path.rsplit('/',1)[-1]
            user={'admin':fixture.admin,'worker':fixture.worker,'root':fixture.root}[role]
            return self.json(200,{'user':{'id':user.pk,'username':user.username,
                'is_staff':user.is_staff,'is_superuser':user.is_superuser},
                'store':TiendaMembresiaSerializer(fixture.tm).data,
                'profile':{'id':1,'tienda':fixture.ruta.pk}})
        if not self.path.startswith(('/tiendas/','ventas/','/ventas/','/clientes/','/recaudos/','/trabajadores/')):
            return self.json(404,{'error':'Fuera de alcance'})
        role=self.headers.get('X-Local-Role','admin')
        api=APIClient();api.force_authenticate({'admin':fixture.admin,'worker':fixture.worker,'root':fixture.root}[role])
        body=self.rfile.read(int(self.headers.get('Content-Length','0')))
        response=api.generic(self.command,self.path,body,content_type=self.headers.get('Content-Type','application/json'))
        self.send_response(response.status_code)
        self.send_header('Content-Type',response.get('Content-Type','application/json'))
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(response.content)
    do_GET=ejecutar
    do_PATCH=ejecutar

print('Alertas: 127.0.0.1:3042; SQLite ficticia, red externa prohibida.',flush=True)
HTTPServer(('127.0.0.1',3042),Handler).serve_forever()
