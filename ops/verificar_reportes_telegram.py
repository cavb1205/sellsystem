"""Contratos reales en READ ONLY; nunca prepara fotos ni envía Telegram."""
import json
import os
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
sys.dont_write_bytecode = True
import requests
def sin_red(*args, **kwargs):
    raise AssertionError('Notificaciones prohibidas durante verificación')
requests.sessions.Session.request = sin_red
root = Path(sys.argv[1] if len(sys.argv) > 1 else '/home/cavb1205/sellsystem/sellsystem').resolve()
assert root == Path('/home/cavb1205/sellsystem/sellsystem') or (
    root.name == 'release' and root.parent.parent == Path('/home/cavb1205/sellsystem/.deploy-backups'))
os.chdir(root);sys.path.insert(0,str(root))
os.environ['DJANGO_SETTINGS_MODULE'] = 'sell_system.settings'
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from Tiendas.alertas_operativas import rutas_del_usuario, resumen_operativo
from Tiendas.reporte_rutas import partes_resumen, longitud_visible
from Tiendas.entrega_reporte_rutas import botones_habilitados
from Ventas.views import list_ventas_a_liquidar
assert connection.vendor == 'postgresql'
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cursor.execute("SET LOCAL statement_timeout='30s'")
    usuario = get_user_model().objects.get(username='cavb1205')
    rutas = rutas_del_usuario(usuario)
    ids = list(rutas.values_list('id',flat=True))
    assert ids
    tables = {'Tiendas_tienda':'id','Clientes_cliente':'tienda_id','Ventas_venta':'tienda_id',
        'Recaudos_recaudo':'tienda_id','Tiendas_cierre_caja':'tienda_id',
        'Tiendas_movimientocaja':'tienda_id','Tiendas_alertaoperativa':'tienda_id_ref',
        'Aportes_aporte':'tienda_id','Gastos_gasto':'tienda_id','Utilidades_utilidad':'tienda_id'}
    def firmas():
        result = {}
        with connection.cursor() as cursor:
            for tabla,campo in tables.items():
                cursor.execute('SELECT count(*), md5(COALESCE(string_agg(t::text, chr(10) ORDER BY id),\'\')) FROM '
                    + connection.ops.quote_name(tabla) + ' t WHERE ' + connection.ops.quote_name(campo)
                    + ' IN (' + ','.join(['%s'] * len(ids)) + ')', ids)
                result[tabla] = cursor.fetchone()
        return result
    antes = firmas()
    fecha = timezone.localdate() - timedelta(days=1)
    filas = resumen_operativo(fecha,rutas=rutas)
    assert {r['id'] for r in filas} == set(ids)
    request_factory = APIRequestFactory()
    metas = []
    for fila in filas:
        request = request_factory.get('/',{'resumen':'1'})
        force_authenticate(request,user=usuario)
        response = list_ventas_a_liquidar(request,date=str(fecha),tienda_id=str(fila['id']))
        assert response.status_code == 200, response.status_code
        meta = Decimal(response.data['resumen_cobranza']['total_a_cobrar'])
        assert meta == fila['esperado']
        assert meta == fila['cobertura_recaudo'] + fila['pendiente_recaudo']
        assert fila['recaudo'] == fila['cobertura_recaudo'] + fila['cobros_adicionales']
        assert fila['programadas'] == fila['con_abono'] + fila['sin_abono'] + fila['sin_gestion']
        assert len({d['venta_id'] for d in fila['creditos_detalle']}) == fila['programadas']
        metas.append({'ruta':fila['nombre'],'cuotas':str(meta),'creditos_con_cuota':fila['programadas']})
    partes = partes_resumen(filas,fecha)
    assert {ruta for parte in partes for ruta in parte['rutas']} == set(ids)
    assert all(longitud_visible(parte['texto']) <= 3500 for parte in partes)
    assert all('Umbrales nuevos' not in parte['texto'] for parte in partes)
    assert firmas() == antes
    print(json.dumps({'soloLectura':True,'fecha':str(fecha),'rutasVerificadas':len(ids),
        'metasIgualesALiquidar':metas,'partes':len(partes),
        'longitudesVisibles':[longitud_visible(p['texto']) for p in partes],
        'botonesDisponibles':botones_habilitados(usuario),
        'finanzasIntactas':True,'notificacionesEnviadas':0},ensure_ascii=False))
