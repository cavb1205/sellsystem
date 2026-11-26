"""Informe semanal de rentabilidad por ruta para el asistente del administrador (Jarvis).

``GET /tiendas/agente/rentabilidad/?fecha=ayer[&ruta=]``: la semana son los 7 días que
terminan en ``fecha`` (un lunes con ``ayer`` es lunes a domingo). Misma autenticación,
alcance y garantía de solo lectura que ``Tiendas/agente_api.py``.

Reglas de la app (Reportes → Utilidad, ``Tiendas/views.reporte_utilidad_diario``):
- Ingreso = interés cobrado: cada abono real (sin renovaciones) se imputa primero al capital y
  después al interés (``_desglosar_abono_capital_interes``), en orden y con el acumulado previo
  de su crédito. El interés no cobrado que pasa a un crédito renovado se recupera como capital.
- Pérdidas: ``capital_no_recuperado`` de las declaraciones (``perdidaCapital``).
- Los retiros (Utilidades) y los aportes mueven la caja; no son gasto ni ingreso.
- Capital en cartera = capital expuesto: max(valor_venta − abonos reales a la fecha, 0).

Criterios del informe (no son reglas de la app):
- Gasto extraordinario: uno solo que vale ``EXTRAORDINARIO_PCT`` o más del interés semanal
  promedio y no es habitual en su tipo (compra de un carro, multas). Sueldo y arriendo nunca lo
  son. Un gasto que dice "cuadre" o "ajuste" de caja va aparte: no es un gasto real.
- Utilidad operativa = interés cobrado − gastos de operación. Utilidad neta = operativa −
  extraordinarios − capital perdido. Rentabilidad mensual = utilidad operativa de 4 semanas /
  capital invertido (capital en cartera + caja positiva). Umbrales: constantes de abajo.
"""
import unicodedata
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.db.models import Q, Sum
from rest_framework import status
from rest_framework.response import Response

from Aportes.models import Aporte
from Gastos.models import Gasto
from Recaudos.models import Recaudo
from Tiendas.agente_api import _fecha_ruta, _filtrar_rutas, _n, endpoint_agente
from Tiendas.agente_informe import _activos_al, _con_saldo_al, _hallazgo, _nombre, _registra_administrador, _ventana, PESO
from Tiendas.agente_semana import _posibles_perdidas, _riesgo
from Tiendas.models import MovimientoCaja
from Tiendas.views import _desglosar_abono_capital_interes
from Utilidades.models import Utilidad
from Ventas.models import DeclaracionPerdidaVenta, Venta

SEMANAS_SERIE = 8
EXTRAORDINARIO_PCT = Decimal('0.5')      # del interés semanal promedio
RENTABILIDAD_MINIMA_MENSUAL = 5           # % sobre el capital invertido
GASTOS_ALTOS_PCT = 40                     # gastos de operación / interés cobrado
CAJA_OCIOSA_PCT = 20                      # caja / capital invertido
CAE_PCT = 80                              # utilidad 4 semanas / 4 semanas anteriores
CARTERA_SE_ACHICA_PCT = 90                # capital hoy / hace 4 semanas
CARTERA_CRECE_PCT = 25                    # crecimiento que explica una rentabilidad baja
CERO = Decimal('0')

INTERPRETACION = (
    'Rentabilidad por ruta. interes_cobrado sigue la regla de la app (cada abono paga primero el capital '
    'y después el interés), igual que Reportes → Utilidad. utilidad_operativa = interés − gastos de operación; utilidad_neta = '
    'operativa − gastos extraordinarios − capital perdido declarado. rentabilidad_mensual_pct = utilidad '
    'operativa de las últimas 4 semanas / capital invertido (capital pendiente en cartera + caja). '
    'flujo_caja = cobrado − prestado − gastos − retiros + aportes; los retiros no restan utilidad. '
    'Extraordinarios, umbrales y veredicto son criterio del informe, no de la app. En un crédito renovado '
    'el valor_venta incluye el interés capitalizado del anterior y cuenta como capital (regla de la app): '
    'baja un poco la rentabilidad de las rutas que renuevan mucho. Los importes de cada ruta están en su '
    'propia moneda y escala: nunca se suman ni se comparan entre rutas.')


