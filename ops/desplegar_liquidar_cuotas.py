"""Despliegue acotado de un commit verificado. Configuración privada fuera de Git.

Fases: backup|activate|health MANIFEST.json [backup-directory].
El manifiesto contiene hashes del commit anterior, del nuevo y archivos cambiados.
No ejecuta migraciones ni escribe registros del negocio.
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
phase, manifest_path = sys.argv[1:3]
assert phase in ('backup', 'activate', 'health')
manifest = json.loads(Path(manifest_path).read_text())
assert len(manifest['commit']) == 40
old, new, changed = manifest['old'], manifest['new'], manifest['changed']
assert all(not Path(p).is_absolute() and '..' not in Path(p).parts and p != 'sell_system/variables.py' for p in new)


def verify(root, files):
    bad = [p for p,h in files.items() if not (root/p).is_file() or hashlib.sha256((root/p).read_bytes()).hexdigest()!=h]
    assert not bad, 'Código distinto del commit: '+str(bad)


def replace(source, destination):
    info = destination.stat() if destination.exists() else destination.parent.stat()
    tmp = destination.with_name(destination.name+'.liquidar-cuotas-release-tmp')
    assert not tmp.exists()
    shutil.copyfile(source,tmp)
    os.chmod(tmp,info.st_mode&0o777 if destination.exists() else 0o644)
    os.chown(tmp,info.st_uid,info.st_gid)
    os.replace(tmp,destination)


if phase == 'backup':
    verify(ROOT,old)
    backup=Path(tempfile.mkdtemp(prefix='20261010-liquidar-cuotas-',dir=BASE));os.chmod(backup,0o700)
    for p in changed:
        if (ROOT/p).exists():
            dest=backup/'codigo'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/p,dest)
    (backup/'release').mkdir()
else:
    backup=Path(sys.argv[3]).resolve()
    assert backup.parent==BASE and backup.name.startswith('20261010-liquidar-cuotas-') and backup.is_dir()

if phase == 'activate':
    verify(ROOT,old);verify(backup/'release',new)
    assert (backup/'postgresql.dump').stat().st_size>0
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

target=ROOT
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
