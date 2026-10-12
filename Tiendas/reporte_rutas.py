"""Cifras y presentación del informe diario. Sin escrituras ni envíos."""
import html
import re
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone
from Recaudos.models import Recaudo
from Ventas.cobranza_dia import cuotas_del_dia

LIMITE_TEXTO = 3500  # Margen bajo el límite de 4096, incluso con emojis.


def dinero(valor):
    monto = Decimal(str(valor or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    formato = f'{monto:,.2f}' if monto % 1 else f'{monto:,.0f}'
    return '$' + formato.replace(',', '_').replace('.', ',').replace('_', '.')


def escape(valor, limite=140):
    texto = ' '.join(str(valor or '—').split())
    return html.escape(texto if len(texto) <= limite else texto[:limite - 1] + '…')


def cumplimiento_dia(tienda, fecha):
    registros = {}
    recaudo_total = Decimal('0')
    registros_no_pago = 0
    for pago in Recaudo.objects.filter(tienda=tienda, fecha_recaudo=fecha,
            es_renovacion=False).values('venta_id', 'valor_recaudo'):
        monto = pago['valor_recaudo'] or Decimal('0')
        datos = registros.setdefault(pago['venta_id'], {'abono': Decimal('0'), 'sin_abono': 0})
        datos['abono'] += monto
        recaudo_total += monto
        if monto <= 0:
            datos['sin_abono'] += 1
            registros_no_pago += 1
    esperado = cubierto = pendiente = Decimal('0')
    detalles = []
    for venta, cuota in cuotas_del_dia(tienda, fecha):
        if cuota <= 0:
            continue
        registro = registros.get(venta.id)
        abono = max(Decimal('0'), registro['abono'] if registro else Decimal('0'))
        cubierto_venta = min(cuota, abono)
        esperado += cuota
        cubierto += cubierto_venta
        pendiente += cuota - cubierto_venta
        estado = 'sin_registro' if registro is None else 'con_abono' if abono > 0 else 'sin_abono'
        detalles.append({'venta_id': venta.id, 'cliente_id': venta.cliente_id,
            'cliente': f'{venta.cliente.nombres} {venta.cliente.apellidos}'.strip(),
            'cuota': str(cuota), 'abonado': str(abono),
            'pendiente': str(cuota - cubierto_venta), 'estado': estado})
    contar = lambda estado: sum(d['estado'] == estado for d in detalles)
    return {'esperado': esperado, 'recaudo': recaudo_total,
        'pendiente_recaudo': pendiente, 'cobertura_recaudo': cubierto,
        'cobros_adicionales': recaudo_total - cubierto,
        'programadas': len(detalles), 'con_gestion': len(detalles) - contar('sin_registro'),
        'con_abono': contar('con_abono'), 'sin_gestion': contar('sin_registro'),
        'sin_abono': contar('sin_abono'), 'fallas': registros_no_pago,
        'fallas_programadas': contar('sin_abono'), 'creditos_detalle': detalles}


def prioridad(ruta):
    return (-ruta.get('alertas_criticas', 0), -ruta.get('sin_gestion', 0),
        -ruta.get('alertas_urgentes', 0), -Decimal(str(ruta.get('pendiente_recaudo', 0))),
        str(ruta['nombre']).casefold())


def bloque_ruta(ruta):
    esperado = Decimal(str(ruta['esperado']))
    cubierto = Decimal(str(ruta['cobertura_recaudo']))
    pendiente = Decimal(str(ruta['pendiente_recaudo']))
    revisar = pendiente > 0 or not ruta['cierre'] or ruta['alertas_criticas'] or ruta['alertas_urgentes']
    cobertura = f'{cubierto / esperado * 100:.1f}%'.replace('.', ',') if esperado else 'sin cuotas programadas'
    lineas = [f'{"⚠️" if revisar else "✅"} <b>{escape(ruta["nombre"])}</b>',
        f'Cuotas del día: <b>{dinero(esperado)}</b>',
        f'Total recaudado: <b>{dinero(ruta["recaudo"])}</b>',
        f'Cuotas cubiertas: {dinero(cubierto)} · {cobertura}',
        f'Cuotas pendientes: <b>{dinero(pendiente)}</b>',
        f'Cobros adicionales: {dinero(ruta["cobros_adicionales"])}',
        f'{ruta["programadas"]} créditos con cuota: {ruta["con_abono"]} con abono · '
        f'{ruta["sin_abono"]} registrados sin abono · <b>{ruta["sin_gestion"]} sin registro</b>',
        f'Cierre: {"registrado" if ruta["cierre"] else "no registrado"}',
        f'Alertas abiertas: {ruta["alertas_criticas"]} críticas · {ruta["alertas_urgentes"]} urgentes']
    acciones = []
    if ruta['sin_gestion']:
        acciones.append('revisar créditos sin registro')
    elif pendiente > 0:
        acciones.append('dar seguimiento a cuotas pendientes')
    if not ruta['cierre']:
        acciones.append('comprobar el cierre')
    if ruta['alertas_criticas'] or ruta['alertas_urgentes']:
        acciones.append('revisar alertas prioritarias')
    if acciones:
        lineas.append('Revisar: ' + '; '.join(acciones) + '.')
    return '\n'.join(lineas)


def longitud_visible(texto):
    plano = html.unescape(re.sub(r'<[^>]*>', '', texto))
    return len(plano.encode('utf-16-le')) // 2


def partes_resumen(rutas, fecha, corte=None):
    corte = timezone.localtime(corte or timezone.now())
    ordenadas = sorted(rutas, key=prioridad)
    grupos, grupo = [], []
    cabecera = (f'📊 <b>REPORTE DE RUTAS</b> · Jornada {fecha:%d/%m/%Y}\n'
        f'Alertas: corte {corte:%d/%m/%Y %H:%M} · Santiago\n'
        f'{len(rutas)} rutas · importes por ruta, sin consolidar monedas.\n')
    pie = ('\n\n<i>Los cobros adicionales no cubren cuotas de otros créditos. '
        'Sin registro no prueba falta de visita; cierre registrado no prueba cuadre de efectivo.</i>')
    for ruta in ordenadas:
        candidato = grupo + [ruta]
        texto = cabecera + '\n\n'.join(bloque_ruta(r) for r in candidato) + pie
        if grupo and longitud_visible(texto) > LIMITE_TEXTO - 50:
            grupos.append(grupo)
            grupo = [ruta]
        else:
            grupo = candidato
    if grupo:
        grupos.append(grupo)
    if not grupos:
        return [{'texto': cabecera + '\nNo hay rutas autorizadas para este reporte.', 'rutas': []}]
    partes = []
    for numero, grupo in enumerate(grupos, 1):
        texto = cabecera + f'Parte {numero}/{len(grupos)}\n\n' + '\n\n'.join(bloque_ruta(r) for r in grupo) + pie
        assert longitud_visible(texto) <= LIMITE_TEXTO
        partes.append({'texto': texto, 'rutas': [r['id'] for r in grupo]})
    return partes