def _pct(a, b):
    return round(float(Decimal(a) / Decimal(b) * 100), 1) if b else None


def _normal(texto):
    texto = unicodedata.normalize('NFKD', (texto or '').lower())
    return ''.join(c for c in texto if not unicodedata.combining(c))


TIPOS_FIJOS = ('sueldo', 'arriendo', 'alquiler')
AJUSTE_CAJA = ('cuadre', 'ajuste de caja', 'ajuste caja', 'descuadre')


def _redondear(valor):
    return Decimal(valor or 0).quantize(Decimal('1'))


def _capital_expuesto(tienda, fecha):
    """Capital pendiente de los créditos activos a la fecha (regla de la app: capital primero)."""
    ventas = _activos_al(tienda, fecha).annotate(_abonos_reales=Sum(
        'recaudo__valor_recaudo', filter=Q(recaudo__valor_recaudo__gt=0, recaudo__es_renovacion=False,
                                           recaudo__fecha_recaudo__lte=fecha)))
    return sum((max(v.valor_venta - Decimal(v._abonos_reales or 0), CERO) for v in _con_saldo_al(ventas)), CERO)


def _interes_cobrado(tienda, desde, hasta):
    """Capital recuperado e interés cobrado por día, como reporte_utilidad_diario."""
    filas = list(Recaudo.objects.filter(
        venta__tienda_id=tienda.id, fecha_recaudo__gte=desde, fecha_recaudo__lte=hasta,
        valor_recaudo__gt=0, es_renovacion=False,
    ).values('venta_id', 'fecha_recaudo', 'valor_recaudo', 'venta__valor_venta', 'venta__interes')
        .order_by('fecha_recaudo', 'id'))
    acumulado = {f['venta_id']: f['total'] or CERO for f in Recaudo.objects.filter(
        venta_id__in={f['venta_id'] for f in filas}, fecha_recaudo__lt=desde, valor_recaudo__gt=0,
        es_renovacion=False).values('venta_id').annotate(total=Sum('valor_recaudo'))}
    for f in filas:
        previo = acumulado.get(f['venta_id'], CERO)
        capital = f['venta__valor_venta']
        capital_r, interes = _desglosar_abono_capital_interes(
            capital, Decimal(f['venta__interes']) / Decimal('100') * capital, previo, f['valor_recaudo'])
        acumulado[f['venta_id']] = previo + f['valor_recaudo']
        yield f['fecha_recaudo'], f['valor_recaudo'], _redondear(capital_r), _redondear(interes)


def _caja_al(tienda, fecha):
    """Caja al cierre de ``fecha`` según la bitácora (None si aún no había movimientos)."""
    _, fin = _ventana(tienda, fecha)
    ultimo = MovimientoCaja.objects.filter(tienda=tienda, creado_en__lt=fin).order_by('-creado_en', '-id') \
        .values_list('saldo_posterior', flat=True).first()
    return ultimo


def _semana_de(fecha, hasta):
    """Índice de semana (0 = la que termina en ``hasta``) o None si queda fuera de la serie."""
    k = (hasta - fecha).days // 7
    return k if 0 <= k < SEMANAS_SERIE else None


def _clasificar_gastos(tienda, hasta, interes_semanal_promedio):
    """Gastos de la serie: 'operacion', 'extraordinario' o 'ajuste_caja'."""
    desde = hasta - timedelta(weeks=SEMANAS_SERIE) + timedelta(days=1)
    historia = defaultdict(list)
    for gasto_id, tipo, valor in Gasto.objects.filter(
            tienda=tienda, fecha__gt=hasta - timedelta(days=180), fecha__lte=hasta).values_list('id', 'tipo_gasto_id', 'valor'):
        historia[tipo].append((gasto_id, valor))
    umbral = interes_semanal_promedio * EXTRAORDINARIO_PCT
    filas = []
    for g in Gasto.objects.filter(tienda=tienda, fecha__gte=desde, fecha__lte=hasta) \
            .select_related('tipo_gasto', 'trabajador__trabajador'):
        valores = [v for i, v in historia[g.tipo_gasto_id] if i != g.id]  # sin el propio gasto
        habitual = len(valores) >= 3 and g.valor <= 3 * median(valores)
        tipo, comentario = _normal(g.tipo_gasto.tipo_gasto), _normal(g.comentario)
        if any(a in comentario or a in tipo for a in AJUSTE_CAJA):
            clase = 'ajuste_caja'
        elif (umbral and g.valor >= umbral and not habitual
              and not any(t in tipo for t in TIPOS_FIJOS)):
            clase = 'extraordinario'
        else:
            clase = 'operacion'
        filas.append({'gasto': g, 'semana': _semana_de(g.fecha, hasta), 'clase': clase})
    return filas


