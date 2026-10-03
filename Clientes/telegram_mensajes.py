"""Texto plano para evitar interpretar nombres como HTML o Markdown."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from Clientes.telegram_resumen import estado_credito_visible


def _dinero(valor):
    monto = Decimal(str(valor)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    texto = f'{monto:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    return '$' + (texto[:-3] if texto.endswith(',00') else texto)


def _numero(valor):
    numero = Decimal(str(valor)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    texto = f'{numero:.2f}'.replace('.', ',')
    return texto.rstrip('0').rstrip(',')


def _fecha(valor):
    try:
        return date.fromisoformat(str(valor)).strftime('%d/%m/%Y')
    except (TypeError, ValueError):
        return str(valor)


def _avance_cuotas(datos):
    try:
        total_cuotas = Decimal(str(datos['cuotas']))
        valor_cuota = Decimal(str(datos['valor_cuota']))
        total_abonado = Decimal(str(datos['total_abonado']))
        if total_cuotas <= 0 or valor_cuota <= 0:
            return None
        equivalentes = min(total_abonado / valor_cuota, total_cuotas)
        return f'{_numero(equivalentes)} de {_numero(total_cuotas)}'
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None


def mensaje_evento(evento):
    datos = evento.datos
    if datos.get('requiere_revision') or evento.estado in ('fallido', 'cancelado'):
        raise ValueError('El evento requiere revisión y no se puede enviar')
    titulos = {'credito': '🆕 Crédito registrado', 'abono': '✅ Abono registrado',
               'falla': '⚠️ Visita sin abono registrada', 'correccion': '📝 Registro corregido',
               'reversa': '↩️ Registro anulado', 'renovacion': '🔄 Crédito cerrado por renovación',
               'ajuste_credito': '🛠️ Fecha o cuotas del crédito corregidas',
               'edicion_credito': '📝 Datos del crédito corregidos',
               'eliminacion_credito': '🗑️ Registro de crédito eliminado'}
    titulo = titulos[evento.tipo]
    # El nombre de la ruta es información interna y nunca debe exponerse al cliente.
    lineas = [titulo, '', f"💳 Crédito #{datos['credito']}"]
    if datos.get('cliente_primer_nombre'):
        lineas.append(f"👤 Cliente: {datos['cliente_primer_nombre']}")
    lineas.append(f"📍 Estado: {estado_credito_visible(datos)}")
    if evento.tipo == 'eliminacion_credito':
        lineas.extend([f"📅 Fecha del crédito: {_fecha(datos['fecha'])}",
                       f"💵 Monto del registro eliminado: {_dinero(datos['monto'])}",
                       f"📌 Saldo anterior a la eliminación: {_dinero(datos['saldo'])}",
                       '',
                       'ℹ️ Este aviso informa la eliminación del registro; no es un comprobante de pago ni de condonación.',
                       'Si no reconoce esta operación, consulte con su administrador.'])
        return '\n'.join(lineas)
    if evento.tipo == 'ajuste_credito':
        lineas.extend([f"📅 Fecha: {_fecha(datos['fecha_anterior'])} → {_fecha(datos['fecha'])}",
                       f"🗓️ Cuotas: {datos['cuotas_anteriores']} → {datos['cuotas']}",
                       f"💵 Valor de cuota: {_dinero(datos['valor_cuota_anterior'])} → {_dinero(datos['valor_cuota'])}",
                       f"⏱️ Frecuencia: {datos['frecuencia']}"])
        if datos.get('vencimiento'):
            lineas.append(f"📅 Vencimiento actualizado: {_fecha(datos['vencimiento'])}")
        lineas.extend(['', 'ℹ️ Esta corrección no modifica el total a pagar ni los abonos registrados.'])
    elif evento.tipo in ('credito', 'edicion_credito'):
        if datos.get('origen_renovacion'):
            lineas.append(f"ℹ️ Renovación del crédito #{datos['origen_renovacion']}: capital trasladado, no dinero nuevo entregado.")
        lineas.extend([f"📅 Fecha: {_fecha(datos['fecha'])}",
                       f"💵 Monto del crédito: {_dinero(datos['monto'])}",
                       f"🧾 Total a pagar: {_dinero(datos['total_a_pagar'])}",
                       f"🗓️ Plazo: {datos['cuotas']} cuotas · {datos['frecuencia']}",
                       f"💵 Valor de cuota: {_dinero(datos['valor_cuota'])}"])
    else:
        etiqueta = 'Monto anulado' if evento.tipo == 'reversa' else 'Saldo trasladado' if evento.tipo == 'renovacion' else 'Monto del registro'
        detalle = [f"📅 Fecha del movimiento: {_fecha(datos['fecha_movimiento'])}"]
        if evento.tipo == 'abono':
            detalle.append(f"💵 Monto abonado: {_dinero(datos['monto_movimiento'])}")
        elif evento.tipo == 'falla':
            detalle.extend([f"🧾 Registro #{datos['registro']}",
                            '💵 Abono recibido: $0'])
        else:
            detalle.extend([f"🧾 Registro #{datos['registro']}",
                            f"💵 {etiqueta}: {_dinero(datos['monto_movimiento'])}"])
        lineas.extend(detalle)
    avance = _avance_cuotas(datos)
    lineas.extend([''])
    if datos.get('fecha_vencimiento'):
        lineas.append(f"📅 Vencimiento: {_fecha(datos['fecha_vencimiento'])}")
    if datos.get('cuotas_atrasadas') and Decimal(str(datos['cuotas_atrasadas'])) > 0:
        lineas.append(f"⏳ Cuotas atrasadas: {_numero(datos['cuotas_atrasadas'])}")
    if datos.get('fecha_ultimo_abono'):
        ultimo = f"📅 Último abono: {_fecha(datos['fecha_ultimo_abono'])}"
        if datos.get('monto_ultimo_abono') is not None:
            ultimo += f" · {_dinero(datos['monto_ultimo_abono'])}"
        lineas.append(ultimo)
    if datos.get('dias_sin_abono', 0) and int(datos['dias_sin_abono']) > 0:
        lineas.append(f"🕒 Días sin abono: {datos['dias_sin_abono']}")
    if avance:
        lineas.append(f"📊 Avance de cuotas: {avance}")
    lineas.extend([f"💰 Total abonado: {_dinero(datos['total_abonado'])}",
                   f"📌 Saldo pendiente: {_dinero(datos['saldo'])}",
                   f"📈 Progreso financiero: {_numero(datos['progreso'])}%"])
    if datos['cierre'] == 'pagado' and evento.tipo != 'renovacion':
        lineas.append('🎉 Crédito liquidado por pago.')
    if evento.tipo == 'renovacion':
        lineas.append('ℹ️ El saldo trasladado no corresponde a un abono recibido.')
    return '\n'.join(lineas)
