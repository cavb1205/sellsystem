"""Cierres de caja del día actual o de una jornada anterior verificada.

Un administrador puede cerrar una fecha anterior aunque la ruta ya tenga
movimientos de jornadas posteriores (por ejemplo, cobros de hoy). El saldo de
esa fecha se reconstruye como ``caja actual - efecto vigente de los registros
con fecha posterior``, y solo se acepta si la bitácora de caja respalda cada
uno de esos registros y cada cambio hecho después del corte.

La escritura llama a esta validación con la fila de Tienda bloqueada, igual
que las operaciones que cambian caja. La consulta previa nunca autoriza por sí
sola el cierre: el POST vuelve a validar y recalcular bajo el bloqueo.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
from django.db.models import Q, Sum
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


def _filtros_vigentes(tipo, filtros, tienda_id):
    # Una visita sin abono vale cero y ya queda fuera por importe. Si después
    # se edita a un abono real, ese dinero sí entró a la caja y debe contarse.
    if tipo == 'RECAUDO':
        return {**filtros, 'venta__tienda_id': tienda_id}
    return filtros


def _registros_posteriores(tienda_id, fecha, origenes):
    """Registros vigentes que pertenecen a jornadas posteriores a ``fecha``.

    Devuelve ``({tipo: ids}, efecto)`` donde ``efecto`` es el cambio de caja
    que esos registros representan hoy. Se restará del saldo actual.
    """
    ids_por_tipo, efecto = {}, Decimal('0')
    for tipo, (modelo, campo_fecha, campo_valor, signo, filtros) in origenes.items():
        filas = modelo.objects.filter(
            tienda_id=tienda_id, **{campo_fecha + '__gt': fecha},
            **_filtros_vigentes(tipo, filtros, tienda_id),
        ).exclude(Q(**{campo_valor: 0}) | Q(**{campo_valor + '__isnull': True})
                  ).values_list('pk', campo_valor)
        for pk, valor in filas.iterator():
            ids_por_tipo.setdefault(tipo, set()).add(pk)
            efecto += signo * Decimal(str(valor))
    return ids_por_tipo, efecto


def _asientos_conciliados(movimientos, corte, tienda_id, origenes, adicionales=None):
    """Verifica el efecto vigente de cada operación modificada tras el corte.

    ``adicionales`` agrega los registros de jornadas posteriores, aunque se
    hayan creado antes del corte: su efecto se resta del saldo actual, así que
    también deben tener un historial de caja que lo respalde exactamente.

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
    for tipo, ids in (adicionales or {}).items():
        origenes_por_tipo.setdefault(tipo, set()).update(ids)

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
            elif accion == 'CORRECCION' and origen_id not in saldos:
                # Registro que nació en cero (visita sin abono editada a un
                # abono): no tiene alta porque no movió caja. Solo se acepta si
                # sus correcciones suman exactamente el importe vigente (o su
                # reversa completa), lo que prueba que partió de cero.
                saldos[origen_id] = delta
                if signo * delta < 0:
                    return False
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
        vigentes = modelo.objects.filter(pk__in=saldos, tienda_id=tienda_id,
                                         **_filtros_vigentes(tipo, filtros, tienda_id))
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