def _vacio():
    return {'cobrado': CERO, 'interes_cobrado': CERO, 'prestado_nuevo': CERO, 'creditos_nuevos': 0,
            'renovaciones': 0, 'gastos_operacion': CERO, 'gastos_extraordinarios': CERO, 'ajustes_caja_como_gasto': CERO,
            'perdidas_declaradas': CERO, 'retiros': CERO, 'aportes': CERO}


def _cerrar(s):
    s = dict(s)
    s['capital_recuperado'] = s['cobrado'] - s['interes_cobrado']
    s['utilidad_operativa'] = s['interes_cobrado'] - s['gastos_operacion']
    s['utilidad_neta'] = s['utilidad_operativa'] - s['gastos_extraordinarios'] - s['perdidas_declaradas']
    s['flujo_caja'] = (s['cobrado'] - s['prestado_nuevo'] - s['gastos_operacion'] - s['gastos_extraordinarios']
                       - s['ajustes_caja_como_gasto'] - s['retiros'] + s['aportes'])
    s['retiros_pct_de_la_utilidad'] = _pct(s['retiros'], s['utilidad_operativa']) if s['utilidad_operativa'] > 0 else None
    s['margen_pct'] = _pct(s['utilidad_operativa'], s['interes_cobrado'])
    return {k: (_n(v) if isinstance(v, Decimal) else v) for k, v in s.items()}


def _sumar(semanas):
    total = _vacio()
    for s in semanas:
        for k, v in s.items():
            total[k] += v
    return total


