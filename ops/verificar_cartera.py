"""Verifica contratos en PostgreSQL READ ONLY. No imprime datos de clientes."""
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode = True
root = Path('/home/cavb1205/sellsystem/sellsystem')
assert root.is_dir()
os.chdir(root); sys.path.insert(0,str(root))
os.environ['DJANGO_SETTINGS_MODULE'] = 'sell_system.settings'
import django
django.setup()
from django.db import connection, transaction
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from Tiendas.alertas_operativas import rutas_del_usuario
from Ventas.models import Venta
from Ventas.riesgo import calcular_cuotas_atrasadas, importe_atrasado_registrado
assert connection.vendor == 'postgresql'

with patch('requests.sessions.Session.request',side_effect=AssertionError('Red externa prohibida')):
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute("SET LOCAL statement_timeout='30s'")
        user = get_user_model().objects.get(username='cavb1205')
        rutas = list(rutas_del_usuario(user)); ids = [r.id for r in rutas]
        assert ids
        tables = {'Tiendas_tienda': 'id', 'Ventas_venta': 'tienda_id', 'Recaudos_recaudo': 'tienda_id',
            'Clientes_cliente': 'tienda_id', 'Gastos_gasto': 'tienda_id', 'Aportes_aporte': 'tienda_id',
            'Utilidades_utilidad': 'tienda_id', 'Tiendas_cierre_caja': 'tienda_id',
            'Tiendas_movimientocaja': 'tienda_id', 'Tiendas_alertaoperativa': 'tienda_id_ref'}
        def signatures():
            result = {}
            with connection.cursor() as cur:
                for table, field in tables.items():
                    cur.execute('SELECT COUNT(*), md5(COALESCE(string_agg(t::text, chr(10) ORDER BY id),\'\')) FROM '
                        + connection.ops.quote_name(table) + ' t WHERE ' + connection.ops.quote_name(field) + ' IN ('
                        + ','.join(['%s']*len(ids)) + ')',ids)
                    result[table] = cur.fetchone()
            return result
        before = signatures()
        api = APIClient(HTTP_HOST='api.carterafinanciera.com'); api.force_authenticate(user)
        verified = 0; repeated_days = 0; changed_estimates = 0
        for ruta in rutas:
            response = api.get(f'/ventas/activas/t/{ruta.pk}/')
            assert response.status_code == 200
            data = response.data if isinstance(response.data,list) else []
            objects = {v.pk:v for v in Venta.objects.filter(tienda=ruta).exclude(estado_venta__in=('Pagado','Perdida'))}
            assert {d['id'] for d in data} == set(objects)
            for d in data:
                v = objects[d['id']]
                assert d['tienda'] == ruta.pk
                assert Decimal(str(d['saldo_actual'])) == v.saldo_actual
                assert Decimal(str(d['valor_cuota'])) == v.valor_cuota()
                if v.plazo == 'Diario':
                    qs = v.recaudo_set.filter(es_renovacion=False)
                    days, receipts = qs.values('fecha_recaudo').distinct().count(), qs.count()
                    repeated_days += int(days < receipts)
                    expected = calcular_cuotas_atrasadas(v.valor_cuota(),days,v.total_abonado())
                    assert Decimal(str(d['dias_atrasados'])) == expected == v.dias_atrasados()
                    changed_estimates += int(expected != calcular_cuotas_atrasadas(v.valor_cuota(),receipts,v.total_abonado()))
                    exact = importe_atrasado_registrado(v.valor_cuota(),days,v.total_abonado(),v.saldo_actual,v.dias_sin_abono())
                    assert Decimal(str(d['importe_atrasado_estimado'])) == exact
                verified += 1
            audit = api.get(f'/tiendas/controles/inconsistencias/t/{ruta.pk}/')
            assert audit.status_code == 200 and audit.data['ruta']['id'] == ruta.pk
            assert audit.data['resumen']['casos'] >= len(audit.data['casos'])
            assert all(c['tienda']['id'] == ruta.pk for c in audit.data['casos'])
        assert signatures() == before
        denied = APIClient(HTTP_HOST='api.carterafinanciera.com')
        denied.force_authenticate(get_user_model()(id=-987654,username='sin-acceso',is_staff=True))
        assert denied.get(f'/ventas/activas/t/{ids[0]}/').status_code == 403
        assert APIClient(HTTP_HOST='api.carterafinanciera.com').get(f'/ventas/activas/t/{ids[0]}/').status_code == 401
        print(json.dumps({'soloLectura':True, 'rutasVerificadas':len(ids), 'creditosVerificados':verified,
            'creditosConRecibosMismaFecha':repeated_days, 'estimacionesCorregidas':changed_estimates,
            'contratosYPermisos':True,'datosFinancierosIntactos':True,'notificacionesEnviadas':0}))
