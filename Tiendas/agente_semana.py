"""Informe semanal de salud de la cartera para el asistente del administrador (Jarvis).

``GET /tiendas/agente/semana/?fecha=ayer[&ruta=]``: la semana son los 7 días que terminan
en ``fecha``. Misma autenticación, alcance y garantía de solo lectura que
``Tiendas/agente_api.py``.

El informe diario cuenta lo que pasó ayer; este mide el estado: cuánta plata está en
riesgo y si sube o baja, qué créditos se pueden perder, cómo cobra cada trabajador y si
alguien está mirando las alertas. Los criterios de "en riesgo" y "posible pérdida" son del
informe (constantes de ``agente_informe``), salvo el candidato a castigo, que es de la app.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from rest_framework import status
from rest_framework.response import Response

from Recaudos.models import Recaudo
from Tiendas.agente_api import _fecha_ruta, _filtrar_rutas, _n, _trabajadores, endpoint_agente
from Tiendas.agente_informe import (
    HITOS_INFORME, _abonan_poco, _abonos_tarde, _activos_al, _con_saldo_al, _gastos, _nombre,
    _registra_administrador, _sin_abonar, _sobre_cupo_por_trabajador, _vencidos, _ventana,
)
from Tiendas.models import AlertaOperativa, MovimientoCaja
from Ventas.models import DeclaracionPerdidaVenta, Venta

DIAS_COSECHA = (15, 120)        # créditos dados hace 15 a 120 días: ya se ve cómo pagan
PERDIDA_SIN_ABONAR = 60         # criterio del informe para "posible pérdida"
PERDIDA_PASADO_PLAZO = 30
LIMITE = 10

INTERPRETACION = (
    'Salud semanal de cada ruta. en_riesgo = créditos con 30+ días sin abonar, que abonan poco '
    '(7+ cuotas atrasadas) o que pasaron su fecha final con saldo, contados una sola vez por crédito. '
    'hace_7_dias es la misma medida reconstruida a esa fecha. posibles_perdidas: 60+ días sin abonar '
    'o 30+ días pasado el plazo (criterio del informe); candidato_castigo (90+) es el de la app. '
    'cobranza.cumplimiento_pct = cuotas cubiertas / cuotas exigidas de la semana. Los importes de '
    'cada ruta están en su propia moneda y escala: nunca se suman ni se comparan entre rutas.')


def _suma(filas):
    return sum((Decimal(str(f['saldo'] or 0)) for f in filas), Decimal('0'))


def _riesgo(tienda, fecha):
    sin_abonar = _sin_abonar(tienda, fecha)
    poco = _abonan_poco(tienda, fecha, excluir={f['venta_id'] for f in sin_abonar if f['tramo'] >= 2})
    vencidos = _vencidos(tienda, fecha)
    cartera = _suma({'saldo': v.saldo_actual} for v in _con_saldo_al(_activos_al(tienda, fecha)))
    graves = [f for f in sin_abonar if f['dias_sin_abonar'] >= HITOS_INFORME[0]]
    # Un crédito puede estar en las tres listas: se cuenta una vez.
    en_riesgo = {f['venta_id']: f for f in graves + poco + vencidos}
    total = _suma(en_riesgo.values())
    medida = lambda filas: {'creditos': len(filas), 'saldo': _n(_suma(filas))}
    resumen = {
        'saldo_cartera': _n(cartera),
        'sin_abonar_30_o_mas': medida(graves), 'abonan_poco': medida(poco), 'vencidos_con_saldo': medida(vencidos),
        'en_riesgo': {'creditos': len(en_riesgo), 'saldo': _n(total),
                      'pct_cartera': round(float(total / cartera * 100), 1) if cartera else None},
    }
    return resumen, sin_abonar, poco, vencidos


def _posibles_perdidas(sin_abonar, vencidos):
    filas = {}
    for f in sin_abonar:
        # Nunca por debajo del tramo crítico de la app para ese plazo (75 en mensual).
        if f['dias_sin_abonar'] >= max(PERDIDA_SIN_ABONAR, f['critico_app']):
            filas[f['venta_id']] = {
                'cliente': f['cliente'], 'venta_id': f['venta_id'], 'saldo': f['saldo'], 'plazo': f['plazo'],
                'dias_sin_abonar': f['dias_sin_abonar'], 'sin_primer_abono': f['sin_primer_abono'],
                'visitas_sin_pago_desde_ultimo_abono': f['visitas_sin_pago_desde_ultimo_abono'],
                'candidato_castigo': f['candidato_castigo'], 'dias_pasado_el_plazo': None}
    for f in vencidos:
        if f['dias_pasado_el_plazo'] >= PERDIDA_PASADO_PLAZO:
            fila = filas.setdefault(f['venta_id'], {
                'cliente': f['cliente'], 'venta_id': f['venta_id'], 'saldo': f['saldo'], 'plazo': f['plazo'],
                'dias_sin_abonar': None,
                'candidato_castigo': False})
            fila['dias_pasado_el_plazo'] = f['dias_pasado_el_plazo']
    orden = sorted(filas.values(), key=lambda f: -(f['saldo'] or 0))
    return {'creditos': len(orden), 'saldo': _n(_suma(orden)), 'lista': orden[:LIMITE]}


def _cobranza(tienda, hasta):
    from Tiendas.reporte_rutas import cumplimiento_dia
    exigido = cubierto = recaudado = Decimal('0')
    for k in range(7):
        c = cumplimiento_dia(tienda, hasta - timedelta(days=k))
        exigido += c['esperado'] or 0
        cubierto += c['cobertura_recaudo'] or 0
        recaudado += c['recaudo'] or 0
    return {'cuotas_exigidas': _n(exigido), 'recaudado': _n(recaudado),
            'cumplimiento_pct': round(float(cubierto / exigido * 100)) if exigido else None}


def _creditos(tienda, desde, hasta):
    ventas = list(Venta.objects.filter(tienda=tienda, fecha_venta__gte=desde, fecha_venta__lte=hasta)
                  .values('valor_venta', 'origen_renovacion_id'))
    nuevos = [v for v in ventas if not v['origen_renovacion_id']]
    sobre_cupo = _sobre_cupo_por_trabajador(tienda, desde, hasta)
    return {'nuevos': len(nuevos), 'monto_nuevos': _n(sum((v['valor_venta'] for v in nuevos), Decimal('0'))),
            'renovaciones': len(ventas) - len(nuevos),
            'sobre_cupo': sum(c['creditos'] for c in sobre_cupo.values())}, sobre_cupo


def _trabajadores_semana(tienda, desde, hasta, sobre_cupo, en_mora, admin_id):
    inicio, _ = _ventana(tienda, desde)
    _, fin = _ventana(tienda, hasta)
    filas = defaultdict(lambda: {'abonos': 0, 'monto_abonos': Decimal('0'), 'abonos_eliminados_o_rebajados': 0,
                                 'monto_eliminado_o_rebajado': Decimal('0'), 'creditos_dados': 0,
                                 'monto_creditos': Decimal('0'), 'creditos_aumentados': 0,
                                 'abonos_registrados_tarde': 0})
    usuarios = {}
    for m in MovimientoCaja.objects.filter(tienda=tienda, creado_en__gte=inicio, creado_en__lt=fin,
                                           usuario__isnull=False).select_related('usuario'):
        usuarios[m.usuario_id] = m.usuario
        f = filas[m.usuario_id]
        if m.tipo == 'RECAUDO' and m.accion == 'CREACION' and m.delta > 0:
            f['abonos'] += 1
            f['monto_abonos'] += m.delta
        elif m.tipo == 'RECAUDO' and m.delta < 0:
            f['abonos_eliminados_o_rebajados'] += 1
            f['monto_eliminado_o_rebajado'] -= m.delta
        elif m.tipo == 'VENTA' and m.accion == 'CREACION':
            f['creditos_dados'] += 1
            f['monto_creditos'] -= m.delta
        elif m.tipo == 'VENTA' and m.accion == 'CORRECCION' and m.delta < 0:
            f['creditos_aumentados'] += 1
    for t in _abonos_tarde(tienda, inicio, fin, admin_id):
        if t['usuario_id'] in filas:
            filas[t['usuario_id']]['abonos_registrados_tarde'] += 1
    # Cosecha: cómo están hoy los créditos nuevos que dio cada uno (creado_por; nulo en históricos).
    cosecha = defaultdict(lambda: {'creditos': 0, 'en_mora': 0})
    for venta_id, creador in Venta.objects.filter(
        tienda=tienda, origen_renovacion__isnull=True,
        fecha_venta__gte=hasta - timedelta(days=DIAS_COSECHA[1]),
        fecha_venta__lte=hasta - timedelta(days=DIAS_COSECHA[0]),
    ).values_list('id', 'creado_por_id'):
        cosecha[creador]['creditos'] += 1
        cosecha[creador]['en_mora'] += int(venta_id in en_mora)
    lista = []
    for usuario_id, f in filas.items():
        if admin_id and usuario_id == admin_id:
            continue  # el dueño; los coadministradores sí se evalúan
        c = cosecha.get(usuario_id, {'creditos': 0, 'en_mora': 0})
        lista.append({
            'nombre': _nombre(usuarios[usuario_id]), **{k: _n(v) for k, v in f.items()},
            'creditos_sobre_cupo': sobre_cupo[usuario_id]['creditos'] if usuario_id in sobre_cupo else 0,
            'cosecha': {**c, 'en_mora_pct': round(c['en_mora'] / c['creditos'] * 100) if c['creditos'] else None},
        })
    lista.sort(key=lambda t: -t['abonos'])
    sin_dato = cosecha.get(None)
    return lista, ({'creditos': sin_dato['creditos'], 'en_mora': sin_dato['en_mora']} if sin_dato else None)


def _supervision(tienda, desde, hasta, sin_abonar):
    inicio, _ = _ventana(tienda, desde)
    _, fin = _ventana(tienda, hasta)
    alertas = AlertaOperativa.objects.filter(tienda_id_ref=tienda.id)
    morosos = [f for f in sin_abonar if f['tramo'] >= 2]
    visitados = set(Recaudo.objects.filter(
        tienda=tienda, venta_id__in=[f['venta_id'] for f in morosos], es_renovacion=False,
        fecha_recaudo__gte=desde, fecha_recaudo__lte=hasta).values_list('venta_id', flat=True))
    sin_visita = sorted((f for f in morosos if f['venta_id'] not in visitados), key=lambda f: -(f['saldo'] or 0))
    return {
        'alertas_sin_revisar': alertas.filter(estado='nueva').count(),
        'alertas_nuevas_semana': alertas.filter(creada__gte=inicio, creada__lt=fin).count(),
        'alertas_atendidas_semana': alertas.filter(actualizada__gte=inicio, actualizada__lt=fin)
                                           .exclude(estado='nueva').count(),
        'morosos': len(morosos),
        'morosos_sin_visita_en_la_semana': len(sin_visita),
        'morosos_sin_visita': [{k: f[k] for k in ('cliente', 'venta_id', 'dias_sin_abonar', 'saldo')}
                               for f in sin_visita[:5]],
    }


def semana_ruta(tienda, hasta, admin_id):
    desde = hasta - timedelta(days=6)
    actual, sin_abonar, poco, vencidos = _riesgo(tienda, hasta)
    anterior, *_ = _riesgo(tienda, hasta - timedelta(days=7))
    creditos, sobre_cupo = _creditos(tienda, desde, hasta)
    en_mora = ({f['venta_id'] for f in sin_abonar if f['tramo'] >= 3} | {f['venta_id'] for f in poco}
               | {f['venta_id'] for f in vencidos}
               | set(DeclaracionPerdidaVenta.objects.filter(tienda_id_ref=tienda.id).values_list('venta_id_ref', flat=True)))
    trabajadores, cosecha_sin_dato = _trabajadores_semana(tienda, desde, hasta, sobre_cupo, en_mora, admin_id)
    gastos, _ = _gastos(tienda, hasta, True)
    return {
        'id': tienda.id, 'nombre': tienda.nombre, 'desde': desde.isoformat(), 'hasta': hasta.isoformat(),
        'trabajadores_asignados': _trabajadores(tienda),
        'registra_administrador': _registra_administrador(tienda, hasta, admin_id),
        'riesgo': {'actual': actual, 'hace_7_dias': anterior},
        'posibles_perdidas': _posibles_perdidas(sin_abonar, vencidos),
        'cobranza': {'esta_semana': _cobranza(tienda, hasta), 'semana_anterior': _cobranza(tienda, desde - timedelta(days=1))},
        'creditos': creditos,
        'gastos': {'semana': gastos['ultimos_7_dias'], 'normal_semanal': gastos['mediana_semanal']},
        'trabajadores': trabajadores, 'cosecha_sin_dato_de_quien_presto': cosecha_sin_dato,
        'supervision': _supervision(tienda, desde, hasta, sin_abonar),
    }


@endpoint_agente
def semana(request):
    """Salud semanal por ruta: plata en riesgo, posibles pérdidas, cobranza, trabajadores y supervisión."""
    from Tiendas.alertas_operativas import usuario_alertas_configurado
    admin = usuario_alertas_configurado()
    admin_id = admin.id if admin else None
    rutas = []
    for tienda in _filtrar_rutas(request):
        fecha, error = _fecha_ruta(tienda, request.query_params.get('fecha') or 'ayer')
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
        rutas.append(semana_ruta(tienda, fecha, admin_id))
    rutas.sort(key=lambda r: (-(r['riesgo']['actual']['en_riesgo']['pct_cartera'] or 0), r['nombre']))
    return Response({'interpretacion': INTERPRETACION, 'rutas': rutas})