def rentabilidad_ruta(tienda, hasta, admin_id):
    desde = hasta - timedelta(weeks=SEMANAS_SERIE) + timedelta(days=1)
    semanas = [_vacio() for _ in range(SEMANAS_SERIE)]

    for fecha, valor, _capital_r, interes in _interes_cobrado(tienda, desde, hasta):
        s = semanas[_semana_de(fecha, hasta)]
        s['cobrado'] += valor
        s['interes_cobrado'] += interes
    for valor, renovacion, fecha in Venta.objects.filter(
        tienda=tienda, fecha_venta__gte=desde, fecha_venta__lte=hasta,
    ).values_list('valor_venta', 'origen_renovacion_id', 'fecha_venta'):
        s = semanas[_semana_de(fecha, hasta)]
        if renovacion:
            s['renovaciones'] += 1  # refinancia el saldo: no sale plata de la caja
        else:
            s['prestado_nuevo'] += valor
            s['creditos_nuevos'] += 1
    for modelo, campo in ((Utilidad, 'retiros'), (Aporte, 'aportes')):
        for valor, fecha in modelo.objects.filter(tienda=tienda, fecha__gte=desde, fecha__lte=hasta) \
                .values_list('valor', 'fecha'):
            semanas[_semana_de(fecha, hasta)][campo] += valor
    for valor, fecha in DeclaracionPerdidaVenta.objects.filter(
        tienda_id_ref=tienda.id, fecha_declaracion__gte=desde, fecha_declaracion__lte=hasta,
    ).values_list('capital_no_recuperado', 'fecha_declaracion'):
        semanas[_semana_de(fecha, hasta)]['perdidas_declaradas'] += valor or CERO

    con_cobro = [s['interes_cobrado'] for s in semanas if s['cobrado']]
    interes_promedio = sum(con_cobro, CERO) / len(con_cobro) if con_cobro else CERO
    gastos = _clasificar_gastos(tienda, hasta, interes_promedio)
    campo = {'operacion': 'gastos_operacion', 'extraordinario': 'gastos_extraordinarios',
             'ajuste_caja': 'ajustes_caja_como_gasto'}
    extraordinarios, ajustes = [], []
    por_tipo = defaultdict(lambda: CERO)
    for f in gastos:
        g = f['gasto']
        semanas[f['semana']][campo[f['clase']]] += g.valor
        if f['semana'] >= 4:
            continue
        fila = {'fecha': g.fecha.isoformat(), 'tipo': g.tipo_gasto.tipo_gasto, 'valor': _n(g.valor),
                'comentario': (g.comentario or '')[:60],
                'registrado_por': _nombre(g.trabajador.trabajador if g.trabajador_id else None)}
        if f['clase'] == 'extraordinario':
            extraordinarios.append(fila)
        elif f['clase'] == 'ajuste_caja':
            ajustes.append(fila)
        else:
            por_tipo[g.tipo_gasto.tipo_gasto] += g.valor

    semana, cuatro, anteriores = semanas[0], _sumar(semanas[:4]), _sumar(semanas[4:])
    capital = _capital_expuesto(tienda, hasta)
    capital_antes = _capital_expuesto(tienda, hasta - timedelta(weeks=4))
    caja = _caja_al(tienda, hasta)
    caja = Decimal(caja) if caja is not None else Decimal(tienda.caja_inicial or 0)
    caja_antes = _caja_al(tienda, hasta - timedelta(days=7))
    invertido = capital + max(caja, CERO)

    riesgo, sin_abonar, poco, vencidos = _riesgo(tienda, hasta)
    perdidas = _posibles_perdidas(sin_abonar, vencidos)
    ids = [f['venta_id'] for f in sin_abonar + poco + vencidos]
    expuesto = {v.id: max(v.valor_venta - Decimal(v._abonos_reales or 0), CERO) for v in Venta.objects.filter(id__in=ids)
                .annotate(_abonos_reales=Sum('recaudo__valor_recaudo', filter=Q(
                    recaudo__valor_recaudo__gt=0, recaudo__es_renovacion=False, recaudo__fecha_recaudo__lte=hasta)))}
    capital_de = lambda filas: sum((expuesto.get(f['venta_id'], CERO) for f in filas), CERO)
    en_riesgo = {f['venta_id']: f for f in [f for f in sin_abonar if f['dias_sin_abonar'] >= 30] + poco + vencidos}
    capital_riesgo = capital_de(en_riesgo.values())
    capital_perdida = capital_de([{'venta_id': i} for i in perdidas['_ids']])
    utilidad_semanal = cuatro['interes_cobrado'] / 4 - cuatro['gastos_operacion'] / 4
    rent_mensual = _pct(cuatro['interes_cobrado'] - cuatro['gastos_operacion'], invertido)
    rent_neta = _pct(_cerrar(cuatro)['utilidad_neta'], invertido)

    resumen = {
        'semana': _cerrar(semana), 'ultimas_4_semanas': _cerrar(cuatro), 'cuatro_semanas_anteriores': _cerrar(anteriores),
        'serie': [{'hasta': (hasta - timedelta(weeks=k)).isoformat(),
                   **{c: v for c, v in _cerrar(s).items() if c in ('interes_cobrado', 'gastos_operacion',
                                                                   'gastos_extraordinarios', 'utilidad_operativa')}}
                  for k, s in enumerate(semanas)],
        'capital': {'en_cartera': _n(round(capital)), 'caja': _n(caja), 'invertido': _n(round(invertido)),
                    'en_cartera_hace_4_semanas': _n(round(capital_antes)),
                    'cambio_cartera_pct': _pct(capital - capital_antes, capital_antes)},
        'rentabilidad': {'mensual_pct': rent_mensual, 'neta_mensual_pct': rent_neta,
                         'minima_esperada_pct': RENTABILIDAD_MINIMA_MENSUAL},
        'riesgo': {'capital_en_riesgo': _n(round(capital_riesgo)),
                   'capital_en_posible_perdida': _n(round(capital_perdida)),
                   'creditos_en_posible_perdida': perdidas['creditos'],
                   'semanas_de_utilidad_en_posible_perdida':
                       round(float(capital_perdida / utilidad_semanal), 1)
                       if utilidad_semanal > 0 else None},
        'gastos_extraordinarios': extraordinarios, 'ajustes_caja_como_gasto': ajustes,
        'gastos_operacion_por_tipo': sorted(({'tipo': k, 'valor': _n(v)} for k, v in por_tipo.items()),
                                            key=lambda x: -x['valor']),
    }
    resumen['senales'] = _senales(semana, cuatro, anteriores, resumen, caja, caja_antes, invertido, utilidad_semanal)
    resumen['senales'].sort(key=lambda h: -PESO[h['severidad']])
    severidades = {h['severidad'] for h in resumen['senales']}
    crece = (resumen['capital']['cambio_cartera_pct'] or 0) >= CARTERA_CRECE_PCT
    veredicto = ('en_perdida' if cuatro['interes_cobrado'] - cuatro['gastos_operacion'] < 0
                 else 'creciendo' if crece and rent_mensual is not None and rent_mensual < RENTABILIDAD_MINIMA_MENSUAL
                 else 'baja' if rent_mensual is not None and rent_mensual < RENTABILIDAD_MINIMA_MENSUAL
                 else 'rentable')
    return {'id': tienda.id, 'nombre': tienda.nombre, 'desde': (hasta - timedelta(days=6)).isoformat(),
            'hasta': hasta.isoformat(), 'registra_administrador': _registra_administrador(tienda, hasta, admin_id),
            'veredicto': veredicto, 'alerta': 'rojo' if severidades & {'alta', 'critica'} else
            'amarillo' if 'media' in severidades else 'verde', **resumen}


