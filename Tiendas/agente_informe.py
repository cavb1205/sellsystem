"""Informe diario de anomalías para el asistente del administrador (Jarvis).

``GET /tiendas/agente/informe/?fecha=ayer[&ruta=]``, con la misma autenticación,
alcance y garantía de solo lectura que ``Tiendas/agente_api.py``.

Principios:
- Cada ruta se compara consigo misma: la línea base son sus últimas
  ``SEMANAS_BASE`` semanas, el mismo día de la semana. Así no hay falsas
  alarmas los domingos y no importa la escala o la moneda de cada ruta.
- Los hallazgos son señales para revisar, no conclusiones: el sistema solo
  sabe lo que se registró. La redacción final la hace el asistente.
- El cierre de caja no se evalúa: ``Cierre_Caja.valor`` es el saldo del
  sistema, no efectivo contado, y las correcciones posteriores ya quedan en
  ``MovimientoCaja``.
- Datos mínimos de clientes: nombre, montos y estados.
- Consultas acotadas: Recaudo se filtra por ``tienda`` + ``fecha_recaudo``
  (índice compuesto) y las líneas base se agregan en SQL por fecha.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from statistics import median

from django.db.models import Count, Exists, Max, OuterRef, Q, Sum
from rest_framework import status
from rest_framework.response import Response

from Gastos.models import Gasto
from Recaudos.models import Recaudo
from Tiendas.agente_api import _fecha_ruta, _filtrar_rutas, _n, _trabajadores, endpoint_agente
from Tiendas.fecha_operativa import zona_horaria_tienda
from Tiendas.models import AlertaOperativa, CambioOrdenVisita, MovimientoCaja
from Ventas.models import AjusteVentaAdministrativo, DeclaracionPerdidaVenta, Venta
from Ventas.calendario import calendario_venta
from Ventas.riesgo import (DIAS_CANDIDATO_CASTIGO, calcular_cuotas_atrasadas, dias_completos_sin_abono,
                           importe_atrasado_registrado, normalizar_plazo, umbrales_dsa)

SEMANAS_BASE = 8
DIAS_HISTORIA = 90
LIMITE = 10
PESO = {'info': 0, 'media': 1, 'alta': 3, 'critica': 6}
ESTADOS_ACTIVOS = ('Vigente', 'Atrasado', 'Vencido')
TIPOS_CARTERA = ('RIESGO_CARTERA', 'SEGUIMIENTO_PREVENTIVO', 'SIN_PRIMER_ABONO')
ALERTA_ABIERTA = ('nueva', 'revisada')
# Criterios propios del informe (no son reglas de la app). El 90 sí es de la app:
# Ventas/riesgo.DIAS_CANDIDATO_CASTIGO.
HITOS_INFORME = (30, 60)
ATRASO_CUOTAS_INFORME = (7, 14)   # cuotas atrasadas (diario) de quien abona algo pero poco
DIAS_TARDE = 2                    # abono registrado 2+ días después de su fecha
HITOS_VENCIDO = (1, 30, 60, 90)   # días pasados del plazo final con saldo

INTERPRETACION = (
    'Señales para revisar, no conclusiones. Cada ruta se compara con sus propias últimas 8 semanas '
    '(mismo día de la semana). severidad: info < media < alta < critica; "info" es contexto. '
    'registra_administrador=true: la ruta la registra el propio administrador, así que las '
    'correcciones hechas por él son suyas. creditos_sobre_cupo es informativo: no cambia el semáforo. '
    'dias_seguidos cuenta días trabajados consecutivos con la misma señal. Los importes de cada ruta están en su propia moneda y escala: '
    'nunca se suman ni se comparan entre rutas.')


def _hallazgo(codigo, severidad, titulo, **datos):
    return {'codigo': codigo, 'severidad': severidad, 'titulo': titulo, 'datos': datos}


def _ventana(tienda, fecha):
    """Inicio y fin de la jornada ``fecha`` en la zona horaria de la ruta."""
    inicio = datetime.combine(fecha, time.min, tzinfo=zona_horaria_tienda(tienda))
    return inicio, inicio + timedelta(days=1)


def _nombre(usuario):
    if not usuario:
        return 'sin usuario'
    return (usuario.get_full_name() or usuario.username).strip()


def _cliente(venta):
    return f'{venta.cliente.nombres} {venta.cliente.apellidos}'.strip()


def _percentil(valores, p):
    orden = sorted(valores)
    if not orden:
        return Decimal('0')
    return orden[min(len(orden) - 1, int(round(p * (len(orden) - 1))))]


# 1. Jornada contra su línea base ------------------------------------------------

def _jornada(tienda, fecha):
    from Tiendas.reporte_rutas import cumplimiento_dia
    c = cumplimiento_dia(tienda, fecha)
    # 6 días extra para evaluar los días anteriores con su propia línea base (persistencia).
    desde = fecha - timedelta(weeks=SEMANAS_BASE, days=6)
    por_dia = {
        fila['fecha_recaudo']: fila for fila in Recaudo.objects.filter(
            tienda=tienda, fecha_recaudo__gte=desde, fecha_recaudo__lte=fecha, es_renovacion=False,
        ).values('fecha_recaudo').annotate(
            abonos=Count('id', filter=Q(valor_recaudo__gt=0)),
            fallas=Count('id', filter=Q(valor_recaudo__lte=0)),
            total=Sum('valor_recaudo'),
            sin_gps=Count('id', filter=Q(valor_recaudo__gt=0, latitud__isnull=True)),
        )}
    vacio = {'abonos': 0, 'fallas': 0, 'total': Decimal('0'), 'sin_gps': 0}
    tasa = lambda d: d['fallas'] / (d['abonos'] + d['fallas'])

    def evaluar(dia):
        """Indicadores del día contra sus mismas 8 semanas previas."""
        hoy = por_dia.get(dia, vacio)
        base = [por_dia.get(dia - timedelta(weeks=k), vacio) for k in range(1, SEMANAS_BASE + 1)]
        activos = [d for d in base if d['abonos'] + d['fallas'] > 0]
        gestiones = hoy['abonos'] + hoy['fallas']
        tasa_base = sum(tasa(d) for d in activos) / len(activos) if activos else None
        recaudo_base = median([d['total'] or Decimal('0') for d in activos]) if activos else None
        tasa_hoy = tasa(hoy) if gestiones else None
        alza = (tasa_hoy - tasa_base) if tasa_hoy is not None and tasa_base is not None else None
        return {
            'hoy': hoy, 'activos': activos, 'gestiones': gestiones, 'tasa_hoy': tasa_hoy, 'tasa_base': tasa_base,
            'recaudo_base': recaudo_base, 'se_trabaja': len(activos) >= 5,
            'fallas_altas': alza is not None and gestiones >= 8 and alza >= 0.20 and tasa_hoy >= 1.5 * tasa_base,
            'recaudo_bajo': bool(recaudo_base) and len(activos) >= 5 and gestiones > 0
                            and (hoy['total'] or 0) < recaudo_base * Decimal('0.6'),
            'sin_actividad': gestiones == 0 and len(activos) >= 5,
        }

    def seguidos(clave):
        """Días trabajados consecutivos (incluido ``fecha``) con la misma señal; salta los días libres."""
        n = 1
        for k in range(1, 7):
            e = evaluar(fecha - timedelta(days=k))
            if not e['se_trabaja']:
                continue
            if not (e[clave] or (clave == 'recaudo_bajo' and e['sin_actividad'])):
                break
            n += 1
        return n

    e = evaluar(fecha)
    hoy, activos, gestiones = e['hoy'], e['activos'], e['gestiones']
    tasa_base, recaudo_base, tasa_hoy = e['tasa_base'], e['recaudo_base'], e['tasa_hoy']
    abonos_periodo = sum(d['abonos'] for f, d in por_dia.items() if fecha - timedelta(weeks=SEMANAS_BASE) <= f < fecha)
    sin_gps_periodo = sum(d['sin_gps'] for f, d in por_dia.items() if fecha - timedelta(weeks=SEMANAS_BASE) <= f < fecha)
    gps_base = sin_gps_periodo / abonos_periodo if abonos_periodo else None
    ventas_dia = Venta.objects.filter(tienda=tienda, fecha_venta=fecha).count()

    hallazgos = []
    if e['sin_actividad'] and ventas_dia == 0:
        n = seguidos('recaudo_bajo')
        hallazgos.append(_hallazgo(
            'SIN_ACTIVIDAD', 'alta', 'Sin abonos, visitas ni ventas registradas en un día que normalmente se trabaja',
            dias_trabajados_mismo_dia_semana=f'{len(activos)} de {SEMANAS_BASE}', dias_seguidos=n))
    if e['fallas_altas']:
        alza = tasa_hoy - tasa_base
        n = seguidos('fallas_altas')
        hallazgos.append(_hallazgo(
            'FALLAS_ALTAS', 'alta' if alza >= 0.35 or n >= 2 else 'media',
            'Visitas sin pago muy por encima de lo normal',
            tasa_fallas=round(tasa_hoy * 100), tasa_normal=round(tasa_base * 100),
            fallas=hoy['fallas'], gestiones=gestiones, dias_seguidos=n))
    if e['recaudo_bajo']:
        n = seguidos('recaudo_bajo')
        # Un día flojo es normal; varios días trabajados seguidos ya no.
        hallazgos.append(_hallazgo(
            'RECAUDO_BAJO', 'alta' if n >= 2 else 'media',
            'Recaudo muy por debajo de lo normal para ese día de la semana',
            recaudado=_n(hoy['total']), mediana_normal=_n(recaudo_base), dias_seguidos=n,
            sin_registro=c['sin_gestion'], creditos_programados=c['programadas']))
    if hoy['abonos'] >= 5:
        pct = hoy['sin_gps'] / hoy['abonos']
        if gps_base is not None and gps_base >= 0.9:
            hallazgos.append(_hallazgo(
                'RUTA_SIN_GPS', 'info', 'La ruta registra los abonos sin ubicación de forma habitual',
                abonos_sin_ubicacion_pct=round(pct * 100)))
        elif pct >= 0.5 and (gps_base is None or gps_base < 0.2):
            hallazgos.append(_hallazgo(
                'ABONOS_SIN_GPS', 'media', 'Abonos sin ubicación, cuando normalmente sí la tienen',
                abonos_sin_ubicacion_pct=round(pct * 100),
                normal_pct=round(gps_base * 100) if gps_base is not None else None))
    esperado = c['esperado'] or Decimal('0')
    resumen = {
        'cuotas_del_dia': _n(esperado), 'recaudado': _n(c['recaudo']),
        'cobertura_pct': round(c['cobertura_recaudo'] / esperado * 100) if esperado else None,
        'creditos_programados': c['programadas'], 'con_abono': c['con_abono'],
        'no_pagaron': c['sin_abono'], 'sin_registro': c['sin_gestion'],
        'visitas_sin_pago': hoy['fallas'], 'ventas_nuevas': ventas_dia,
        'normal_mismo_dia': {
            'dias_trabajados': f'{len(activos)} de {SEMANAS_BASE}',
            'recaudo_mediana': _n(recaudo_base) if recaudo_base is not None else None,
            'tasa_visitas_sin_pago_pct': round(tasa_base * 100) if tasa_base is not None else None,
        },
    }
    return resumen, hallazgos, gestiones + ventas_dia > 0


# 2. Correcciones, reversas y ajustes en la bitácora de caja ---------------------

def _severidad_movimiento(mov, es_admin):
    if es_admin or not mov.delta:
        # Lo del administrador es suyo; un cambio sin efecto en caja (fecha o texto) es contexto.
        return 'info'
    if mov.tipo == 'RECAUDO':
        if mov.accion == 'REVERSA' or mov.delta < 0:
            return 'alta'
        return 'info'
    if mov.tipo == 'VENTA' and (mov.accion == 'REVERSA' or mov.delta < 0):
        # Eliminar un crédito o subirle el monto: lo segundo no pasa por el control de cupo.
        return 'alta'
    if mov.accion == 'AJUSTE' or mov.tipo == 'AJUSTE':
        return 'alta'
    return 'media'


def _bitacora(tienda, fecha, admin_id):
    inicio, fin = _ventana(tienda, fecha)
    movimientos = list(MovimientoCaja.objects.filter(
        tienda=tienda, creado_en__gte=inicio, creado_en__lt=fin,
    ).exclude(accion='CREACION').select_related('usuario').order_by('creado_en', 'id'))
    origenes = {(m.origen_tipo, m.origen_id) for m in movimientos if m.origen_id}
    creaciones = {}
    if origenes:
        # Solo la CREACION: si el registro es anterior al ledger o empezó como visita
        # convertida en abono, no hay original y no se informa uno equivocado.
        for c in MovimientoCaja.objects.filter(
            tienda=tienda, accion='CREACION', origen_tipo__in={o[0] for o in origenes},
            origen_id__in={o[1] for o in origenes},
        ).select_related('usuario').order_by('creado_en', 'id'):
            creaciones.setdefault((c.origen_tipo, c.origen_id), c)
    zona = zona_horaria_tienda(tienda)
    previos = MovimientoCaja.objects.filter(
        tienda=tienda, creado_en__gte=inicio - timedelta(weeks=SEMANAS_BASE), creado_en__lt=inicio,
    ).exclude(accion='CREACION')
    if admin_id:
        previos = previos.exclude(usuario_id=admin_id)
    promedio_diario = round(previos.count() / (SEMANAS_BASE * 7), 2)

    hallazgos = []
    for m in movimientos:
        es_admin = bool(admin_id and m.usuario_id == admin_id)
        original = creaciones.get((m.origen_tipo, m.origen_id))
        datos = {
            'tipo': m.tipo, 'accion': m.accion, 'efecto_en_caja': _n(m.delta), 'hecho_por': _nombre(m.usuario),
            'hecho_por_administrador': es_admin, 'hora': m.creado_en.astimezone(zona).strftime('%H:%M'),
            'detalle': (m.detalle or '')[:120],
        }
        if original:
            datos['registrado_originalmente'] = original.creado_en.astimezone(zona).date().isoformat()
            datos['registrado_por'] = _nombre(original.usuario)
            datos['dias_despues'] = (fecha - original.creado_en.astimezone(zona).date()).days
            datos['monto_original'] = _n(original.delta)
        titulo = {
            ('RECAUDO', 'REVERSA'): 'Abono eliminado', ('RECAUDO', 'CORRECCION'): 'Abono corregido',
            ('VENTA', 'REVERSA'): 'Crédito eliminado',
            ('VENTA', 'CORRECCION'): 'Monto del crédito aumentado (sin control de cupo)' if m.delta < 0 else 'Crédito corregido',
            ('GASTO', 'REVERSA'): 'Gasto eliminado', ('GASTO', 'CORRECCION'): 'Gasto corregido',
        }.get((m.tipo, m.accion), f'{m.get_tipo_display()}: {m.get_accion_display().lower()}')
        hallazgos.append(_hallazgo('CORRECCION_CAJA', _severidad_movimiento(m, es_admin), titulo, **datos))
    hallazgos.sort(key=lambda h: -PESO[h['severidad']])
    serios = [h for h in hallazgos if h['severidad'] != 'info']
    if len(serios) >= 3 and len(serios) > 2 * promedio_diario:
        hallazgos.insert(0, _hallazgo(
            'MUCHAS_CORRECCIONES', 'alta', 'Muchas correcciones o eliminaciones en un solo día',
            cantidad=len(serios), promedio_diario_normal=promedio_diario))
    return hallazgos[:LIMITE + 1], {'correcciones_del_dia': len(movimientos),
                                    'promedio_diario_trabajadores': promedio_diario}


# 3. Gastos fuera de patrón ------------------------------------------------------

def _gastos(tienda, fecha, hubo_actividad):
    filas = list(Gasto.objects.filter(
        tienda=tienda, fecha__gt=fecha - timedelta(days=DIAS_HISTORIA), fecha__lte=fecha,
    ).select_related('tipo_gasto', 'trabajador__trabajador'))
    del_dia = [g for g in filas if g.fecha == fecha]
    previos = [g for g in filas if g.fecha < fecha]
    semana = lambda fin: sum((g.valor for g in filas if fin - timedelta(days=6) <= g.fecha <= fin), Decimal('0'))
    semanas_previas = [semana(fecha - timedelta(weeks=k)) for k in range(1, SEMANAS_BASE + 1)
                       if fecha - timedelta(weeks=k) - timedelta(days=6) > fecha - timedelta(days=DIAS_HISTORIA)]
    semana_actual = semana(fecha)
    mediana_semanal = median(semanas_previas) if semanas_previas else Decimal('0')
    valores = [g.valor for g in previos]
    tipos_previos = {g.tipo_gasto_id for g in previos}
    mediana = median(valores) if valores else None
    p90 = _percentil(valores, 0.9) if valores else None

    hallazgos = []
    for g in del_dia:
        datos = {'tipo': g.tipo_gasto.tipo_gasto, 'valor': _n(g.valor), 'comentario': (g.comentario or '')[:60],
                 'registrado_por': _nombre(g.trabajador.trabajador if g.trabajador_id else None)}
        if len(valores) >= 8 and g.valor > max(2 * mediana, p90):
            alto = g.valor > 3 * mediana and g.valor > max(valores)
            hallazgos.append(_hallazgo('GASTO_ALTO', 'alta' if alto else 'media',
                                       'Gasto mucho más alto que los habituales de la ruta',
                                       mediana_normal=_n(mediana), **datos))
        elif len(previos) >= 5 and g.tipo_gasto_id not in tipos_previos:
            hallazgos.append(_hallazgo('GASTO_TIPO_NUEVO', 'media',
                                       'Tipo de gasto que la ruta no usaba en los últimos 90 días', **datos))
    if del_dia and not hubo_actividad:
        hallazgos.append(_hallazgo('GASTO_SIN_ACTIVIDAD', 'media', 'Gastos en un día sin cobros ni ventas',
                                   total=_n(sum(g.valor for g in del_dia))))
    umbral_semana = Decimal('1.5') * mediana_semanal
    # Solo el día en que la semana móvil cruza el umbral: así no se repite siete días seguidos.
    if (del_dia and len(semanas_previas) >= 4 and mediana_semanal > 0
            and semana_actual > umbral_semana >= semana(fecha - timedelta(days=1))):
        hallazgos.append(_hallazgo('GASTOS_SEMANA_ALTOS', 'media', 'Los gastos de los últimos 7 días superan lo normal',
                                   ultimos_7_dias=_n(semana_actual), mediana_semanal=_n(mediana_semanal)))
    resumen = {'del_dia': _n(sum((g.valor for g in del_dia), Decimal('0'))), 'cantidad_del_dia': len(del_dia),
               'ultimos_7_dias': _n(semana_actual), 'mediana_semanal': _n(mediana_semanal)}
    return resumen, hallazgos


# 4. Créditos nuevos ------------------------------------------------------------

def _dias_sin_abono_antes(venta, fecha):
    ultimo = Recaudo.objects.filter(
        tienda_id=venta.tienda_id, venta=venta, valor_recaudo__gt=0, es_renovacion=False, fecha_recaudo__lt=fecha,
    ).aggregate(f=Max('fecha_recaudo'))['f']
    return dias_completos_sin_abono(ultimo or venta.fecha_venta, hoy=fecha)


def _creditos_nuevos(tienda, fecha, admin_id):
    ventas = list(Venta.objects.filter(tienda=tienda, fecha_venta=fecha)
                  .select_related('cliente', 'creado_por', 'origen_renovacion').order_by('id'))
    if not ventas:
        return [], [], []
    historicos = list(Venta.objects.filter(
        tienda=tienda, fecha_venta__gte=fecha - timedelta(days=DIAS_HISTORIA), fecha_venta__lt=fecha,
    ).values_list('valor_venta', flat=True))
    mediana = median(historicos) if len(historicos) >= 8 else None
    p90 = _percentil(historicos, 0.9) if mediana else None
    con_historia = set(Venta.objects.filter(
        cliente_id__in=[v.cliente_id for v in ventas], fecha_venta__lt=fecha,
    ).values_list('cliente_id', flat=True))
    senales = defaultdict(list)
    gravedad = {}
    sobre_cupo = {}
    for a in AlertaOperativa.objects.filter(tipo='VENTA_CON_ALERTAS', venta_id_ref__in=[v.id for v in ventas]):
        score = (a.datos or {}).get('score_previo') or {}
        # Cada señal trae su texto (doble financiación, exceso de cupo, cliente bloqueado…).
        for senal in (a.datos or {}).get('senales') or [{'texto': a.titulo, 'severidad': a.severidad}]:
            if senal.get('codigo') == 'EXCESO_CUPO':
                # El cupo sale de la calificación del cliente calculada justo antes de crear el
                # crédito (Clientes/views._calcular_score): pasarlo es una alerta en sí misma.
                d = senal.get('datos') or {}
                sobre_cupo[a.venta_id_ref] = {
                    'severidad': 'critica' if senal.get('severidad') == 'critica' else 'alta',
                    'cupo_disponible': d.get('cupo_disponible', score.get('cupo_disponible')),
                    'exceso': d.get('exceso'), 'calificacion': score.get('score'), 'nivel': score.get('nivel'),
                    'cupo_bloqueado': bool((score.get('justificacion') or {}).get('bloqueado')),
                    'motivo_cupo': (score.get('justificacion') or {}).get('razon'),
                }
                continue
            senales[a.venta_id_ref].append(senal.get('texto') or a.titulo)
            # Un escalón menos que la señal original: es contexto de la venta, no un hallazgo nuevo.
            nivel = {'critica': 'alta', 'alta': 'media'}.get(senal.get('severidad'), 'info')
            if PESO[nivel] > PESO[gravedad.get(a.venta_id_ref, 'info')]:
                gravedad[a.venta_id_ref] = nivel

    lista, hallazgos, lista_sobre_cupo = [], [], []
    for v in ventas:
        cliente_nuevo = v.cliente_id not in con_historia
        datos = {'cliente': _cliente(v), 'venta_id': v.id, 'valor': _n(v.valor_venta), 'plazo': v.plazo,
                 'cuotas': v.cuotas, 'origen': v.tipo_origen or ('Renovacion' if v.origen_renovacion_id else 'Nuevo'),
                 'cliente_nuevo': cliente_nuevo, 'registrado_por': _nombre(v.creado_por)}
        lista.append(datos)
        if mediana and v.valor_venta > max(2 * mediana, p90):
            hallazgos.append(_hallazgo('CREDITO_ALTO', 'alta' if cliente_nuevo else 'media',
                                       'Crédito muy superior a los habituales de la ruta'
                                       + (' para un cliente nuevo' if cliente_nuevo else ''),
                                       mediana_normal=_n(mediana), **datos))
        if v.origen_renovacion_id:
            dias = _dias_sin_abono_antes(v.origen_renovacion, fecha)
            # Mismos tramos de la app (Ventas/riesgo.UMBRALES_DSA): 2.º tramo media, 3.º alta.
            umbral = umbrales_dsa(normalizar_plazo(v.origen_renovacion.plazo))
            severidad = 'alta' if dias >= umbral[2] else 'media' if dias >= umbral[1] else None
            if severidad:
                hallazgos.append(_hallazgo('RENOVACION_CON_MORA', severidad,
                                           'Renovación de un crédito que llevaba días sin abonos',
                                           dias_sin_abono_al_renovar=dias, credito_anterior=_n(v.origen_renovacion.valor_venta),
                                           **datos))
        if v.id in sobre_cupo:
            # Informativo (decisión del dueño): no cambia el semáforo, va en su propia lista.
            extra = dict(sobre_cupo[v.id])
            extra.pop('severidad')
            lista_sobre_cupo.append({**datos, **extra})
        if senales.get(v.id):
            hallazgos.append(_hallazgo('CREDITO_CON_SENALES', gravedad.get(v.id, 'info'),
                                       'El sistema marcó señales al crear el crédito',
                                       senales=senales[v.id][:3], **datos))
    return lista[:LIMITE * 2], hallazgos, lista_sobre_cupo


def _sobre_cupo_por_trabajador(tienda, inicio, fin):
    """Créditos sobre el cupo por trabajador en [inicio, fin] (fechas de venta)."""
    ventas = dict(Venta.objects.filter(tienda=tienda, fecha_venta__gte=inicio, fecha_venta__lte=fin)
                  .values_list('id', 'creado_por_id'))
    conteo = defaultdict(lambda: {'creditos': 0, 'exceso': Decimal('0')})
    for a in AlertaOperativa.objects.filter(tipo='VENTA_CON_ALERTAS', venta_id_ref__in=list(ventas)):
        for senal in (a.datos or {}).get('senales') or []:
            if senal.get('codigo') == 'EXCESO_CUPO':
                c = conteo[ventas[a.venta_id_ref]]
                c['creditos'] += 1
                c['exceso'] += Decimal(str((senal.get('datos') or {}).get('exceso') or 0))
                break
    return conteo


# 5. Acciones administrativas del día -------------------------------------------

def _acciones_administrativas(tienda, fecha, admin_id):
    inicio, fin = _ventana(tienda, fecha)
    hallazgos = []
    for d in DeclaracionPerdidaVenta.objects.filter(tienda_id_ref=tienda.id, fecha_declaracion=fecha).select_related('venta__cliente', 'usuario'):
        es_admin = bool(admin_id and d.usuario_id == admin_id)
        hallazgos.append(_hallazgo(
            'PERDIDA_DECLARADA', 'info' if es_admin else 'alta', 'Crédito declarado como pérdida',
            cliente=_cliente(d.venta) if d.venta else None, venta_id=d.venta_id_ref,
            capital_no_recuperado=_n(d.capital_no_recuperado), hecho_por=d.usuario_nombre,
            hecho_por_administrador=es_admin))
    for a in AjusteVentaAdministrativo.objects.filter(venta__tienda=tienda, creada__gte=inicio, creada__lt=fin).select_related('venta__cliente', 'usuario'):
        es_admin = bool(admin_id and a.usuario_id == admin_id)
        hallazgos.append(_hallazgo(
            'AJUSTE_CREDITO', 'info' if es_admin else 'media', 'Cambio de fecha o cuotas de un crédito',
            cliente=_cliente(a.venta), venta_id=a.venta_id, motivo=a.motivo[:120],
            cuotas=f'{a.cuotas_anteriores} → {a.cuotas_nuevas}', hecho_por=_nombre(a.usuario),
            hecho_por_administrador=es_admin))
    cambios = CambioOrdenVisita.objects.filter(tienda=tienda, registrado_en__gte=inicio, registrado_en__lt=fin)
    if cambios.exists():
        hallazgos.append(_hallazgo('ORDEN_VISITA', 'info', 'Se cambió el orden de visita',
                                   cambios=cambios.count(),
                                   por=sorted(set(cambios.values_list('usuario_nombre', flat=True)))))
    return hallazgos


# 6. Clientes que necesitan atención --------------------------------------------

def _cambio_de_comportamiento(tienda, fecha):
    """Clientes diarios que pagaban casi siempre y en la última semana solo tienen visitas sin pago."""
    corte = fecha - timedelta(days=7)
    historia = defaultdict(lambda: {'abonos_previos': 0, 'registros_previos': 0, 'abonos_recientes': 0, 'fallas_recientes': 0})
    for r in Recaudo.objects.filter(
        tienda=tienda, fecha_recaudo__gt=fecha - timedelta(days=35), fecha_recaudo__lte=fecha, es_renovacion=False,
        venta__estado_venta__in=ESTADOS_ACTIVOS, venta__plazo='Diario',
    ).values('venta_id', 'fecha_recaudo', 'valor_recaudo'):
        h = historia[r['venta_id']]
        pago = (r['valor_recaudo'] or 0) > 0
        if r['fecha_recaudo'] <= corte:
            h['registros_previos'] += 1
            h['abonos_previos'] += int(pago)
        elif pago:
            h['abonos_recientes'] += 1
        else:
            h['fallas_recientes'] += 1
    ids = [v for v, h in historia.items()
           if h['abonos_previos'] >= 10 and h['abonos_previos'] >= 0.85 * h['registros_previos']
           and h['abonos_recientes'] == 0 and h['fallas_recientes'] >= 3]
    ventas = Venta.objects.filter(id__in=ids).select_related('cliente').order_by('-saldo_actual')
    return [{'cliente': _cliente(v), 'venta_id': v.id, 'saldo': _n(v.saldo_actual),
             'abonos_semanas_previas': historia[v.id]['abonos_previos'],
             'visitas_sin_pago_ultima_semana': historia[v.id]['fallas_recientes']} for v in ventas]


def _activos_al(tienda, fecha):
    """Créditos que tenían saldo al cierre de ``fecha`` (misma reconstrucción que cuotas_del_dia).

    El saldo a la fecha es ``saldo_actual`` + todos los cobros posteriores (con su signo,
    incluidas las renovaciones, que cerraron el crédito viejo). Un crédito pagado o declarado
    pérdida después de ``fecha`` estaba activo ese día; uno declarado pérdida antes, no.
    Cada fila trae ``_saldo_al``; las que no tenían saldo se descartan en Python.
    """
    despues = Recaudo.objects.filter(venta_id=OuterRef('pk'), fecha_recaudo__gt=fecha)
    perdida_antes = DeclaracionPerdidaVenta.objects.filter(venta_id=OuterRef('pk'), fecha_declaracion__lte=fecha)
    return (Venta.objects.filter(tienda=tienda, fecha_venta__lte=fecha)
            .filter(Q(estado_venta__in=ESTADOS_ACTIVOS) | Exists(despues) | Q(estado_venta='Perdida'))
            .exclude(Exists(perdida_antes))
            .exclude(Q(estado_venta='Perdida') & ~Exists(DeclaracionPerdidaVenta.objects.filter(venta_id=OuterRef('pk'))))
            .annotate(_despues=Sum('recaudo__valor_recaudo', filter=Q(recaudo__fecha_recaudo__gt=fecha))))


def _con_saldo_al(ventas):
    """Itera los créditos con saldo a la fecha, dejando en memoria el estado de ese día."""
    for v in ventas:
        saldo = Decimal(v.saldo_actual or 0) + Decimal(v._despues or 0)
        if saldo <= 0:
            continue
        if v.estado_venta not in ESTADOS_ACTIVOS:
            v.estado_venta = 'Vigente'  # solo en memoria: ese día seguía activo
        v.saldo_actual = saldo
        yield v


def _hitos(plazo):
    """Tramos de la app (UMBRALES_DSA) más los hitos del informe y el candidato a castigo."""
    umbral = umbrales_dsa(normalizar_plazo(plazo))
    return umbral + tuple(h for h in HITOS_INFORME + (DIAS_CANDIDATO_CASTIGO,) if h > umbral[2])


def _sin_abonar(tienda, fecha):
    """Créditos activos con días completos sin abono real al cierre de ``fecha``.

    Mismos tramos que la app (Ventas/riesgo.UMBRALES_DSA; Diario 3/7/14). Diario usa días
    sin abono; Semanal y Mensual usan el calendario de cuotas como ``riesgo_calendario``:
    solo cuentan si hay importe vencido y se miden en días de mora. ``cruzo_hoy``
    marca a quien llegó justo ese día a un tramo o a un hito del informe (30/60) o al
    candidato a castigo de la app (90), para avisar una vez por hito y no repetir la misma
    lista todos los días. Las visitas sin pago desde el último abono distinguen un cliente
    que no paga de uno al que no se le registró visita. Funciona también hacia atrás
    (``_activos_al``), para comparar con semanas anteriores.
    """
    reales = Q(recaudo__valor_recaudo__gt=0, recaudo__es_renovacion=False, recaudo__fecha_recaudo__lte=fecha)
    netos = Q(recaudo__es_renovacion=False, recaudo__fecha_recaudo__lte=fecha)
    del_dia = Q(recaudo__es_renovacion=False, recaudo__fecha_recaudo=fecha)
    # Los _*_calendario son los atributos que calendario_venta lee si existen (evita N+1).
    ventas = _activos_al(tienda, fecha) \
        .annotate(ultimo=Max('recaudo__fecha_recaudo', filter=reales),
                  _abonos_calendario=Sum('recaudo__valor_recaudo', filter=netos),
                  _gestiones_calendario=Count('recaudo', filter=del_dia),
                  _fallas_calendario=Count('recaudo', filter=del_dia & Q(recaudo__visita_blanco__isnull=False))) \
        .select_related('cliente')
    filas = []
    for v in _con_saldo_al(ventas):
        referencia = v.ultimo or v.fecha_venta
        calendario = calendario_venta(v, consulta=fecha)
        if calendario is None:
            dias = dias_completos_sin_abono(referencia, hoy=fecha + timedelta(days=1))
        elif Decimal(calendario['importe_vencido']) > 0:
            dias = calendario['dias_mora']
        else:
            continue  # al día según su calendario (anticipo o cuota aún no vencida)
        umbral = umbrales_dsa(normalizar_plazo(v.plazo))
        if dias < umbral[0]:
            continue
        hitos = _hitos(v.plazo)
        filas.append({'cliente': _cliente(v), 'venta_id': v.id, 'plazo': v.plazo, 'saldo': _n(v.saldo_actual),
                      'dias_sin_abonar': dias, 'sin_primer_abono': v.ultimo is None,
                      'cuenta': 'dias_sin_abono' if calendario is None else 'dias_con_cuota_vencida',
                      'tramo': 3 if dias >= umbral[2] else 2 if dias >= umbral[1] else 1,
                      'hito': max(h for h in hitos if h <= dias),
                      'candidato_castigo': dias >= DIAS_CANDIDATO_CASTIGO,
                      'cruzo_hoy': dias in hitos, '_desde': referencia})
    if filas:
        visitas = defaultdict(int)
        desde = {f['venta_id']: f['_desde'] for f in filas}
        for venta_id, dia in Recaudo.objects.filter(
            tienda=tienda, fecha_recaudo__gt=min(desde.values()), fecha_recaudo__lte=fecha,
            venta_id__in=list(desde), valor_recaudo__lte=0, es_renovacion=False,
        ).values_list('venta_id', 'fecha_recaudo'):
            if dia > desde[venta_id]:
                visitas[venta_id] += 1
        for f in filas:
            del f['_desde']
            f['visitas_sin_pago_desde_ultimo_abono'] = visitas[f['venta_id']]
    filas.sort(key=lambda f: (not f['cruzo_hoy'], -f['hito'], -(f['saldo'] or 0)))
    return filas


def _abonan_poco(tienda, fecha, excluir=()):
    """Créditos diarios que abonan algo pero se van atrasando en cuotas (el "goteo").

    El sistema los ve al día porque tienen abonos recientes. Se mide igual que la app
    (Venta.dias_atrasados / importe_atrasado_estimado): cuota por jornada registrada contra
    lo abonado, acumulado desde el inicio, así un adelanto no cuenta como atraso. Los
    Semanal y Mensual no van aquí: su calendario ya mide el importe vencido en ``_sin_abonar``.
    ``excluir`` son los que ya figuran sin abonar. ``cruzo_hoy`` = llegó hoy a 7 o 14 cuotas.
    """
    reales = Q(recaudo__es_renovacion=False)
    ventas = _activos_al(tienda, fecha).filter(plazo='Diario').exclude(id__in=list(excluir)).annotate(
        ciclos=Count('recaudo__fecha_recaudo', distinct=True, filter=reales & Q(recaudo__fecha_recaudo__lte=fecha)),
        ciclos_antes=Count('recaudo__fecha_recaudo', distinct=True,
                           filter=reales & Q(recaudo__fecha_recaudo__lt=fecha)),
        del_dia=Sum('recaudo__valor_recaudo', filter=Q(recaudo__fecha_recaudo=fecha)),
    ).select_related('cliente')
    filas = []
    for v in _con_saldo_al(ventas):
        cuota = v.valor_cuota()
        if not cuota:
            continue
        total = v.total_a_pagar()
        abonado = total - v.saldo_actual
        atraso = calcular_cuotas_atrasadas(cuota, v.ciclos, abonado)
        if atraso < ATRASO_CUOTAS_INFORME[0] or abonado <= 0:
            continue
        atraso_antes = calcular_cuotas_atrasadas(cuota, v.ciclos_antes, abonado - Decimal(v.del_dia or 0))
        filas.append({'cliente': _cliente(v), 'venta_id': v.id, 'saldo': _n(v.saldo_actual),
                      'cuotas_atrasadas': float(atraso),
                      'importe_atrasado': _n(importe_atrasado_registrado(cuota, v.ciclos, abonado, v.saldo_actual)),
                      'cuota': _n(round(cuota)), 'pagado_pct': round(abonado / total * 100) if total else None,
                      'cruzo_hoy': any(atraso_antes < h <= atraso for h in ATRASO_CUOTAS_INFORME)})
    filas.sort(key=lambda f: (not f['cruzo_hoy'], -f['importe_atrasado']))
    return filas


def _vencidos(tienda, fecha):
    """Créditos que pasaron su fecha final (ya incluye la tolerancia de la app) con saldo."""
    ventas = _activos_al(tienda, fecha).filter(fecha_vencimiento__lt=fecha).select_related('cliente')
    filas = []
    for v in _con_saldo_al(ventas):
        dias = (fecha - v.fecha_vencimiento).days
        total = v.total_a_pagar()
        filas.append({'cliente': _cliente(v), 'venta_id': v.id, 'plazo': v.plazo, 'saldo': _n(v.saldo_actual),
                      'dias_pasado_el_plazo': dias,
                      'pendiente_pct': round(v.saldo_actual / total * 100) if total else None,
                      'cruzo_hoy': dias in HITOS_VENCIDO})
    filas.sort(key=lambda f: (not f['cruzo_hoy'], -(f['saldo'] or 0)))
    return filas


def _abonos_tarde(tienda, inicio, fin, admin_id):
    """Abonos registrados ``DIAS_TARDE`` o más días después de su fecha.

    La fecha de registro es la de la bitácora (``creado_en``) en la zona de la ruta. Solo
    cuenta la CREACION de un abono real (la conversión de una visita en abono es una
    CORRECCION) y se excluyen las renovaciones. Solo se excluye al dueño (``admin_id``): un
    coadministrador que opera una ruta desde otra ciudad sí se evalúa, igual que en la bitácora.
    """
    zona = zona_horaria_tienda(tienda)
    movimientos = list(MovimientoCaja.objects.filter(
        tienda=tienda, tipo='RECAUDO', accion='CREACION', delta__gt=0, origen_tipo='recaudos.recaudo',
        creado_en__gte=inicio, creado_en__lt=fin).select_related('usuario'))
    if not movimientos:
        return []
    fechas = dict(Recaudo.objects.filter(tienda=tienda, id__in=[m.origen_id for m in movimientos],
                                         es_renovacion=False).values_list('id', 'fecha_recaudo'))
    filas = []
    for m in movimientos:
        fecha_abono = fechas.get(m.origen_id)
        if fecha_abono is None:
            continue
        atraso = (m.creado_en.astimezone(zona).date() - fecha_abono).days
        if atraso >= DIAS_TARDE and not (admin_id and m.usuario_id == admin_id):
            filas.append({'usuario_id': m.usuario_id, 'registrado_por': _nombre(m.usuario), 'monto': m.delta,
                          'dias_atraso': atraso, 'fecha_abono': fecha_abono})
    return filas


def _hallazgos_abonos_tarde(tienda, fecha, admin_id):
    inicio, fin = _ventana(tienda, fecha)
    por_usuario = defaultdict(list)
    for f in _abonos_tarde(tienda, inicio, fin, admin_id):
        por_usuario[f['registrado_por']].append(f)
    hallazgos = []
    for nombre, filas in por_usuario.items():
        cantidad, maximo = len(filas), max(f['dias_atraso'] for f in filas)
        severidad = 'alta' if cantidad >= 5 or maximo >= 5 else 'media' if cantidad >= 2 or maximo >= 3 else 'info'
        hallazgos.append(_hallazgo(
            'ABONOS_REGISTRADOS_TARDE', severidad, 'Registró abonos con fecha de varios días atrás',
            registrado_por=nombre, cantidad=cantidad, monto=_n(sum(f['monto'] for f in filas)),
            dias_atraso_max=maximo, fecha_mas_antigua=min(f['fecha_abono'] for f in filas).isoformat()))
    return hallazgos


def _atencion(tienda, fecha):
    inicio, fin = _ventana(tienda, fecha)
    abiertas = AlertaOperativa.objects.filter(tienda_id_ref=tienda.id, tipo__in=TIPOS_CARTERA, estado__in=ALERTA_ABIERTA)
    nuevas = list(abiertas.filter(creada__gte=inicio, creada__lt=fin))
    urgentes = list(abiertas.filter(severidad='critica'))
    ventas = {v.id: v for v in Venta.objects.filter(
        id__in={a.venta_id_ref for a in nuevas + urgentes if a.venta_id_ref}).select_related('cliente')}
    fila = lambda a: {'cliente': _cliente(ventas[a.venta_id_ref]), 'venta_id': a.venta_id_ref,
                      'saldo': _n(ventas[a.venta_id_ref].saldo_actual), 'severidad': a.severidad,
                      'motivo': {'SIN_PRIMER_ABONO': 'sin primer abono', 'SEGUIMIENTO_PREVENTIVO': 'seguimiento preventivo'}
                      .get(a.tipo, 'mora'), 'dias_sin_abono': (a.datos or {}).get('dias_sin_abono')}
    nuevas = sorted((fila(a) for a in nuevas if a.venta_id_ref in ventas), key=lambda x: -PESO.get(x['severidad'], 0))
    urgentes = sorted((fila(a) for a in urgentes if a.venta_id_ref in ventas), key=lambda x: -(x['saldo'] or 0))
    terminaron = (Venta.objects.filter(tienda=tienda, estado_venta='Pagado', saldo_actual__lte=0,
                                       recaudo__tienda=tienda, recaudo__fecha_recaudo=fecha,
                                       recaudo__valor_recaudo__gt=0, recaudo__es_renovacion=False)
                  .exclude(renovacion__isnull=False).select_related('cliente').distinct())
    cambio = _cambio_de_comportamiento(tienda, fecha)
    sin_abonar = _sin_abonar(tienda, fecha)
    ya_listados = {f['venta_id'] for f in sin_abonar}
    nuevas = [x for x in nuevas if x['venta_id'] not in ya_listados]
    # Los que llevan una semana o más sin abonar ya figuran arriba: no se repiten como "abonan poco".
    poco = _abonan_poco(tienda, fecha, excluir={f['venta_id'] for f in sin_abonar if f['tramo'] >= 2})
    vencidos = _vencidos(tienda, fecha)
    datos = {
        'sin_abonar': sin_abonar[:LIMITE + 5], 'total_sin_abonar': len(sin_abonar),
        'sin_abonar_sin_visitas': sum(1 for f in sin_abonar if not f['visitas_sin_pago_desde_ultimo_abono']),
        'entraron_en_riesgo': nuevas[:LIMITE], 'criticos_por_saldo': urgentes[:5],
        'total_criticos': len(urgentes), 'dejaron_de_pagar_de_golpe': cambio[:LIMITE],
        'terminaron_de_pagar': [{'cliente': _cliente(v), 'venta_id': v.id, 'valor_credito': _n(v.valor_venta)}
                                for v in terminaron[:LIMITE]],
        'abonan_poco': [f for f in poco if f['cruzo_hoy']][:LIMITE], 'total_abonan_poco': len(poco),
        'vencidos_con_saldo': [f for f in vencidos if f['cruzo_hoy']][:LIMITE], 'total_vencidos': len(vencidos),
    }
    hallazgos = []
    # El primer tramo (3 días en diario) es frecuente, sobre todo tras el domingo: va en la
    # lista de clientes sin cambiar el semáforo. El 2.º y 3.º tramo sí son un hallazgo.
    campos = ('cliente', 'venta_id', 'dias_sin_abonar', 'saldo', 'visitas_sin_pago_desde_ultimo_abono',
              'sin_primer_abono', 'candidato_castigo')
    cruzaron = [f for f in sin_abonar if f['cruzo_hoy'] and f['tramo'] >= 2 and f['hito'] < HITOS_INFORME[0]]
    graves = [f for f in sin_abonar if f['cruzo_hoy'] and f['hito'] >= HITOS_INFORME[0]]
    datos['llegaron_al_primer_tramo'] = sum(1 for f in sin_abonar if f['cruzo_hoy'] and f['tramo'] == 1)
    if cruzaron:
        hallazgos.append(_hallazgo(
            'SIN_ABONAR', 'alta', 'Clientes que llegaron a una semana o más sin abonar', cantidad=len(cruzaron),
            clientes=[{k: f[k] for k in campos} for f in cruzaron[:5]]))
    if graves:
        # 30 y 60 días son criterio del informe; 90 es el candidato a castigo de la app.
        hallazgos.append(_hallazgo(
            'POSIBLE_PERDIDA', 'critica' if any(f['candidato_castigo'] for f in graves) else 'alta',
            'Clientes que llegaron a 30, 60 o 90 días sin abonar: riesgo de perder el crédito',
            cantidad=len(graves), clientes=[{k: f[k] for k in campos} for f in graves[:5]]))
    if datos['abonan_poco']:
        hallazgos.append(_hallazgo(
            'ABONAN_POCO', 'media', 'Clientes que abonan algo, pero se están atrasando en cuotas',
            cantidad=len(datos['abonan_poco']), clientes=datos['abonan_poco'][:5]))
    if datos['vencidos_con_saldo']:
        largos = any(f['dias_pasado_el_plazo'] >= 30 for f in datos['vencidos_con_saldo'])
        hallazgos.append(_hallazgo(
            'VENCIDO_CON_SALDO', 'alta' if largos else 'media',
            'Créditos que pasaron su fecha final y todavía deben', cantidad=len(datos['vencidos_con_saldo']),
            clientes=datos['vencidos_con_saldo'][:5]))
    if len(cambio) >= 3:
        hallazgos.append(_hallazgo('PATRON_DEJARON_DE_PAGAR', 'alta',
                                   'Varios buenos pagadores dejaron de pagar a la vez: conviene confirmar con ellos',
                                   clientes=len(cambio)))
    elif cambio:
        hallazgos.append(_hallazgo('DEJO_DE_PAGAR', 'media', 'Buen pagador que dejó de pagar de golpe',
                                   clientes=len(cambio)))
    return datos, hallazgos


# Endpoint ------------------------------------------------------------------------

def _registra_administrador(tienda, fecha, admin_id):
    """True si el administrador registra casi toda la operación de la ruta."""
    if not admin_id:
        return False
    inicio, _ = _ventana(tienda, fecha)
    conteo = MovimientoCaja.objects.filter(
        tienda=tienda, creado_en__gte=inicio - timedelta(weeks=SEMANAS_BASE), creado_en__lt=inicio + timedelta(days=1),
    ).aggregate(total=Count('id'), admin=Count('id', filter=Q(usuario_id=admin_id)))
    return bool(conteo['total']) and conteo['admin'] >= 0.9 * conteo['total']


def informe_ruta(tienda, fecha, admin_id):
    jornada, h_jornada, hubo_actividad = _jornada(tienda, fecha)
    h_bitacora, bitacora = _bitacora(tienda, fecha, admin_id)
    gastos, h_gastos = _gastos(tienda, fecha, hubo_actividad)
    creditos, h_creditos, sobre_cupo = _creditos_nuevos(tienda, fecha, admin_id)
    if sobre_cupo:
        semana = _sobre_cupo_por_trabajador(tienda, fecha - timedelta(days=6), fecha)
        por_venta = dict(Venta.objects.filter(id__in=[f['venta_id'] for f in sobre_cupo]).values_list('id', 'creado_por_id'))
        for f in sobre_cupo:
            creador = por_venta.get(f['venta_id'])
            f['veces_del_trabajador_ultimos_7_dias'] = semana[creador]['creditos'] if creador else None
    h_admin = _acciones_administrativas(tienda, fecha, admin_id)
    atencion, h_atencion = _atencion(tienda, fecha)
    h_tarde = _hallazgos_abonos_tarde(tienda, fecha, admin_id)
    hallazgos = h_jornada + h_bitacora + h_gastos + h_creditos + h_admin + h_atencion + h_tarde
    hallazgos.sort(key=lambda h: -PESO[h['severidad']])
    puntaje = sum(PESO[h['severidad']] for h in hallazgos)
    severidades = {h['severidad'] for h in hallazgos}
    semaforo = 'rojo' if severidades & {'alta', 'critica'} else 'amarillo' if 'media' in severidades else 'verde'
    return {
        'id': tienda.id, 'nombre': tienda.nombre, 'fecha': fecha.isoformat(),
        'trabajadores': _trabajadores(tienda),
        'registra_administrador': _registra_administrador(tienda, fecha, admin_id),
        'semaforo': semaforo, 'puntaje': puntaje, 'hallazgos': hallazgos,
        'jornada': jornada, 'caja': bitacora, 'gastos': gastos, 'creditos_nuevos': creditos,
        'creditos_sobre_cupo': sobre_cupo, 'clientes': atencion,
    }


@endpoint_agente
def informe(request):
    """Informe diario por ruta: anomalías, correcciones, gastos, créditos y clientes a atender."""
    from Tiendas.alertas_operativas import usuario_alertas_configurado
    admin = usuario_alertas_configurado()
    admin_id = admin.id if admin else None
    rutas = []
    for tienda in _filtrar_rutas(request):
        fecha, error = _fecha_ruta(tienda, request.query_params.get('fecha') or 'ayer')
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
        rutas.append(informe_ruta(tienda, fecha, admin_id))
    rutas.sort(key=lambda r: (-r['puntaje'], r['nombre']))
    return Response({'interpretacion': INTERPRETACION,
                     'resumen': {s: sum(r['semaforo'] == s for r in rutas) for s in ('rojo', 'amarillo', 'verde')},
                     'rutas': rutas})
