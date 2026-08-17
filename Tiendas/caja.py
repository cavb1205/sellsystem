from decimal import Decimal

from django.db import connection

from .models import MovimientoCaja


def registrar_movimiento_caja(
    tienda,
    delta,
    *,
    tipo,
    usuario=None,
    accion='CREACION',
    origen=None,
    origen_tipo=None,
    origen_id=None,
    detalle='',
):
    """Aplica un cambio de caja y lo registra en la misma transacción.

    Todas las vistas financieras llaman a esta función después de bloquear la
    fila de ``Tienda`` con ``select_for_update()``. Exigir una transacción
    evita que pueda existir un saldo sin su respectiva fila de auditoría.
    """
    if connection.get_autocommit():
        raise RuntimeError('Los movimientos de caja deben registrarse dentro de una transacción.')

    delta = Decimal(str(delta or 0))
    saldo_anterior = Decimal(str(tienda.caja_inicial or 0))
    saldo_posterior = saldo_anterior + delta
    tienda.caja_inicial = saldo_posterior
    tienda.save(update_fields=['caja_inicial'])

    if origen is not None:
        origen_tipo = origen_tipo or origen._meta.label_lower
        origen_id = origen_id or origen.pk

    usuario_registro = usuario if getattr(usuario, 'is_authenticated', False) else None
    return MovimientoCaja.objects.create(
        tienda=tienda,
        tienda_nombre=tienda.nombre,
        tipo=tipo,
        accion=accion,
        delta=delta,
        saldo_anterior=saldo_anterior,
        saldo_posterior=saldo_posterior,
        usuario=usuario_registro,
        origen_tipo=origen_tipo or '',
        origen_id=origen_id,
        detalle=detalle or '',
    )
