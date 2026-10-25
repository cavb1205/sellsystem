"""Genera locks verificables para wheels de las versiones fijadas en PyPI."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def generar(source, destination):
    lines = ['# Generado con ops/bloquear_dependencias.py; usar Python 3.12.',
             '# Instalar con --require-hashes --only-binary=:all:.', '']
    count = 0
    for value in source.read_text().splitlines():
        value = value.strip()
        if not value or value.startswith('#'): continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)', value)
        assert match, 'Requisito sin versión exacta: '+value
        name, version = match.groups()
        result = subprocess.run(['curl','--fail','--silent','--show-error','--max-time','20',
            'https://pypi.org/pypi/'+name+'/'+version+'/json'], capture_output=True,text=True,check=True)
        metadata = json.loads(result.stdout)
        assert metadata['info']['version']==version
        hashes = sorted({entry['digests']['sha256'] for entry in metadata['urls']
                         if entry['packagetype']=='bdist_wheel' and not entry.get('yanked')})
        assert hashes and all(re.fullmatch('[a-f0-9]{64}',value) for value in hashes)
        lines.append(name+'=='+version+' \\')
        for index, digest in enumerate(hashes):
            lines.append('    --hash=sha256:'+digest+(' \\' if index<len(hashes)-1 else ''))
        lines.append('')
        count += 1
    destination.write_text('\n'.join(lines))
    return count


if __name__=='__main__':
    print(json.dumps({'runtimePackages':generar(ROOT/'requirements.txt',ROOT/'requirements.lock'),
        'toolPackages':generar(ROOT/'requirements-tools.txt',ROOT/'requirements-tools.lock')}))
