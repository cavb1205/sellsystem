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


def _asientos_conciliados(movimientos, corte, tienda_id, origenes):
    """Verifica el efecto vigente de cada operación modificada tras el corte.

    La fecha de registro solo identifica qué operaciones necesitan revisión.
    Para un origen vigente, la suma de alta y correcciones debe coincidir con
    el importe guardado. Un origen eliminado requiere una reversa exacta de
    su propio historial. Compensar operaciones distintas no es suficiente.
    """
    posteriores = movimientos.filter(creado_en__gte=corte).filter(
        ~Q(delta=0) | Q(accion='CORRECCION'))
    origenes_por_tipo = {}
    for tipo, accion, etiqueta, origen_id in posteriores.values_list(
            'tipo', 'accion', 'origen_tipo', 'origen_id').iterator():
        if tipo not in origenes or origen_id is None:
            return False
        if accion not in ('CREACION', 'CORRECCION', 'REVERSA'):
            return False
        if etiqueta != origenes[tipo][0]._meta.label_lower:
            return False
        origenes_por_tipo.setdefault(tipo, set()).add(origen_id)

    for tipo, ids in origenes_por_tipo.items():
        modelo, _, campo_valor, signo, filtros = origenes[tipo]
        historial = movimientos.filter(tipo=tipo, origen_tipo=modelo._meta.label_lower,
                                       origen_id__in=ids).order_by('creado_en', 'pk')
        saldos = {}
        anulados = set()
        for origen_id, accion, delta in historial.values_list('origen_id', 'accion', 'delta').iterator():
            if origen_id in anulados:
                return False
            if accion == 'CREACION' and origen_id not in saldos and signo * delta > 0:
                saldos[origen_id] = delta
            elif accion == 'CORRECCION' and origen_id in saldos:
                saldos[origen_id] += delta
                if signo * saldos[origen_id] < 0:
                    return False
            elif accion == 'REVERSA' and origen_id in saldos and saldos[origen_id] == -delta:
                anulados.add(origen_id)
                del saldos[origen_id]
            else:
                return False

        if (saldos.keys() | anulados) != ids:
            return False
        if tipo == 'RECAUDO':
            filtros = {**filtros, 'venta__tienda_id': tienda_id, 'visita_blanco__isnull': True}
        vigentes = modelo.objects.filter(pk__in=saldos, tienda_id=tienda_id, **filtros)
        importes = dict(vigentes.values_list('pk', campo_valor))
        if any(importe is None or saldos[pk] != signo * importe for pk, importe in importes.items()):
            return False
        # Borrar un abono ya corregido a cero no genera un asiento de reversa.
        # En ambos casos, confirmar que el registro fue eliminado de verdad.
        faltantes = saldos.keys() - importes.keys()
        if any(saldos[pk] != 0 for pk in faltantes):
            return False
        if modelo.objects.filter(pk__in=anulados | faltantes).exists():
            return False
    return True


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
    origenes = _origenes_de_caja()
    # La jornada operativa es la del registro vigente, incluso tras una edición
    # de fecha. El resumen y el cierre usan la misma contabilidad corregida.
    for modelo, campo_fecha, campo_valor, _, filtros in origenes.values():
        if modelo.objects.filter(tienda_id=tienda.pk, **{campo_fecha+'__gt': fecha}, **filtros).exclude(
                Q(**{campo_valor: 0}) | Q(**{campo_valor+'__isnull': True})).exists():
            return bloquear('movimientos_posteriores', 'No puedes cerrar esta fecha con el saldo actual: hay movimientos con fecha posterior al día seleccionado.')
    if not _asientos_conciliados(movimientos, corte, tienda.pk, origenes):
        return bloquear('historial_no_conciliado', 'No se pudo verificar el efecto de un registro, corrección o anulación en el historial de caja. Revisa los movimientos antes de cerrar.')
    if not primero or primero.creado_en >= corte:
        return bloquear('historial_insuficiente', 'No hay historial de caja suficiente para verificar el saldo de esta fecha. No se registró ningún cierre.')
    ultimo = movimientos.order_by('-creado_en', '-id').only('saldo_posterior').first()
    if Decimal(str(ultimo.saldo_posterior)) != Decimal(str(tienda.caja_inicial)):
        return bloquear('saldo_sin_respaldo', 'El saldo actual no coincide con el último movimiento auditado. Revisa la caja antes de registrar un cierre anterior.')
    return {**result, 'puede_registrar': True, 'codigo': 'sin_movimientos_posteriores',
        'mensaje': 'Puedes cerrar esta fecha: no hay movimientos que correspondan a días posteriores.',
        'saldo_a_registrar': str(tienda.caja_inicial)}