def verificar_cierres(tienda, cierres):
    """Compara cada cierre guardado con el saldo que hoy corresponde a su día.

    Usa la misma contabilidad que el cierre de fechas anteriores: caja actual
    menos el efecto de lo registrado para días posteriores. Un cierre queda
    ``desactualizado`` cuando, después de registrarlo, hubo movimientos o
    correcciones de ese mismo día (por ejemplo, se cerró a media jornada o se
    ingresó un abono tarde). Es solo informativo: no modifica ningún dato.

    Fechas sin historial de caja que las cubra, o una caja que no coincide con
    la bitácora, quedan como ``sin_verificacion``.
    """
    cierres = list(cierres)
    sin_dato = {'estado': 'sin_verificacion', 'saldo_del_dia': None, 'diferencia': None}
    resultado = {c.pk: dict(sin_dato) for c in cierres}
    if not cierres:
        return resultado
    movimientos = MovimientoCaja.objects.filter(tienda_id=tienda.pk)
    primero = movimientos.order_by('creado_en', 'id').only('creado_en').first()
    ultimo = movimientos.order_by('-creado_en', '-id').only('saldo_posterior').first()
    caja = Decimal(str(tienda.caja_inicial or 0))
    if not primero or Decimal(str(ultimo.saldo_posterior)) != caja:
        return resultado
    desde = fecha_operativa(tienda, primero.creado_en)
    verificables = [c for c in cierres if c.fecha_cierre >= desde]
    if not verificables:
        return resultado
    efectos = {}
    minimo = min(c.fecha_cierre for c in verificables)
    for tipo, (modelo, campo_fecha, campo_valor, signo, filtros) in _origenes_de_caja().items():
        filas = modelo.objects.filter(
            tienda_id=tienda.pk, **{campo_fecha + '__gt': minimo},
            **_filtros_vigentes(tipo, filtros, tienda.pk),
        ).order_by().values(campo_fecha).annotate(total=Sum(campo_valor)).values_list(campo_fecha, 'total')
        for dia, total in filas:
            efectos[dia] = efectos.get(dia, Decimal('0')) + signo * Decimal(str(total or 0))
    dias = sorted(efectos, reverse=True)
    for cierre in verificables:
        posterior = sum((efectos[d] for d in dias if d > cierre.fecha_cierre), Decimal('0'))
        saldo = caja - posterior
        diferencia = saldo - Decimal(str(cierre.valor))
        # Cierre_Caja guarda pesos sin centavos; menos de 1 es redondeo.
        resultado[cierre.pk] = {
            'estado': 'coincide' if abs(diferencia) < 1 else 'desactualizado',
            'saldo_del_dia': str(saldo), 'diferencia': str(diferencia),
        }
    return resultado


def validar_cierre(tienda, fecha, usuario):
    hoy = fecha_operativa(tienda)
    result = {'fecha': fecha.isoformat(), 'tienda_id': tienda.pk,
        'hoy': hoy.isoformat(), 'puede_registrar': False, 'codigo': '',
        'mensaje': '', 'saldo_a_registrar': None, 'cierre_existente': None,
        'historial_desde': None, 'saldo_actual': str(tienda.caja_inicial),
        'efecto_posterior': None}

    def bloquear(codigo, mensaje):
        return {**result, 'codigo': codigo, 'mensaje': mensaje}

    if fecha > hoy:
        return bloquear('fecha_futura', 'No puedes registrar un cierre de una fecha futura.')
    if fecha != hoy and not usuario_es_administrador_tienda(usuario, tienda.pk):
        return bloquear('solo_hoy_trabajador', 'El trabajador solo puede cerrar el día actual de la ruta.')
    existente = Cierre_Caja.objects.filter(tienda_id=tienda.pk, fecha_cierre=fecha).order_by('-id').first()
    if existente:
        result['cierre_existente'] = {'id': existente.pk, 'fecha_cierre': fecha.isoformat(), 'valor': str(existente.valor),
                                      'verificacion': verificar_cierres(tienda, [existente])[existente.pk]}
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
    # de fecha. El resumen y el cierre usan la misma contabilidad corregida:
    # lo registrado tarde para esta fecha cuenta en ella, y lo que pertenece a
    # jornadas posteriores (aunque se haya hecho hoy) se descuenta del saldo.
    posteriores, efecto = _registros_posteriores(tienda.pk, fecha, origenes)
    if not _asientos_conciliados(movimientos, corte, tienda.pk, origenes, posteriores):
        return bloquear('historial_no_conciliado', 'No se pudo verificar el efecto de un registro, corrección o anulación en el historial de caja. Revisa los movimientos antes de cerrar.')
    if not primero or primero.creado_en >= corte:
        return bloquear('historial_insuficiente', 'No hay historial de caja suficiente para verificar el saldo de esta fecha. No se registró ningún cierre.')
    ultimo = movimientos.order_by('-creado_en', '-id').only('saldo_posterior').first()
    if Decimal(str(ultimo.saldo_posterior)) != Decimal(str(tienda.caja_inicial)):
        return bloquear('saldo_sin_respaldo', 'El saldo actual no coincide con el último movimiento auditado. Revisa la caja antes de registrar un cierre anterior.')
    saldo = Decimal(str(tienda.caja_inicial)) - efecto
    if not efecto and not posteriores:
        return {**result, 'puede_registrar': True, 'codigo': 'sin_movimientos_posteriores',
            'mensaje': 'Puedes cerrar esta fecha: no hay movimientos que correspondan a días posteriores.',
            'saldo_a_registrar': str(saldo), 'efecto_posterior': '0'}
    return {**result, 'puede_registrar': True, 'codigo': 'saldo_reconstruido',
        'mensaje': 'Puedes cerrar esta fecha: el saldo se calculó descontando los movimientos de días posteriores.',
        'saldo_a_registrar': str(saldo), 'efecto_posterior': str(efecto)}
