"""Despliegue acotado de un commit verificado. Configuración privada fuera de Git.

Fases: backup|migrate|activate|health MANIFEST.json [backup-directory].
El manifiesto contiene hashes del commit anterior, del nuevo y archivos cambiados.
Solo migrate agrega columnas/tabla; ninguna fase vence rutas ni anula pagos.
"""
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path('/home/cavb1205/sellsystem/sellsystem')
BASE = Path('/home/cavb1205/sellsystem/.deploy-backups')
MIGRATION = '0026_vencimiento_manual'
phase, manifest_path = sys.argv[1:3]
assert phase in ('backup', 'migrate', 'activate', 'health')
manifest = json.loads(Path(manifest_path).read_text())
assert len(manifest['commit']) == 40
old, new, changed = manifest['old'], manifest['new'], manifest['changed']
assert all(not Path(p).is_absolute() and '..' not in Path(p).parts and p != 'sell_system/variables.py' for p in new)


def verify(root, files):
    bad = [p for p,h in files.items() if not (root/p).is_file() or hashlib.sha256((root/p).read_bytes()).hexdigest()!=h]
    assert not bad, 'Código distinto del commit: '+str(bad)


def replace(source, destination):
    info = destination.stat() if destination.exists() else destination.parent.stat()
    tmp = destination.with_name(destination.name+'.suscripciones-release-tmp')
    assert not tmp.exists()
    shutil.copyfile(source,tmp)
    os.chmod(tmp,info.st_mode&0o777 if destination.exists() else 0o644)
    os.chown(tmp,info.st_uid,info.st_gid)
    os.replace(tmp,destination)


if phase == 'backup':
    verify(ROOT,old)
    backup=Path(tempfile.mkdtemp(prefix='20261010-suscripciones-',dir=BASE));os.chmod(backup,0o700)
    for p in changed:
        if (ROOT/p).exists():
            dest=backup/'codigo'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/p,dest)
    (backup/'release').mkdir()
else:
    backup=Path(sys.argv[3]).resolve()
    assert backup.parent==BASE and backup.name.startswith('20261010-suscripciones-') and backup.is_dir()

if phase == 'activate':
    verify(ROOT,old);verify(backup/'release',new)
    assert (backup/'postgresql.dump').stat().st_size>0 and (backup/'migration.json').is_file()
    services={}
    for service in ('sellsystem','telegram-clientes-webhook'):
        pid=int(subprocess.check_output(['supervisorctl','pid',service],text=True).strip())
        command=subprocess.check_output(['ps','-p',str(pid),'-o','args='],text=True)
        assert '/home/cavb1205/sellsystem/venv/bin/gunicorn sell_system.wsgi' in command
        workers=[int(p) for p in subprocess.check_output(['ps','--ppid',str(pid),'-o','pid='],text=True).split()]
        log=Path('/var/log/sellsystem.err.log' if service=='sellsystem' else '/var/log/telegram-clientes-webhook.log')
        services[service]={'pid':pid,'workers':workers,'log':str(log),'offset':log.stat().st_size}
    copied=[]
    try:
        for p in changed:
            source=backup/'release'/p
            if p.endswith('.py'):compile(source.read_bytes(),p,'exec')
            (ROOT/p).parent.mkdir(parents=True,exist_ok=True);replace(source,ROOT/p);copied.append(p)
        subprocess.run([sys.executable,'manage.py','check'],cwd=ROOT,check=True)
        verify(ROOT,new)
    except BaseException:
        for p in reversed(copied):
            if (backup/'codigo'/p).exists():replace(backup/'codigo'/p,ROOT/p)
            else:
                dest=backup/'desactivado'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(ROOT/p),dest)
        verify(ROOT,old)
        raise
    (backup/'workers.json').write_text(json.dumps(services))
    (backup/'commit.json').write_text(json.dumps({'commit':manifest['commit'],'files':len(new)}))
    for info in services.values():os.kill(info['pid'],signal.SIGHUP)
    print(json.dumps({'activated':True,'commit':manifest['commit'],'verifiedFiles':len(new)}))
    sys.exit(0)

