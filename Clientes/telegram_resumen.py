"""Representación mínima para clientes. Sin red ni cambios financieros.

El llamador debe verificar la vinculación antes de construir el resumen.
Los abonos reales excluyen siempre los asientos de renovación.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, Sum

from Tiendas.fecha_operativa import fecha_operativa
from Ventas.riesgo import (
    calcular_cuotas_atrasadas,
    dias_completos_sin_abono,
    intervalo_cobro,
)


class ResumenInconsistente(ValueError):
    """Importes no conciliados; no deben enviarse al cliente."""


def primer_nombre(valor):
    """Devuelve solo el primer nombre, sin saltos ni texto controlable."""
    partes = ' '.join(str(valor or '').split()).split(' ')
    return (partes[0] if partes and partes[0] else 'Cliente')[:40]


def estado_credito_visible(datos):
    """Mapea estados internos a etiquetas comprensibles para el cliente."""
    if datos.get('cierre') == 'renovacion':
        return '🔄 Finalizado por renovación'
    if datos.get('cierre') == 'pagado' or datos.get('estado_venta') == 'Pagado':
        return '✅ Pagado'
    if datos.get('estado_venta') == 'Vencido':
        return '🔴 Vencido'
    calendario = datos.get('calendario_pago')
    if calendario:
        # La antigüedad del último recibo no implica mora si las cuotas están cubiertas.
        return '🟠 Con atraso' if Decimal(calendario['importe_vencido']) > 0 else '🟢 Al día'
    try:
        cuotas_atrasadas = Decimal(str(datos.get('cuotas_atrasadas', 0)))
        dias_sin_abono = int(datos.get('dias_sin_abono', 0) or 0)
        intervalo = intervalo_cobro(datos.get('frecuencia'))
    except (TypeError, ValueError, ArithmeticError):
        cuotas_atrasadas, dias_sin_abono, intervalo = Decimal('0'), 0, 1
    if (datos.get('estado_venta') == 'Atrasado'
            or cuotas_atrasadas > 0
            or dias_sin_abono >= intervalo):
        return '🟠 Con atraso'
    return '🟢 Al día'


def resumen_credito(venta):
    total = Decimal(venta.total_a_pagar())
    saldo = venta.saldo_actual
    if saldo is None or total <= 0 or saldo < 0:
        raise ResumenInconsistente('Crédito con importes inconsistentes')
    movimientos = venta.recaudo_set
    reales_qs = movimientos.filter(es_renovacion=False)
    reales_resumen = reales_qs.aggregate(
        total=Sum('valor_recaudo'), ciclos=Count('id'),
    )
    reales = reales_resumen['total'] or Decimal('0')
    transferidos = movimientos.filter(es_renovacion=True).aggregate(
        total=Sum('valor_recaudo'),
    )['total'] or Decimal('0')
    if abs(total - saldo - reales - transferidos) > Decimal('0.01'):
        raise ResumenInconsistente('Saldo y movimientos requieren conciliación')
    if reales < 0 or transferidos < 0 or reales > total:
        raise ResumenInconsistente('Abonos inconsistentes')
    progreso = (reales * 100 / total).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP,
    )
    ultimo_abono = reales_qs.filter(valor_recaudo__gt=0).order_by(
        '-fecha_recaudo', '-id',
    ).first()
    referencia_sin_abono = ultimo_abono.fecha_recaudo if ultimo_abono else venta.fecha_venta
    dias_sin_abono = dias_completos_sin_abono(
        referencia_sin_abono, hoy=fecha_operativa(venta.tienda),
    )
    cuotas_atrasadas = calcular_cuotas_atrasadas(
        venta.valor_cuota(), reales_resumen['ciclos'], reales,
    )
    if not ultimo_abono:
        cuotas_atrasadas = max(
            cuotas_atrasadas,
            Decimal(max(0, dias_sin_abono // intervalo_cobro(venta.plazo))),
        )
    from Ventas.calendario import calendario_venta
    calendario = calendario_venta(venta)
    if calendario:
        cuotas_atrasadas = Decimal(calendario['cuotas_atrasadas'])
    return {
        'credito': venta.pk,
        'cliente_primer_nombre': primer_nombre(
            getattr(getattr(venta, 'cliente', None), 'nombres', ''),
        ),
        'ruta': venta.tienda.nombre,
        'fecha': venta.fecha_venta.isoformat(),
        'monto': str(venta.valor_venta),
        'total_a_pagar': str(total),
        'cuotas': venta.cuotas,
        'frecuencia': venta.plazo,
        'valor_cuota': str(venta.valor_cuota()),
        'estado_venta': calendario['estado'] if calendario else getattr(venta, 'estado_venta', 'Vigente'),
        'calendario_pago': calendario,
        'fecha_vencimiento': (
            venta.fecha_vencimiento.isoformat()
            if getattr(venta, 'fecha_vencimiento', None) else None
        ),
        'fecha_ultimo_abono': (
            ultimo_abono.fecha_recaudo.isoformat() if ultimo_abono else None
        ),
        'monto_ultimo_abono': str(ultimo_abono.valor_recaudo) if ultimo_abono else None,
        'dias_sin_abono': dias_sin_abono,
        'cuotas_atrasadas': str(cuotas_atrasadas),
        'saldo': str(saldo),
        'total_abonado': str(reales),
        'saldo_transferido_renovacion': str(transferidos),
        'progreso': str(progreso),
        'cierre': ('renovacion' if transferidos else
                   'pagado' if saldo == 0 else 'pendiente'),
    }
