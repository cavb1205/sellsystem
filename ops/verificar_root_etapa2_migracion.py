"""Migración 0027 -> 0028 con datos anteriores ficticios, solo SQLite en memoria."""
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE']='sell_system.settings_pruebas_reportes'
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
assert connection.vendor=='sqlite' and connection.settings_dict['NAME']==':memory:'
connection.features.can_alter_table_drop_column=False
executor=MigrationExecutor(connection)
old=[('Tiendas','0027_root_integridad')]
executor.migrate(old)
apps=executor.loader.project_state(old).apps
User=apps.get_model('auth','User');admin=User.objects.create(username='ficticio')
Plan=apps.get_model('Tiendas','Membresia');plan=Plan.objects.create(nombre='Mensual',precio=12000)
Ruta=apps.get_model('Tiendas','Tienda');ruta=Ruta.objects.create(nombre='Ficticia',administrador=admin,caja_inicial=10000)
Cuenta=apps.get_model('Tiendas','CuentaDestino')
Cuenta.objects.update_or_create(pk=1, defaults={'banco':'Banco Ficticio','numero':'000-TEST','titular':'Titular Ficticio','tipo':'Cuenta Ficticia'})
cuenta_antes=Cuenta.objects.filter(pk=1).values('banco','numero','titular','tipo').get()
Solicitud=apps.get_model('Tiendas','SolicitudPago')
s=Solicitud.objects.create(tienda=ruta,membresia=plan,codigo='HISTORICA',monto_acordado=11000,expira=datetime(2026,10,12,tzinfo=timezone.utc))
Pago=apps.get_model('Tiendas','PagoMembresia')
Pago.objects.create(tienda=ruta,membresia=plan,solicitud=s,monto=11000,fecha=date(2026,10,10))
ingresos=list(Pago.objects.values_list('pk','monto','estado'))
executor=MigrationExecutor(connection);new=[('Tiendas','0028_root_estados_configuracion')]
executor.migrate(new);apps=executor.loader.project_state(new).apps
Solicitud=apps.get_model('Tiendas','SolicitudPago');s=Solicitud.objects.get(pk=s.pk)
assert s.cuenta_acordada=={} and not s.confirmada_fuera_plazo and s.motivo_revision==''
assert s.monto_acordado==11000
Cuenta=apps.get_model('Tiendas','CuentaDestino')
assert Cuenta.objects.filter(pk=1).values('banco','numero','titular','tipo').get()==cuenta_antes
assert Cuenta.objects.get(pk=1).version==0
assert ingresos==list(apps.get_model('Tiendas','PagoMembresia').objects.values_list('pk','monto','estado'))
assert apps.get_model('Tiendas','Tienda').objects.get(pk=ruta.pk).caja_inicial==10000
assert not apps.get_model('Tiendas','CambioConfiguracionRoot').objects.exists()
from Tiendas import models
for model,fields in [(models.CuentaDestino,['version']),
    (models.SolicitudPago,['cuenta_acordada','confirmada_fuera_plazo','motivo_revision']),
    (models.CambioConfiguracionRoot,[f.name for f in models.CambioConfiguracionRoot._meta.fields])]:
    estado=apps.get_model('Tiendas',model.__name__)
    for field in fields:
        assert model._meta.get_field(field).deconstruct()[1:]==estado._meta.get_field(field).deconstruct()[1:],field
print('OK: migración 0027 -> 0028; schema nuevo coincide; cuenta, monto, ingreso y caja intactos; no se inventa cuenta histórica ni auditoría retroactiva.')
