"""Extensión opt-in v2. Preserva las fórmulas y el contrato del reporte anterior."""
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Exists, OuterRef, Q, Sum
from django.utils import timezone

from Tiendas.fecha_operativa import fecha_operativa, nombre_zona_horaria
from Tiendas.models import MovimientoCaja
from Ventas.models import Venta, DeclaracionPerdidaVenta
from Ventas.perdidas import desglose_pendiente


CAMPOS = ('capitalNuevos', 'capitalRenovado', 'capitalSinClasificar',
          'capitalPerdidaDeclarado', 'interesPerdidaDeclarado',
          'cantidadDeclaracionesPerdida', 'declaracionesPorRevisar')


def monto(valor):
    return int(Decimal(valor or 0).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def ampliar_reporte(tienda, inicio, fin, filas, nueva_fila):
    # Copia: el contrato v1 y sus cifras no se alteran.
    por_fecha = {fila['fecha']: dict(fila) for fila in filas}

    def obtener(fecha):
        clave = fecha.isoformat()
        if clave not in por_fecha:
            por_fecha[clave] = dict(nueva_fila(fecha))
        return por_fecha[clave]

    for fila in por_fecha.values():
        fila.update({campo: 0 for campo in CAMPOS})

    auditada = MovimientoCaja.objects.filter(
        tienda_id=tienda.pk, tipo='VENTA', accion='CREACION',
        origen_tipo='ventas.venta', origen_id=OuterRef('pk'), delta__lt=0,
    )
    ventas = Venta.objects.filter(tienda=tienda, fecha_venta__range=[inicio, fin]).annotate(
        _nuevo_auditado=Exists(auditada),
    ).values('fecha_venta', 'valor_venta', 'tipo_origen', 'origen_renovacion_id', '_nuevo_auditado')
    for venta in ventas:
        fila = obtener(venta['fecha_venta'])
        if venta['tipo_origen'] == 'Renovacion' or venta['origen_renovacion_id']:
            campo = 'capitalRenovado'
        elif venta['tipo_origen'] == 'Nuevo' or venta['_nuevo_auditado']:
            campo = 'capitalNuevos'
        else:
            campo = 'capitalSinClasificar'
        fila[campo] += monto(venta['valor_venta'])

    declaraciones = DeclaracionPerdidaVenta.objects.filter(
        tienda_id_ref=tienda.pk, fecha_declaracion__range=[inicio, fin],
    ).values('fecha_declaracion', 'capital_no_recuperado', 'interes_no_cobrado', 'saldo_consistente')
    for declaracion in declaraciones:
        fila = obtener(declaracion['fecha_declaracion'])
        for campo in CAMPOS:
            fila.setdefault(campo, 0)
        fila['capitalPerdidaDeclarado'] += monto(declaracion['capital_no_recuperado'])
        fila['interesPerdidaDeclarado'] += monto(declaracion['interes_no_cobrado'])
        fila['cantidadDeclaracionesPerdida'] += 1
        fila['declaracionesPorRevisar'] += int(not declaracion['saldo_consistente'])

    # Estado ACTUAL de toda la ruta, no imputación al período consultado.
    historicas = Venta.objects.filter(
        tienda=tienda, estado_venta='Perdida', declaraciones_perdida__isnull=True,
    ).annotate(_netos=Sum('recaudo__valor_recaudo', filter=Q(recaudo__es_renovacion=False))).values(
        'valor_venta', 'interes', '_netos',
    )
    cantidad, capital = 0, Decimal('0')
    for venta in historicas:
        cantidad += 1
        pendiente, _ = desglose_pendiente(venta['valor_venta'], venta['interes'], venta['_netos'])
        capital += pendiente
    ahora = timezone.now()
    return {
        'version': 2,
        'filas': sorted(por_fecha.values(), key=lambda fila: fila['fecha'], reverse=True),
        'contexto': {
            'tiendaId': tienda.pk, 'inicio': inicio.isoformat(), 'fin': fin.isoformat(),
            'consultadoEn': ahora.isoformat(), 'fechaConsulta': fecha_operativa(tienda, ahora).isoformat(),
            'zonaHoraria': nombre_zona_horaria(tienda),
            'perdidasSinFecha': {'cantidad': cantidad, 'capitalActual': monto(capital)},
            'criterioDeclaraciones': 'brutas_sin_neteo_de_recuperaciones',
        },
    }
