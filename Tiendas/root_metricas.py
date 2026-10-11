"""Indicadores internos: ledger vigente, períodos equivalentes y cohortes explícitas."""
import calendar
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Count, OuterRef, Subquery, Sum, Q
from django.db.models.functions import TruncMonth
from .models import CambioSuscripcion, PagoMembresia, SolicitudPago, Tienda, Tienda_Membresia

MESES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']


def mes_desplazado(hoy, desplazamiento):
    numero = hoy.year * 12 + hoy.month - 1 + desplazamiento
    return date(numero // 12, numero % 12 + 1, 1)


def periodos_mes(hoy):
    previo = mes_desplazado(hoy, -1)
    dias = min(hoy.day, calendar.monthrange(previo.year, previo.month)[1])
    return (hoy.replace(day=1), hoy.replace(day=dias), previo, previo.replace(day=dias))


def periodos_anio(year, hoy):
    if year > hoy.year or not 1901 <= year <= 9999:
        raise ValueError('Selecciona un año válido, no posterior al actual.')
    if year < hoy.year:
        return date(year, 1, 1), date(year, 12, 31), date(year - 1, 1, 1), date(year - 1, 12, 31)
    dia = min(hoy.day, calendar.monthrange(year - 1, hoy.month)[1])
    return date(year, 1, 1), date(year, hoy.month, dia), date(year - 1, 1, 1), date(year - 1, hoy.month, dia)


def comparar_ledger(periodos):
    inicio, fin, previo, fin_previo = periodos
    data = PagoMembresia.objects.filter(estado='vigente').aggregate(
        actual=Sum('monto', filter=Q(fecha__range=(inicio, fin))),
        anterior=Sum('monto', filter=Q(fecha__range=(previo, fin_previo))))
    actual, anterior = data['actual'] or Decimal(0), data['anterior'] or Decimal(0)
    return {'actual': float(actual), 'anterior': float(anterior),
        'variacion_pct': round(float((actual - anterior) * 100 / anterior), 1) if anterior > 0 else None,
        'inicio': str(inicio), 'fin': str(fin), 'inicio_anterior': str(previo), 'fin_anterior': str(fin_previo)}


def cohortes_registro(hoy):
    """Rutas aún existentes (incluidas archivadas), con 30 días de observación.

    No presupone trial. Pago = primer ledger positivo vigente, no prueba bancaria.
    No incluye rutas eliminadas ni inventa su fecha de registro.
    """
    primer_pago = PagoMembresia.objects.filter(tienda_id=OuterRef('pk'), estado='vigente',
        monto__gt=0, fecha__gte=OuterRef('fecha_registro'), fecha__lte=hoy,
        membresia__nombre__in=('Mensual', 'Anual')).order_by('fecha').values('fecha')[:1]
    filas = Tienda.objects.filter(fecha_registro__range=(mes_desplazado(hoy, -5), hoy)).annotate(
        primer_pago=Subquery(primer_pago)).values('fecha_registro', 'primer_pago')
    meses = {mes_desplazado(hoy, i): {'mes': str(mes_desplazado(hoy, i))[:7],
        'registradas': 0, 'evaluadas': 0, 'con_pago_30d': 0, 'en_observacion': 0} for i in range(-5, 1)}
    for fila in filas:
        registro = fila['fecha_registro']
        grupo = meses[registro.replace(day=1)]
        grupo['registradas'] += 1
        if registro + timedelta(days=30) > hoy:
            grupo['en_observacion'] += 1
            continue
        grupo['evaluadas'] += 1
        if fila['primer_pago'] and fila['primer_pago'] < registro + timedelta(days=30):
            grupo['con_pago_30d'] += 1
    for grupo in meses.values():
        grupo['conversion_pct'] = round(grupo['con_pago_30d'] * 100 / grupo['evaluadas'], 1) if grupo['evaluadas'] else None
    return list(meses.values())


def resumen_root(hoy):
    members = list(Tienda_Membresia.objects.select_related('tienda', 'membresia').all())
    actuales = [tm for tm in members if not tm.archivada]
    estados = Counter(tm.estado for tm in actuales)
    planes = Counter(tm.membresia.nombre for tm in actuales if tm.estado == 'Activa')
    inicio = hoy.replace(day=1)
    pagos = PagoMembresia.objects.filter(estado='vigente', fecha__lte=hoy)
    importes = pagos.aggregate(mes=Sum('monto', filter=Q(fecha__gte=inicio)),
        anterior=Sum('monto', filter=Q(fecha__range=(mes_desplazado(hoy, -1), inicio - timedelta(days=1)))),
        anio=Sum('monto', filter=Q(fecha__gte=hoy.replace(month=1, day=1))),
        renovaciones=Count('id', filter=Q(fecha__gte=inicio)))
    grupos = pagos.filter(fecha__gte=mes_desplazado(hoy, -5)).annotate(mes=TruncMonth('fecha')).values('mes').annotate(total=Sum('monto'))
    serie = {g['mes']: g['total'] for g in grupos}
    mrr = sum((tm.membresia.precio / (12 if tm.membresia.nombre == 'Anual' else 1)
        for tm in actuales if tm.estado == 'Activa' and tm.membresia.nombre in ('Mensual', 'Anual')), Decimal(0))
    con_pago = set(pagos.filter(monto__gt=0).exclude(tienda_id=None).values_list('tienda_id', flat=True).distinct())
    vencidas_estimadas = sum(tm.estado == 'Vencida' and inicio - timedelta(days=2) <= tm.fecha_vencimiento <= hoy - timedelta(days=2) for tm in actuales)
    trial_perdido = sum(tm.membresia.nombre == 'Prueba' and tm.estado == 'Vencida' and tm.tienda_id not in con_pago for tm in members)
    convertidas = sum(tm.tienda_id in con_pago for tm in members)
    suspensiones = CambioSuscripcion.objects.filter(accion='vencimiento_manual', fecha__date__range=(inicio, hoy)).aggregate(
        eventos=Count('id'), rutas=Count('tienda_id', distinct=True))
    # Legacy: estos nombres siguen presentes, pero la UI nueva no los llama churn/trial.
    base = convertidas + trial_perdido
    return {'fecha_corte': str(hoy),
        'rutas': {'total': len(actuales), 'activas': estados['Activa'], 'pendientes': estados['Pendiente Pago'],
            'vencidas': estados['Vencida'], 'preactivadas': estados['Pre-activada'], 'archivadas': len(members) - len(actuales)},
        'por_vencer': sum(tm.estado == 'Activa' and hoy <= tm.fecha_vencimiento <= hoy + timedelta(days=3) for tm in actuales),
        'ingresos_mes': float(importes['mes'] or 0), 'ingresos_anio': float(importes['anio'] or 0),
        'ingresos_mes_anterior': float(importes['anterior'] or 0),
        'comparacion_mes': comparar_ledger(periodos_mes(hoy)), 'renovaciones_mes': importes['renovaciones'],
        'mrr_estimado': round(float(mrr)), 'conciliacion_pendiente': SolicitudPago.objects.filter(estado='pendiente_confirmacion').count(),
        'ingresos_6m': [{'label': MESES[m.month - 1], 'anio': m.year, 'mes': m.month,
            'total': float(serie.get(m, 0)), 'parcial': m == inicio} for m in (mes_desplazado(hoy, i) for i in range(-5, 1))],
        'distribucion_plan': dict(planes), 'nuevas_rutas_mes': Tienda.objects.filter(fecha_registro__range=(inicio, hoy)).count(),
        'retencion': {'bloqueadas_mes': vencidas_estimadas, 'conversion_trial': round(convertidas * 100 / base) if base else None,
            'trials_en_curso': sum(tm.membresia.nombre == 'Prueba' and tm.estado == 'Activa' for tm in actuales),
            'trials_perdidos': trial_perdido, 'sin_primer_pago_vencidas': sum(tm.estado == 'Vencida' and tm.tienda_id not in con_pago for tm in actuales),
            'suspensiones_manuales_mes': suspensiones['eventos'], 'rutas_suspendidas_mes': suspensiones['rutas']},
        'cohortes_registro': cohortes_registro(hoy)}