if phase == 'health':
    verify(ROOT,new)
    services=json.loads((backup/'workers.json').read_text());result=[]
    for service,info in services.items():
        assert 'RUNNING' in subprocess.check_output(['supervisorctl','status',service],text=True)
        assert not any(Path('/proc/'+str(pid)).exists() for pid in info['workers']), 'Workers anteriores activos'
        with open(info['log'],'rb') as f:f.seek(info['offset']);lines=f.read().decode(errors='replace').splitlines()
        errors=sum(any(t in line for t in ('Traceback','[ERROR]','[CRITICAL]','Internal Server Error:')) for line in lines)
        assert not errors, service+' tiene errores nuevos'
        result.append({'service':service,'state':'RUNNING','newErrors':errors})
    assert subprocess.check_output(['systemctl','is-active','nginx'],text=True).strip()=='active'
    print(json.dumps({'sourceMatchesGitHubCommit':manifest['commit'],'verifiedFiles':len(new),'services':result,'nginx':'active'}))
    sys.exit(0)

target=backup/'release' if phase=='migrate' else ROOT
if phase=='migrate':
    verify(ROOT,old);verify(target,new)
    private=target/'sell_system/variables.py';assert not private.exists()
    private.symlink_to(ROOT/'sell_system/variables.py')
os.chdir(target);sys.path.insert(0,str(target));os.environ['DJANGO_SETTINGS_MODULE']='sell_system.settings'
from django.conf import settings
assert 'postgresql' in settings.DATABASES['default']['ENGINE']
import django
django.setup()
from django.db import connection
from django.core.management import call_command

if phase=='backup':
    db=settings.DATABASES['default'];env=os.environ.copy()
    for key,field in [('PGDATABASE','NAME'),('PGUSER','USER'),('PGPASSWORD','PASSWORD'),('PGHOST','HOST'),('PGPORT','PORT')]:
        if db.get(field):env[key]=str(db[field])
    dump=backup/'postgresql.dump'
    subprocess.run(['pg_dump','--format=custom','--no-owner','--no-acl','--file',str(dump)],env=env,check=True)
    os.chmod(dump,0o600)
    listing=subprocess.check_output(['pg_restore','--list',str(dump)],text=True)
    assert dump.stat().st_size>0 and 'Ventas_venta' in listing
    print(json.dumps({'backup':str(backup),'databaseArchiveReadable':True,'backupBytes':dump.stat().st_size,'verifiedFiles':len(old)}))
else:
    from django.db.migrations.executor import MigrationExecutor
    plan=MigrationExecutor(connection).migration_plan([('Tiendas',MIGRATION)])
    assert [(m.app_label,m.name,back) for m,back in plan]==[('Tiendas',MIGRATION,False)], 'Plan no acotado'
    assert {type(op).__name__ for op in plan[0][0].operations} <= {'CreateModel','AddField','AddConstraint'}
    assert [op.name for op in plan[0][0].operations if type(op).__name__=='CreateModel']==['CambioSuscripcion']
    with connection.cursor() as cur:
        cur.execute("SET lock_timeout='5s'");cur.execute("SET statement_timeout='60s'")
        cur.execute('SELECT COUNT(*) FROM "Tiendas_pagomembresia"');pagos_antes=cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM "Tiendas_tienda_membresia"');rutas_antes=cur.fetchone()[0]
    call_command('migrate','Tiendas',MIGRATION,interactive=False,verbosity=1)
    from Tiendas.models import Tienda_Membresia, PagoMembresia, CambioSuscripcion
    assert Tienda_Membresia.objects.count()==rutas_antes and PagoMembresia.objects.count()==pagos_antes
    assert not Tienda_Membresia.objects.filter(vencida_manualmente=True).exists()
    assert not PagoMembresia.objects.filter(estado='anulado').exists()
    assert not CambioSuscripcion.objects.exists()
    result={'migration':MIGRATION,'businessRowsPreserved':True,'manualExpirations':0,'annulledPayments':0}
    (backup/'migration.json').write_text(json.dumps(result));print(json.dumps(result))
