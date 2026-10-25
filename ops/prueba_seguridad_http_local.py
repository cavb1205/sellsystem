"""Prueba WSGI real con Gunicorn, SQLite temporal y datos exclusivamente ficticios.

No importa variables.py, no usa PostgreSQL y bloquea conexiones externas.
Ejecutar con el Python del entorno candidato, nunca con credenciales reales.
"""
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    assert sys.version_info[:2] == (3, 12), 'Usar el entorno candidato Python 3.12'
    with tempfile.TemporaryDirectory(prefix='cartera-http-ficticio-') as temporary:
        folder = Path(temporary)
        os.chmod(folder, 0o700)
        settings = folder/'settings_http_ficticias.py'
        settings.write_text('''
import sys, types, socket
from pathlib import Path
variables=types.ModuleType('sell_system.variables')
variables.KEY='clave-ficticia-exclusiva-prueba-local-HTTP-2026'
variables.JWT_SIGNING_KEY='clave-jwt-ficticia-separada-exclusiva-prueba-local-HTTP-2026'
variables.NAME_BD=variables.USER_BD=variables.PASSWORD_BD='solo-pruebas'
sys.modules['sell_system.variables']=variables
from sell_system.settings import *
DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':str(Path(__file__).parent/'ficticia.sqlite3')}}
ALLOWED_HOSTS=['127.0.0.1','localhost']
STATIC_ROOT=Path(__file__).parent/'static'
MEDIA_ROOT=Path(__file__).parent/'media'
_original_connect=socket.socket.connect
def _local_connect(sock,address):
    if isinstance(address,str) or (isinstance(address,tuple) and address[0] in ('127.0.0.1','::1')):
        return _original_connect(sock,address)
    raise RuntimeError('Red externa prohibida en prueba ficticia')
socket.socket.connect=_local_connect
''')
        env = os.environ.copy()
        env['DJANGO_SETTINGS_MODULE']='settings_http_ficticias'
        env['PYTHONPATH']=str(folder)+os.pathsep+str(ROOT)
        os.environ.update({key:env[key] for key in ['DJANGO_SETTINGS_MODULE','PYTHONPATH']})
        sys.path[:0]=[str(folder),str(ROOT)]
        import django
        django.setup()
        from django.core.management import call_command
        from django.db import connection
        from django.contrib.auth.models import User
        from Tiendas.models import Tienda, Tienda_Administrador, Membresia, Tienda_Membresia
        from Tiendas.fecha_operativa import fecha_operativa
        from Trabajadores.models import Perfil
        from Clientes.models import Cliente
        from Ventas.models import Venta
        from Recaudos.models import Recaudo
        from Ventas.test_reporte_historico import completar_esquema_legacy_de_pruebas
        from datetime import timedelta
        assert connection.vendor=='sqlite'
        assert Path(connection.settings_dict['NAME']).parent==folder
        connection.features.can_alter_table_drop_column=False
        call_command('migrate',interactive=False,verbosity=0,stdout=io.StringIO())
        completar_esquema_legacy_de_pruebas()
        call_command('check',stdout=io.StringIO())
        owner=User.objects.create_user('admin-http-ficticio',is_staff=True)
        worker=User.objects.create_user('worker-http-ficticio',password='Clave-HTTP-Ficticia-Inicial-2026!')
        route=Tienda.objects.create(nombre='Ruta HTTP ficticia',administrador=owner,caja_inicial=10000)
        Tienda_Administrador.objects.create(tienda=route,administrador=owner)
        profile=Perfil.objects.create(trabajador=worker,tienda=route)
        membership=Membresia.objects.create(nombre='Mensual HTTP ficticia',precio=0)
        today=fecha_operativa(route)
        Tienda_Membresia.objects.create(tienda=route,membresia=membership,estado='Activa',
            fecha_activacion=today,fecha_vencimiento=today+timedelta(days=30))
        client=Cliente.objects.create(tienda=route,identificacion='http-ficticio',nombres='Cliente',
            apellidos='Ficticio',telefono_principal='000',direccion='Solo prueba local')
        sale=Venta.objects.create(tienda=route,cliente=client,fecha_venta=today-timedelta(days=3),
            valor_venta=1000,interes=20,cuotas=20,saldo_actual=1200)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        base='http://127.0.0.1:'+str(port)
        checks=[]
        def request(path,expected,method='GET',data=None,token=None):
            headers={'Content-Type':'application/json'}
            if token:headers['Authorization']='Bearer '+token
            req=Request(base+path,data=json.dumps(data).encode() if data is not None else None,
                        headers=headers,method=method)
            try:response=urlopen(req,timeout=5)
            except HTTPError as error:response=error
            raw=response.read()
            value=json.loads(raw) if 'application/json' in response.headers.get('Content-Type','') else None
            assert response.status==expected, (path,response.status,expected)
            checks.append({'path':path,'status':response.status,'passed':True})
            return value,response.headers
        log=folder/'gunicorn.log'
        with log.open('w') as stream:
            server=subprocess.Popen([sys.executable,'-m','gunicorn','sell_system.wsgi:application',
                '--bind','127.0.0.1:'+str(port),'--workers','1','--error-logfile',str(log)],
                cwd=ROOT,env=env,stdout=stream,stderr=stream)
            try:
                ready=False
                for _ in range(40):
                    if server.poll() is not None:break
                    try:
                        request('/clientes/',401)
                        ready=True;break
                    except (URLError,ConnectionError,TimeoutError):time.sleep(0.15)
                assert ready, 'El servidor candidato no inició; revisar log ficticio local'
                data,_=request('/login/',200,'POST',{'username':worker.username,
                    'password':'Clave-HTTP-Ficticia-Inicial-2026!'})
                access=data['token'];refresh=data['refresh']
                request('/clientes/tienda/'+str(route.pk)+'/',200,token=access)
                payment={'venta':sale.pk,'fecha_recaudo':str(today),'valor_recaudo':'100'}
                request('/recaudos/create/t/'+str(route.pk)+'/',200,'POST',payment,access)
                request('/trabajadores/password/'+str(profile.pk)+'/',200,'POST',
                    {'passwordNuevo':'Clave-HTTP-Ficticia-Nueva-2026!'},access)
                request('/clientes/',401,token=access)
                request('/token/refresh/',401,'POST',{'refresh':refresh})
                request('/recaudos/create/t/'+str(route.pk)+'/',401,'POST',payment,access)
                data,_=request('/login/',200,'POST',{'username':worker.username,
                    'password':'Clave-HTTP-Ficticia-Nueva-2026!'})
                request('/token/refresh/',200,'POST',{'refresh':data['refresh']})
                request('/recaudos/create/t/'+str(route.pk)+'/',200,'POST',payment,data['token'])
                _,headers=request('/admin/login/',200)
                assert 'Secure' in headers.get('Set-Cookie','')
                sale.refresh_from_db();route.refresh_from_db()
                assert sale.saldo_actual==1000 and route.caja_inicial==10200
                assert Recaudo.objects.count()==2
                print(json.dumps({'gunicornHTTP':True,'django':django.get_version(),
                    'checks':checks,'revokedTokensRejected':True,'collectionsNotDuplicated':True,
                    'secureCsrfCookie':True,'database':'temporary SQLite',
                    'externalConnectionsBlocked':True,'productionDataTouched':False}),flush=True)
            finally:
                server.terminate()
                try:server.wait(timeout=5)
                except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
                connection.close()


if __name__=='__main__':main()
