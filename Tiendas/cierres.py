"""Cierres con la caja actual: solo si corresponde a la fecha solicitada.

La escritura llama a esta validación con la fila de Tienda bloqueada, igual
que las operaciones que cambian caja. La consulta previa nunca autoriza por sí
sola el cierre ni reconstruye balances históricos.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
from django.db.models import Q
from .models import Cierre_Caja, MovimientoCaja
from .fecha_operativa import fecha_operativa, zona_horaria_tienda
from .permissions import usuario_es_administrador_tienda

ESTADOS_HTTP = {
    'fecha_futura': 400, 'fecha_anterior_ruta': 400,
    'solo_hoy_trabajador': 403,
}


def _hay_cambios_posteriores(movimientos, corte, fecha, tienda_id):
    """Un abono ingresado tarde puede pertenecer al día que se quiere cerrar.

    Solo se admite su creación si el origen sigue siendo un pago real de la
    misma ruta y su importe coincide. Correcciones (incluso de solo fecha),
    reversas, ajustes y referencias sin respaldo mantienen el bloqueo.
    """
    from Recaudos.models import Recaudo

    posteriores = movimientos.filter(creado_en__gte=corte).filter(
        ~Q(delta=0) | Q(tipo='RECAUDO', accion='CORRECCION'))
    if posteriores.exclude(
            tipo='RECAUDO', accion='CREACION', delta__gt=0,
            origen_tipo=Recaudo._meta.label_lower, origen_id__isnull=False).exists():
        return True

    # Una consulta para todos los orígenes: no consultar un cliente por asiento.
    abonos = dict(Recaudo.objects.filter(
        pk__in=posteriores.values('origen_id'), tienda_id=tienda_id,
        venta__tienda_id=tienda_id, fecha_recaudo__lte=fecha,
        valor_recaudo__gt=0, es_renovacion=False, visita_blanco__isnull=True,
    ).values_list('pk', 'valor_recaudo'))
    comprobados = set()
    for origen_id, delta in posteriores.values_list('origen_id', 'delta').iterator():
        if origen_id in comprobados or abonos.get(origen_id) != delta:
            return True
        comprobados.add(origen_id)
    return False


def validar_cierre(tienda, fecha, usuario):
    hoy = fecha_operativa(tienda)
    result = {'fecha': fecha.isoformat(), 'tienda_id': tienda.pk,
        'hoy': hoy.isoformat(), 'puede_registrar': False, 'codigo': '',
        'mensaje': '', 'saldo_a_registrar': None, 'cierre_existente': None,
        'historial_desde': None}

    def bloquear(codigo, mensaje):
        return {**result, 'codigo': codigo, 'mensaje': mensaje}

    if fecha > hoy:
        return bloquear('fecha_futura', 'No puedes registrar un cierre de una fecha futura.')
    if fecha != hoy and not usuario_es_administrador_tienda(usuario, tienda.pk):
        return bloquear('solo_hoy_trabajador', 'El trabajador solo puede cerrar el día actual de la ruta.')
    existente = Cierre_Caja.objects.filter(tienda_id=tienda.pk, fecha_cierre=fecha).order_by('-id').first()
    if existente:
        result['cierre_existente'] = {'id': existente.pk, 'fecha_cierre': fecha.isoformat(), 'valor': str(existente.valor)}
        return bloquear('cierre_existente', 'Ya existe un cierre registrado para esta fecha.')
    if fecha == hoy:
        return {**result, 'puede_registrar': True, 'codigo': 'dia_actual',
            'mensaje': 'Se registrará el saldo actual de caja.', 'saldo_a_registrar': str(tienda.caja_inicial)}

    if fecha < tienda.fecha_registro:
        return bloquear('fecha_anterior_ruta', 'La fecha es anterior a la creación de la ruta.')

    # El corte es el comienzo del día siguiente EN LA ZONA DE LA RUTA.
    corte = datetime.combine(fecha + timedelta(days=1), time.min, tzinfo=zona_horaria_tienda(tienda))
    movimientos = MovimientoCaja.objects.filter(tienda_id=tienda.pk)
    primero = movimientos.order_by('creado_en', 'id').only('creado_en').first()
    if primero:
        result['historial_desde'] = fecha_operativa(tienda, primero.creado_en).isoformat()
    if _hay_cambios_posteriores(movimientos, corte, fecha, tienda.pk):
        return bloquear('movimientos_posteriores', 'No puedes cerrar esta fecha con el saldo actual: hubo cambios de caja posteriores, incluidas correcciones o anulaciones.')

    # También detectar operaciones fechadas después que no tengan auditoría
    # (datos anteriores al registro, importaciones o cambios de fecha).
    from Aportes.models import Aporte
    from Gastos.models import Gasto
    from Utilidades.models import Utilidad
    from Recaudos.models import Recaudo
    from Ventas.models import Venta
    for modelo, campo_fecha, campo_valor, filtros in (
        (Aporte, 'fecha', 'valor', {}), (Gasto, 'fecha', 'valor', {}),
        (Utilidad, 'fecha', 'valor', {}),
        (Recaudo, 'fecha_recaudo', 'valor_recaudo', {'es_renovacion': False}),
        (Venta, 'fecha_venta', 'valor_venta', {}),
    ):
        if modelo.objects.filter(tienda_id=tienda.pk, **{campo_fecha+'__gt': fecha}, **filtros).exclude(
                Q(**{campo_valor: 0}) | Q(**{campo_valor+'__isnull': True})).exists():
            return bloquear('movimientos_posteriores', 'No puedes cerrar esta fecha con el saldo actual: hay movimientos con fecha posterior al día seleccionado.')
    if not primero or primero.creado_en >= corte:
        return bloquear('historial_insuficiente', 'No hay historial de caja suficiente para verificar el saldo de esta fecha. No se registró ningún cierre.')
    ultimo = movimientos.order_by('-creado_en', '-id').only('saldo_posterior').first()
    if Decimal(str(ultimo.saldo_posterior)) != Decimal(str(tienda.caja_inicial)):
        return bloquear('saldo_sin_respaldo', 'El saldo actual no coincide con el último movimiento auditado. Revisa la caja antes de registrar un cierre anterior.')
    return {**result, 'puede_registrar': True, 'codigo': 'sin_movimientos_posteriores',
        'mensaje': 'Puedes cerrar esta fecha: no hay movimientos que correspondan a días posteriores.',
        'saldo_a_registrar': str(tienda.caja_inicial)}
