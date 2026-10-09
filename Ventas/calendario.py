"""Calendario de obligaciones: nunca modifica saldos ni registros históricos."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum, Count, Q
from Tiendas.fecha_operativa import fecha_operativa

NO_DIARIOS = ('Semanal', 'Mensual')
CENTAVO = Decimal('0.01')


def sumar_meses(ancla, meses):
    indice = ancla.year * 12 + ancla.month - 1 + meses
    year, month0 = divmod(indice, 12)
    month = month0 + 1
    return date(year, month, min(ancla.day, monthrange(year, month)[1]))


def fechas_cuotas(fecha_venta, cuotas, plazo, version=0):
    if int(cuotas) < 1 or (version == 1 and int(cuotas) > 120):
        raise ValueError('Las cuotas deben estar entre 1 y 120.')
    for numero in range(1, int(cuotas) + 1):
        if plazo == 'Mensual' and version == 1:
            yield sumar_meses(fecha_venta, numero)
        else:
            yield fecha_venta + timedelta(days=numero * {'Diario': 1, 'Semanal': 7, 'Mensual': 30}[plazo])


def calcular_calendario(*, fecha_venta, cuotas, plazo, total, abonado, consulta,
                       version=0, vencimiento=None, estado='Vigente', gestionado=False):
    fechas = list(fechas_cuotas(fecha_venta, cuotas, plazo, version))
    total = Decimal(str(total)).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    abonos = max(Decimal('0'), min(total, Decimal(str(abonado or 0))))
    acumulados = [(total * n / int(cuotas)).quantize(CENTAVO, rounding=ROUND_HALF_UP)
                  for n in range(1, int(cuotas) + 1)]
    cerradas = sum(fecha < consulta for fecha in fechas)
    hasta_hoy = sum(fecha <= consulta for fecha in fechas)
    exigido_cerrado = acumulados[cerradas - 1] if cerradas else Decimal('0')
    exigido_hoy = acumulados[hasta_hoy - 1] if hasta_hoy else Decimal('0')
    pendiente = max(Decimal('0'), exigido_hoy - abonos)
    vencido = max(Decimal('0'), exigido_cerrado - abonos)
    saldo = max(Decimal('0'), total - abonos)
    anticipo = max(Decimal('0'), abonos - exigido_hoy)
    primera_pendiente = next((f for f, monto in zip(fechas, acumulados) if monto > abonos), None)
    # La fecha histórica almacenada puede incluir la tolerancia contractual.
    final = vencimiento or (fechas[-1] + timedelta(days=4 if version == 0 else 0))
    if estado == 'Perdida':
        calculado = 'Perdida'
    elif estado == 'Pagado' or saldo == 0:
        calculado = 'Pagado'
    elif consulta > final:
        calculado = 'Vencido'
    elif vencido > 0:
        calculado = 'Atrasado'
    else:
        calculado = 'Vigente'
    terminal = calculado in ('Pagado', 'Perdida')
    if terminal:
        pendiente = vencido = anticipo = Decimal('0')
    cuota = total / int(cuotas) if total else Decimal('0')
    atrasadas = (vencido / cuota).quantize(CENTAVO, rounding=ROUND_HALF_UP) if cuota else Decimal('0')
    dias_mora = max(0, (consulta - primera_pendiente).days) if vencido and primera_pendiente else 0
    iso = lambda fecha: fecha.isoformat() if fecha else None
    dinero = lambda monto: format(monto, '.2f')
    return {
        'version': version, 'fecha_consulta': iso(consulta),
        'primera_cuota': iso(fechas[0]), 'ultima_cuota': iso(fechas[-1]),
        'proxima_cuota': iso(primera_pendiente) if not terminal else None,
        'cuotas_vencidas': cerradas, 'cuotas_atrasadas': dinero(atrasadas),
        'importe_vencido': dinero(vencido), 'importe_pendiente_hoy': dinero(pendiente),
        'anticipo': dinero(anticipo), 'saldo_calculado': dinero(saldo),
        'dias_mora': dias_mora, 'estado': calculado, 'gestionado_hoy': bool(gestionado),
        'permite_falla': not terminal and pendiente > 0 and consulta > fecha_venta,
        'pendiente_operativo': not terminal and pendiente > 0 and not gestionado,
        'cobro_voluntario': not terminal and pendiente == 0 and (anticipo > 0 or abonos > exigido_cerrado),
        'visible_liquidar': not terminal and consulta > fecha_venta and (pendiente > 0 or anticipo > 0 or abonos > exigido_cerrado),
    }


def calendario_venta(venta, consulta=None):
    if venta.plazo not in NO_DIARIOS:
        return None
    consulta = consulta or getattr(venta, '_fecha_calendario', None) or fecha_operativa(venta.tienda)
    clave = (consulta, venta.saldo_actual, venta.estado_venta)
    cached = getattr(venta, '_calendario_cache', None)
    if cached and cached[0] == clave:
        return cached[1]
    if hasattr(venta, '_abonos_calendario'):
        abonado = venta._abonos_calendario or 0
        gestionado = bool(venta._gestiones_calendario)
    else:
        resumen = venta.recaudo_set.filter(es_renovacion=False, fecha_recaudo__lte=consulta).aggregate(
            neto=Sum('valor_recaudo'), gestiones=Count('id', filter=Q(fecha_recaudo=consulta)))
        abonado, gestionado = resumen['neto'] or 0, bool(resumen['gestiones'])
    perfil = calcular_calendario(fecha_venta=venta.fecha_venta, cuotas=venta.cuotas,
        plazo=venta.plazo, total=venta.total_a_pagar(), abonado=abonado, consulta=consulta,
        version=venta.calendario_version, vencimiento=venta.fecha_vencimiento,
        estado=venta.estado_venta, gestionado=gestionado)
    venta._calendario_cache = (clave, perfil)
    return perfil


def riesgo_calendario(calendario, plazo, dias_sin_abono=0):
    from Ventas.riesgo import umbrales_dsa, intervalo_cobro
    dias = calendario['dias_mora']
    cuotas = float(calendario['cuotas_atrasadas'])
    en_mora = Decimal(calendario['importe_vencido']) > 0
    atencion, alto, critico = umbrales_dsa(plazo)
    deterioro = 3 if en_mora and dias >= critico else 2 if en_mora and dias >= alto else 1 if en_mora and dias >= atencion else 0
    if calendario['estado'] in ('Pagado', 'Perdida'):
        rank, key, motivo = 0, 'al_dia', 'Crédito cerrado'
    elif calendario['estado'] == 'Vencido':
        rank, key, motivo = 3, 'critico', 'Crédito vencido'
    elif en_mora and (cuotas >= 5 or dias >= critico):
        rank, key, motivo = 3, 'critico', 'Atraso crítico'
    elif en_mora and cuotas >= 2:
        rank, key, motivo = 2, 'urgente', 'Cuotas vencidas pendientes'
    elif en_mora or Decimal(calendario['importe_pendiente_hoy']) > 0:
        rank, key, motivo = 1, 'hoy', 'Cuota vencida pendiente' if en_mora else 'Cuota correspondiente hoy'
    else:
        rank, key, motivo = 0, 'al_dia', 'Sin cuota exigible hoy'
    return {'nivel_cobranza': rank, 'clave_cobranza': key, 'motivo': motivo,
        'nivel_deterioro': deterioro, 'en_mora': en_mora,
        'dias_sin_abono': dias_sin_abono, 'dias_mora': dias, 'cuotas_atrasadas': cuotas,
        'estado_calendario': calendario['estado'],
        'importe_pendiente_hoy': calendario['importe_pendiente_hoy'],
        'intervalo_cobro': intervalo_cobro(plazo),
        'umbrales_dsa': {'atencion': atencion, 'alto': alto, 'critico': critico},
        'umbral_alerta': 0, 'candidato_castigo': en_mora and dias >= 90}


def actualizar_estado(venta):
    """Usar solo dentro de una escritura autorizada, no desde GET."""
    if venta.plazo in NO_DIARIOS:
        venta._calendario_cache = None
        if venta.estado_venta == 'Pagado' and venta.saldo_actual is not None and venta.saldo_actual > 0:
            venta.estado_venta = 'Vigente'
        venta.estado_venta = calendario_venta(venta)['estado']
    else:
        recaudos = venta.recaudo_set.filter(es_renovacion=False)
        venta.estado_venta = 'Vigente' if venta.promedio_pago() >= venta.valor_cuota() else 'Atrasado'
        if venta.cuotas < recaudos.count():
            venta.estado_venta = 'Vencido'
        if venta.saldo_actual is not None and venta.saldo_actual <= 0:
            venta.estado_venta = 'Pagado'


def datos_calendario_score(venta, registros, hoy):
    """Una observación por obligación cerrada; nunca por visita ni por recibo."""
    total = Decimal(str(venta['valor_venta'])) * (1 + Decimal(str(venta.get('interes', 0))) / 100)
    reales = sorted((r for r in registros if not r['es_renovacion'] and r['fecha_recaudo'] <= hoy), key=lambda r: (r['fecha_recaudo'], r['id']))
    neto = sum((Decimal(str(r['valor_recaudo'])) for r in reales), Decimal('0'))
    perfil = calcular_calendario(fecha_venta=venta['fecha_venta'], cuotas=venta['cuotas'],
        plazo=venta['plazo'], total=total, abonado=neto, consulta=hoy,
        version=venta.get('calendario_version', 0), vencimiento=venta.get('fecha_vencimiento'), estado=venta['estado_venta'])
    cierre_renovacion = min((r['fecha_recaudo'] for r in registros if r['es_renovacion']), default=hoy)
    limite = min(hoy, cierre_renovacion + timedelta(days=1))
    observaciones, indice, acumulado = [], 0, Decimal('0')
    for n, fecha in enumerate(fechas_cuotas(venta['fecha_venta'], venta['cuotas'], venta['plazo'], venta.get('calendario_version', 0)), 1):
        if fecha >= limite:
            break
        while indice < len(reales) and reales[indice]['fecha_recaudo'] <= fecha:
            acumulado += Decimal(str(reales[indice]['valor_recaudo']))
            indice += 1
        exigido = (total * n / venta['cuotas']).quantize(CENTAVO, rounding=ROUND_HALF_UP)
        exitoso = acumulado >= exigido
        observaciones.append({'id': -n, 'venta_id': venta['id'], 'fecha_recaudo': fecha,
            'valor_recaudo': 1 if exitoso else 0, 'visita_blanco_id': None if exitoso else -1, 'es_renovacion': False})
    return perfil, observaciones
