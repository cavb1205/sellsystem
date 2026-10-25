"""Prepara una copia privada con claves nuevas; nunca cambia el archivo activo."""
import argparse
import ast
import json
import os
from pathlib import Path
import secrets
import stat


def arbol_privado(text):
    try:
        return ast.parse(text)
    except SyntaxError:
        # SyntaxError normalmente imprime la línea original, que puede contener
        # una credencial real. No incluir ni encadenar ese diagnóstico.
        raise ValueError('El archivo privado no tiene sintaxis válida; no se modificó.') from None


def reemplazar(text, name, value, required=False):
    matches=[]
    for node in arbol_privado(text).body:
        target=node.targets[0] if isinstance(node,ast.Assign) and len(node.targets)==1 else (
            node.target if isinstance(node,ast.AnnAssign) else None)
        if isinstance(target,ast.Name) and target.id==name:matches.append(node)
    if not matches:
        if required:raise ValueError('No se encontró una asignación única de '+name)
        return text.rstrip()+'\n'+name+' = '+repr(value)+'\n'
    if len(matches)!=1:raise ValueError('Asignación ambigua de '+name)
    node=matches[0]
    lines=text.encode('utf-8').splitlines(keepends=True)
    start=sum(map(len,lines[:node.lineno-1]))+node.col_offset
    end=sum(map(len,lines[:node.end_lineno-1]))+node.end_col_offset
    raw=text.encode('utf-8')
    return (raw[:start]+(name+' = '+repr(value)).encode('utf-8')+raw[end:]).decode('utf-8')


def preparar(source,destination):
    source=Path(source);destination=Path(destination)
    if source.is_symlink() or source.resolve()==destination.resolve():
        raise ValueError('Usar un archivo privado original y un destino nuevo separado')
    info=source.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:
        raise ValueError('El archivo de origen debe ser privado, con permisos 600 o más restrictivos')
    original=source.read_text()
    content=reemplazar(original,'KEY',secrets.token_urlsafe(64),required=True)
    content=reemplazar(content,'JWT_SIGNING_KEY',secrets.token_urlsafe(64))
    arbol_privado(content)
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as stream:stream.write(content)
        os.chmod(destination,0o600)
        if (destination.stat().st_uid,destination.stat().st_gid)!=(info.st_uid,info.st_gid):
            os.chown(destination,info.st_uid,info.st_gid)
        assert source.read_text()==original, 'El origen cambió durante la preparación'
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {'keysPrepared':True,'sourceUnchanged':True,'destination':str(destination),'permissions':'600'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--destination',required=True)
    args=parser.parse_args()
    print(json.dumps(preparar(args.source,args.destination)))
