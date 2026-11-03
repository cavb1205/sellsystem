"""Cierres con la caja actual: solo si corresponde a la fecha solicitada.

La escritura llama a esta validación con la fila de Tienda bloqueada, igual
que las operaciones que cambian caja. La consulta previa nunca autoriza por sí
sola el cierre ni reconstruye balances históricos.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
from django.db.models import F, Q
from .models import Cierre_Caja, MovimientoCaja
from .fecha_operativa import fecha_operativa, zona_horaria_tienda
from .permissions import usuario_es_administrador_tienda

ESTADOS_HTTP = {
    'fecha_futura': 400, 'fecha_anterior_ruta': 400,
    'solo_hoy_trabajador': 403,
}


def _origenes_de_caja():
    from Aportes.models import Aporte
    from Gastos.models import Gasto
    from Recaudos.models import Recaudo
    from Utilidades.models import Utilidad
    from Ventas.models import Venta

    return {
        'APORTE': (Aporte, 'fecha', 'valor', 1, {}),
        'GASTO': (Gasto, 'fecha', 'valor', -1, {}),
        'UTILIDAD': (Utilidad, 'fecha', 'valor', -1, {}),
        'RECAUDO': (Recaudo, 'fecha_recaudo', 'valor_recaudo', 1, {'es_renovacion': False}),
        'VENTA': (Venta, 'fecha_venta', 'valor_venta', -1,
                  {'origen_renovacion__isnull': True, 'tipo_origen__in': ['', 'Nuevo']}),
    }


def _hay_cambios_posteriores(movimientos, corte, fecha, tienda_id, origenes):
    """Solo las altas con origen, fecha e importe comprobables pueden ser tardías."""
    posteriores = movimientos.filter(creado_en__gte=corte).filter(
        ~Q(delta=0) | Q(accion='CORRECCION'))
    asientos_por_tipo = {}
    for tipo, accion, etiqueta, origen_id, delta in posteriores.values_list(
            'tipo', 'accion', 'origen_tipo', 'origen_id', 'delta').iterator():
        if accion != 'CREACION' or tipo not in origenes or origen_id is None:
            return True
        if etiqueta != origenes[tipo][0]._meta.label_lower:
            return True
        asientos = asientos_por_tipo.setdefault(tipo, {})
        if origen_id in asientos:
            return True
        asientos[origen_id] = delta

    for tipo, asientos in asientos_por_tipo.items():
        modelo, campo_fecha, campo_valor, signo, filtros = origenes[tipo]
        requisitos = {'venta__tienda_id': tienda_id, 'visita_blanco__isnull': True} if tipo == 'RECAUDO' else {}
        importes = dict(modelo.objects.filter(
            pk__in=asientos, tienda_id=tienda_id,
            **filtros, **requisitos, **{campo_fecha+'__lte': fecha, campo_valor+'__gt': 0},
        ).values_list('pk', campo_valor))
        for origen_id, delta in asientos.items():
            importe = importes.get(origen_id)
            if importe is None or signo * importe != delta:
                return True
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
    from Ventas.models import AjusteVentaAdministrativo

    origenes = _origenes_de_caja()
    ajustes_fecha = AjusteVentaAdministrativo.objects.filter(
        venta__tienda_id=tienda.pk, creada__gte=corte,
    ).exclude(fecha_venta_anterior=F('fecha_venta_nueva'))
    if _hay_cambios_posteriores(movimientos, corte, fecha, tienda.pk, origenes) or ajustes_fecha.exists():
        return bloquear('movimientos_posteriores', 'No puedes cerrar esta fecha con el saldo actual: hubo cambios de caja posteriores, incluidas correcciones o anulaciones.')

    # También detectar operaciones fechadas después que no tengan auditoría
    # (datos anteriores al registro, importaciones o cambios de fecha).
    for modelo, campo_fecha, campo_valor, _, filtros in origenes.values():
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
