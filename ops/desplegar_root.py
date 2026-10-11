"""Release root 0027/0028 desde un commit de GitHub, con respaldo y hashes.

Local: prepare COMMIT_BASE. Remoto: preflight|backup|migrate|activate|health
MANIFEST [backup-directory]. Nunca importa configuración privada en local.
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
HOST = 'root@137.184.176.199'
MIGRATIONS = ['0027_root_integridad', '0028_root_estados_configuracion']
phase = sys.argv[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(root, files):
    bad = [p for p, h in files.items() if not (root / p).is_file() or sha(root / p) != h]
    assert not bad, 'Código distinto del commit: ' + str(bad)


if phase == 'prepare':
    previous = sys.argv[2]
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    assert subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip() == ''
    baseline = json.loads(subprocess.check_output(['git', 'show', previous + ':docs/production-source-sha256.json']))
    tracked = subprocess.check_output(['git', 'ls-files'], text=True).splitlines()
    selected = [p for p in tracked if p.endswith('.py') or p.startswith(('ops/', 'docs/')) or p == 'requirements.txt']
    new = {p: sha(Path(p)) for p in selected}
    old = {p: hashlib.sha256(subprocess.check_output(['git', 'show', previous + ':' + p])).hexdigest() for p in baseline}
    optional = {}
    for p in selected:
        if p in old:
            continue
        value = subprocess.run(['git', 'show', previous + ':' + p], capture_output=True)
        optional[p] = hashlib.sha256(value.stdout).hexdigest() if value.returncode == 0 else None
    directory = Path(tempfile.mkdtemp(prefix='cartera-root-release-'))
    manifest = {'previousCommit': previous, 'commit': commit, 'old': old, 'new': new,
        'optionalOld': optional, 'changed': [p for p, h in new.items() if old.get(p) != h]}
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    subprocess.run(['git', 'archive', '--format=tar', '--output=' + str(directory / 'release.tar'), commit], check=True)
    print(json.dumps({'directory': str(directory), 'commit': commit, 'previousCommit': previous,
        'oldFiles': len(old), 'newFiles': len(new)}))
    sys.exit(0)

assert phase in ('preflight', 'backup', 'migrate', 'activate', 'health')
manifest_path = Path(sys.argv[2])
manifest = json.loads(manifest_path.read_text())
old, new = manifest['old'], manifest['new']
assert len(manifest['commit']) == 40 and len(manifest['previousCommit']) == 40
assert all(not Path(p).is_absolute() and '..' not in Path(p).parts and p != 'sell_system/variables.py' for p in new)

if phase in ('preflight', 'backup'):
    verify(ROOT, old)
    for p, expected in manifest.get('optionalOld', {}).items():
        dest = ROOT / p
        if dest.exists():
            assert not dest.is_symlink(), p
            actual = sha(dest)
            assert actual in (expected, new[p]), 'Archivo adicional distinto de GitHub: ' + p
            old[p] = actual
    manifest['old'] = old
    manifest['changed'] = [p for p, h in new.items() if old.get(p) != h]
    manifest_path.write_text(json.dumps(manifest, indent=2))
    if phase == 'preflight':
        print(json.dumps({'preflight': True, 'previousCommit': manifest['previousCommit'],
            'verifiedOldFiles': len(old), 'changedFiles': len(manifest['changed'])}))
        sys.exit(0)

changed = manifest['changed']
if phase == 'backup':
    backup = Path(tempfile.mkdtemp(prefix='20261011-root-', dir=BASE))
    os.chmod(backup, 0o700)
    for p in changed:
        if (ROOT / p).exists():
            dest = backup / 'codigo' / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / p, dest)
    (backup / 'release').mkdir()
else:
    backup = Path(sys.argv[3]).resolve()
    assert backup.parent == BASE and backup.name.startswith('20261011-root-') and backup.is_dir()


def replace(source, destination):
    assert not destination.is_symlink()
    info = destination.stat() if destination.exists() else destination.parent.stat()
    tmp = destination.with_name(destination.name + '.root-release-tmp')
    assert not tmp.exists()
    shutil.copyfile(source, tmp)
    os.chmod(tmp, info.st_mode & 0o777 if destination.exists() else 0o644)
    os.chown(tmp, info.st_uid, info.st_gid)
    os.replace(tmp, destination)


if phase == 'activate':
    verify(ROOT, old)
    verify(backup / 'release', new)
    assert (backup / 'postgresql.dump').stat().st_size > 0 and (backup / 'migration.json').is_file()
    services = {}
    for service in ('sellsystem', 'telegram-clientes-webhook'):
        pid = int(subprocess.check_output(['supervisorctl', 'pid', service], text=True).strip())
        command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'args='], text=True)
        assert '/home/cavb1205/sellsystem/venv/bin/gunicorn sell_system.wsgi' in command
        workers = [int(p) for p in subprocess.check_output(['ps', '--ppid', str(pid), '-o', 'pid='], text=True).split()]
        log = Path('/var/log/sellsystem.err.log' if service == 'sellsystem' else '/var/log/telegram-clientes-webhook.log')
        services[service] = {'pid': pid, 'workers': workers, 'log': str(log), 'offset': log.stat().st_size}
    copied = []
    try:
        for p in changed:
            source = backup / 'release' / p
            if p.endswith('.py'):
                compile(source.read_bytes(), p, 'exec')
            (ROOT / p).parent.mkdir(parents=True, exist_ok=True)
            replace(source, ROOT / p)
            copied.append(p)
        subprocess.run([sys.executable, 'manage.py', 'check'], cwd=ROOT, check=True)
        verify(ROOT, new)
    except BaseException:
        for p in reversed(copied):
            if (backup / 'codigo' / p).exists():
                replace(backup / 'codigo' / p, ROOT / p)
            else:
                dest = backup / 'desactivado' / p
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(ROOT / p), dest)
        verify(ROOT, old)
        raise
    (backup / 'workers.json').write_text(json.dumps(services))
    (backup / 'commit.json').write_text(json.dumps({'commit': manifest['commit'], 'files': len(new)}))
    for info in services.values():
        os.kill(info['pid'], signal.SIGHUP)
    print(json.dumps({'activated': True, 'commit': manifest['commit'], 'verifiedFiles': len(new)}))
    sys.exit(0)

if phase == 'health':
    verify(ROOT, new)
    result = []
    for service, info in json.loads((backup / 'workers.json').read_text()).items():
        assert 'RUNNING' in subprocess.check_output(['supervisorctl', 'status', service], text=True)
        assert not any(Path('/proc/' + str(pid)).exists() for pid in info['workers']), 'Workers anteriores activos'
        with open(info['log'], 'rb') as stream:
            stream.seek(info['offset'])
            lines = stream.read().decode(errors='replace').splitlines()
        errors = sum(any(t in line for t in ('Traceback', '[ERROR]', '[CRITICAL]', 'Internal Server Error:')) for line in lines)
        assert not errors, service + ' tiene errores nuevos'
        result.append({'service': service, 'state': 'RUNNING', 'newErrors': errors})
    assert subprocess.check_output(['systemctl', 'is-active', 'nginx'], text=True).strip() == 'active'
    print(json.dumps({'sourceMatchesGitHubCommit': manifest['commit'], 'verifiedFiles': len(new), 'services': result, 'nginx': 'active'}))
    sys.exit(0)

target = backup / 'release' if phase == 'migrate' else ROOT
if phase == 'migrate':
    verify(ROOT, old)
    verify(target, new)
    private = target / 'sell_system/variables.py'
    assert not private.exists()
    private.symlink_to(ROOT / 'sell_system/variables.py')
os.chdir(target)
sys.path.insert(0, str(target))
os.environ['DJANGO_SETTINGS_MODULE'] = 'sell_system.settings'
import django
django.setup()
from django.conf import settings
from django.db import connection, transaction
from django.core.management import call_command
assert 'postgresql' in settings.DATABASES['default']['ENGINE']

if phase == 'backup':
    db = settings.DATABASES['default']
    env = os.environ.copy()
    for key, field in [('PGDATABASE', 'NAME'), ('PGUSER', 'USER'), ('PGPASSWORD', 'PASSWORD'), ('PGHOST', 'HOST'), ('PGPORT', 'PORT')]:
        if db.get(field):
            env[key] = str(db[field])
    dump = backup / 'postgresql.dump'
    subprocess.run(['pg_dump', '--format=custom', '--no-owner', '--no-acl', '--file', str(dump)], env=env, check=True)
    os.chmod(dump, 0o600)
    listing = subprocess.check_output(['pg_restore', '--list', str(dump)], text=True)
    assert dump.stat().st_size > 0 and 'Ventas_venta' in listing
    print(json.dumps({'backup': str(backup), 'databaseArchiveReadable': True, 'backupBytes': dump.stat().st_size, 'verifiedFiles': len(old)}))
else:
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan([('Tiendas', MIGRATIONS[-1])])
    assert [(m.app_label, m.name, back) for m, back in plan] == [('Tiendas', m, False) for m in MIGRATIONS], 'Plan no acotado'
    assert {type(op).__name__ for m, _ in plan for op in m.operations} <= {'CreateModel', 'AddField', 'RunPython'}
    # Misma instantánea PostgreSQL antes/después: otros cobros concurrentes no
    # parecen modificaciones de la migración y no se detienen las operaciones.
    tables = ['Tiendas_tienda', 'Tiendas_tienda_membresia', 'Tiendas_pagomembresia', 'Tiendas_cuentadestino',
        'Ventas_venta', 'Clientes_cliente', 'Recaudos_recaudo', 'Gastos_gasto', 'Aportes_aporte',
        'Utilidades_utilidad', 'Tiendas_cierre_caja', 'Tiendas_movimientocaja', 'Tiendas_cambiosuscripcion']
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            cur.execute("SET LOCAL lock_timeout='5s'")
            cur.execute("SET LOCAL statement_timeout='60s'")
            columns = {}
            for table in tables:
                cur.execute('SELECT column_name FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position', [table])
                columns[table] = [r[0] for r in cur.fetchall()]
                assert columns[table], table
        def fingerprints():
            values = {}
            with connection.cursor() as cur:
                for table, fields in columns.items():
                    names = ','.join(connection.ops.quote_name(f) for f in fields)
                    cur.execute('SELECT COUNT(*), md5(COALESCE(string_agg(row(' + names + ')::text, chr(10) ORDER BY id),\'\')) FROM ' + connection.ops.quote_name(table))
                    values[table] = cur.fetchone()
            return values
        before = fingerprints()
        call_command('migrate', 'Tiendas', MIGRATIONS[-1], interactive=False, verbosity=1)
        assert fingerprints() == before, 'La migración cambió datos financieros anteriores'
        from Tiendas.models import CambioConfiguracionRoot, SolicitudPago
        assert not CambioConfiguracionRoot.objects.exists()
        assert not SolicitudPago.objects.exclude(cuenta_acordada={}).exists()
    result = {'migrations': MIGRATIONS, 'businessRowsPreserved': True, 'tablesCompared': len(tables),
        'historicalAccountsInvented': False, 'configurationHistoryInvented': False}
    (backup / 'migration.json').write_text(json.dumps(result))
    print(json.dumps(result))