def _senales(semana, cuatro, anteriores, r, caja, caja_antes, invertido, utilidad_semanal):
    h = []
    op4 = cuatro['interes_cobrado'] - cuatro['gastos_operacion']
    op_ant = anteriores['interes_cobrado'] - anteriores['gastos_operacion']
    if op4 < 0:
        h.append(_hallazgo('RUTA_EN_PERDIDA', 'critica', 'En las últimas 4 semanas los gastos superaron el interés cobrado',
                           utilidad_operativa=_n(op4)))
    elif semana['interes_cobrado'] - semana['gastos_operacion'] < 0:
        h.append(_hallazgo('SEMANA_EN_PERDIDA', 'media', 'La semana cerró con gastos de operación mayores al interés cobrado',
                           utilidad_operativa=_n(semana['interes_cobrado'] - semana['gastos_operacion'])))
    rent = r['rentabilidad']['mensual_pct']
    cambio = r['capital']['cambio_cartera_pct']
    creciendo = cambio is not None and cambio >= CARTERA_CRECE_PCT
    if creciendo:
        # Con la regla de la app (capital primero) el interés de los créditos nuevos llega al final:
        # una ruta que crece rinde poco en el papel mientras recupera ese capital.
        h.append(_hallazgo('CARTERA_CRECE', 'info', 'La cartera está creciendo: el interés de lo prestado llegará después',
                           cambio_pct=cambio))
    if rent is not None and 0 <= rent < RENTABILIDAD_MINIMA_MENSUAL:
        h.append(_hallazgo('RENTABILIDAD_BAJA', 'info' if creciendo else 'media', 'La ruta rinde poco para el capital que tiene',
                           mensual_pct=rent, minima_pct=RENTABILIDAD_MINIMA_MENSUAL, explicada_por_crecimiento=creciendo))
    if cuatro['retiros'] > 0 and cuatro['retiros'] > max(op4, CERO):
        achica = cambio is not None and cambio < CARTERA_SE_ACHICA_PCT - 100
        h.append(_hallazgo('RETIROS_SUPERAN_UTILIDAD', 'alta' if achica else 'media',
                           'Se retiró más plata de la que la ruta ganó: se está sacando capital',
                           retiros=_n(cuatro['retiros']), utilidad_operativa=_n(op4)))
    if op_ant > 0 and op4 >= 0 and op4 < op_ant * CAE_PCT / 100:
        h.append(_hallazgo('UTILIDAD_CAE', 'media', 'La utilidad de las últimas 4 semanas cayó frente a las 4 anteriores',
                           ultimas_4=_n(op4), anteriores_4=_n(op_ant), cambio_pct=_pct(op4 - op_ant, op_ant)))
    if cuatro['interes_cobrado'] and cuatro['gastos_operacion'] > cuatro['interes_cobrado'] * GASTOS_ALTOS_PCT / 100:
        h.append(_hallazgo('GASTOS_ALTOS', 'media', 'Los gastos de operación se comen buena parte del interés',
                           gastos_pct_del_interes=_pct(cuatro['gastos_operacion'], cuatro['interes_cobrado'])))
    if r['gastos_extraordinarios']:
        h.append(_hallazgo('GASTOS_EXTRAORDINARIOS', 'media', 'Gastos fuera de lo habitual (inversiones, ajustes o multas)',
                           cantidad=len(r['gastos_extraordinarios']),
                           total=_n(sum(Decimal(str(g['valor'])) for g in r['gastos_extraordinarios']))))
    if not cuatro['gastos_operacion'] and not cuatro['gastos_extraordinarios'] and cuatro['cobrado']:
        h.append(_hallazgo('SIN_GASTOS', 'info', 'La ruta no registró gastos en 4 semanas: sus costos quizá se cargan en otra ruta'))
    if r['ajustes_caja_como_gasto']:
        h.append(_hallazgo('AJUSTE_CAJA_COMO_GASTO', 'media',
                           'Se registró como gasto un ajuste para cuadrar la caja: no es un gasto real',
                           total=_n(sum(Decimal(str(g['valor'])) for g in r['ajustes_caja_como_gasto']))))
    # Caja negativa: solo cuando pasa a negativa o empeora; si viene así de antes, sería ruido semanal.
    if caja < 0 and (caja_antes is None or caja_antes >= 0):
        h.append(_hallazgo('CAJA_NEGATIVA', 'alta', 'La caja del sistema pasó a negativo: se prestó o gastó plata que no figura',
                           caja=_n(caja)))
    elif caja < 0 and caja < caja_antes:
        h.append(_hallazgo('CAJA_NEGATIVA_EMPEORA', 'media', 'La caja del sistema sigue en negativo y empeoró en la semana',
                           caja=_n(caja), semana_anterior=_n(caja_antes)))
    elif caja < 0:
        h.append(_hallazgo('CAJA_NEGATIVA_CRONICA', 'info', 'La caja del sistema está en negativo desde antes',
                           caja=_n(caja)))
    elif invertido and caja > invertido * CAJA_OCIOSA_PCT / 100 and caja > 2 * cuatro['prestado_nuevo'] / 4:
        h.append(_hallazgo('CAJA_OCIOSA', 'media', 'Hay mucha plata quieta en caja', caja=_n(caja),
                           pct_del_invertido=_pct(caja, invertido)))
    if cambio is not None and cambio < CARTERA_SE_ACHICA_PCT - 100:
        h.append(_hallazgo('CARTERA_SE_ACHICA', 'media', 'La cartera se está achicando: se presta menos de lo que se recupera',
                           cambio_pct=cambio))
    semanas = r['riesgo']['semanas_de_utilidad_en_posible_perdida']
    if semanas is not None and semanas >= 2:
        h.append(_hallazgo('RIESGO_SUPERA_UTILIDAD', 'alta' if semanas >= 4 else 'media',
                           'El capital en posible pérdida equivale a varias semanas de utilidad',
                           semanas=semanas, capital=r['riesgo']['capital_en_posible_perdida']))
    return h


@endpoint_agente
def rentabilidad(request):
    """Rentabilidad semanal por ruta: interés cobrado, gastos, utilidad, capital, riesgo y señales."""
    from Tiendas.alertas_operativas import usuario_alertas_configurado
    admin = usuario_alertas_configurado()
    admin_id = admin.id if admin else None
    rutas = []
    for tienda in _filtrar_rutas(request):
        fecha, error = _fecha_ruta(tienda, request.query_params.get('fecha') or 'ayer')
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
        rutas.append(rentabilidad_ruta(tienda, fecha, admin_id))
    orden = {'en_perdida': 0, 'baja': 1, 'creciendo': 2, 'rentable': 3}
    rutas.sort(key=lambda r: (orden[r['veredicto']], r['rentabilidad']['mensual_pct'] or 0, r['nombre']))
    return Response({'interpretacion': INTERPRETACION, 'rutas': rutas})
