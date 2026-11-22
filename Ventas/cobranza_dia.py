"""Base compartida de cuotas del día; no cambia saldos ni acumula mora."""
from decimal import Decimal
from django.db.models import Exists, OuterRef, Q, Sum
from Recaudos.models import Recaudo
from Ventas.models import Venta
from Ventas.calendario import importe_cuota_fecha


def cuotas_del_dia(tienda, fecha, fecha_maxima=None):
    # Incluir créditos pagados durante/después de la jornada conserva su base
    # original. Es la misma reconstrucción que utiliza Liquidar.
    # Prefiltro equivalente: un crédito con saldo 0 solo puede calificar si
    # tiene algún cobro desde la fecha. Evita sumar los cobros de todo el
    # historial de créditos pagados (usa el índice tienda+fecha_recaudo).
    cobros_desde_fecha = Recaudo.objects.filter(
        tienda=tienda, fecha_recaudo__gte=fecha, venta_id=OuterRef('pk'))
    base = Venta.objects.filter(tienda=tienda, fecha_venta__lt=fecha).exclude(
        estado_venta='Perdida').filter(
            Q(saldo_actual__gt=0) | Exists(cobros_desde_fecha)).annotate(
            _pagos_desde_fecha=Sum('recaudo__valor_recaudo',
                filter=Q(recaudo__fecha_recaudo__gte=fecha))).filter(
                    Q(saldo_actual__gt=0) | Q(_pagos_desde_fecha__gt=0))
    if fecha_maxima is not None:
        base = base.filter(fecha_venta__lte=fecha_maxima)
    for venta in base.select_related('cliente'):
        saldo_inicio = Decimal(venta.saldo_actual or 0) + Decimal(venta._pagos_desde_fecha or 0)
        importe = importe_cuota_fecha(fecha_venta=venta.fecha_venta,
            cuotas=venta.cuotas, plazo=venta.plazo, total=venta.total_a_pagar(),
            saldo_inicio=saldo_inicio, consulta=fecha, version=venta.calendario_version)
        yield venta, importe
