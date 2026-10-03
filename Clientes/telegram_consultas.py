"""Consultas de solo lectura para el bot Telegram de clientes."""
from django.db.models import F

from Clientes.models import Cliente
from Clientes.telegram_models import TelegramVinculo
from Clientes.telegram_mensajes import _avance_cuotas, _dinero, _fecha, _numero
from Clientes.telegram_resumen import (
    ResumenInconsistente, estado_credito_visible, primer_nombre, resumen_credito,
)
from Clientes.telegram_vinculacion import _alcance, _privado
from Recaudos.models import Recaudo
from Ventas.models import Venta


PAGE_SIZE = 5


def _clientes_vinculados(chat_id):
    _privado(chat_id)
    vinculos = TelegramVinculo.objects.select_related(
        'cliente', 'cliente__tienda',
    ).filter(
        chat_id=chat_id, activo=True,
        consentimiento_en__isnull=False, confirmado_en__isnull=False,
    ).order_by('cliente_id')
    return [vinculo.cliente for vinculo in vinculos if _alcance(vinculo.cliente)]


def _ventas_activas(chat_id):
    clientes = _clientes_vinculados(chat_id)
    if not clientes:
        return []
    ventas = Venta.objects.select_related('cliente', 'tienda').filter(
        cliente_id__in=[cliente.pk for cliente in clientes],
        tienda_id=F('cliente__tienda_id'), saldo_actual__gt=0,
    ).exclude(estado_venta__in=('Pagado', 'Perdida')).order_by(
        'fecha_venta', 'pk',
    )
    resultados = []
    for venta in ventas:
        try:
            datos = resumen_credito(venta)
        except ResumenInconsistente:
            continue
        resultados.append(datos)
    return resultados


def _detalle_credito(datos):
    lineas = [
        f"💳 Crédito #{datos['credito']}",
        f"👤 Cliente: {datos['cliente_primer_nombre']}",
        f"📍 Estado: {estado_credito_visible(datos)}",
        f"📅 Inicio: {_fecha(datos['fecha'])}",
        f"💵 Monto solicitado: {_dinero(datos['monto'])}",
        f"🧾 Total a pagar: {_dinero(datos['total_a_pagar'])}",
        f"🗓️ Plazo: {datos['cuotas']} cuotas · {datos['frecuencia']}",
        f"💵 Valor de cuota: {_dinero(datos['valor_cuota'])}",
    ]
    avance = _avance_cuotas(datos)
    if avance:
        lineas.append(f"📊 Cuotas cubiertas: {avance}")
    if datos.get('fecha_vencimiento'):
        lineas.append(f"📅 Vencimiento: {_fecha(datos['fecha_vencimiento'])}")
    if datos.get('cuotas_atrasadas') and float(datos['cuotas_atrasadas']) > 0:
        lineas.append(f"⏳ Cuotas atrasadas: {_numero(datos['cuotas_atrasadas'])}")
    if datos.get('fecha_ultimo_abono'):
        ultimo = f"📅 Último abono: {_fecha(datos['fecha_ultimo_abono'])}"
        if datos.get('monto_ultimo_abono') is not None:
            ultimo += f" · {_dinero(datos['monto_ultimo_abono'])}"
        lineas.append(ultimo)
    if datos.get('dias_sin_abono', 0) and int(datos['dias_sin_abono']) > 0:
        lineas.append(f"🕒 Días sin abono: {datos['dias_sin_abono']}")
    lineas.extend([
        f"💰 Total abonado: {_dinero(datos['total_abonado'])}",
        f"📌 Saldo pendiente: {_dinero(datos['saldo'])}",
        f"📈 Progreso financiero: {_numero(datos['progreso'])}%",
    ])
    return '\n'.join(lineas)


def menu_principal(chat_id):
    clientes = _clientes_vinculados(chat_id)
    if not clientes:
        return (
            'No hay una vinculación activa para este chat. Solicita un enlace '
            'al administrador o trabajador de tu ruta.', []
        )
    return (
        '👋 Hola.\n\nSelecciona la información que deseas consultar:',
        [[{'text': '📚 Mis créditos activos', 'callback_data': 'tgmenu:creditos:0'}],
         [{'text': '🧾 Últimos movimientos', 'callback_data': 'tgmenu:movimientos'}]],
    )


def creditos_activos(chat_id, offset=0):
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    ventas = _ventas_activas(chat_id)
    if not ventas:
        return '📚 No tienes créditos activos disponibles para consulta.', [
            [{'text': '⬅️ Menú principal', 'callback_data': 'tgmenu:menu'}],
        ]
    pagina = ventas[offset:offset + PAGE_SIZE]
    lineas = ['📚 Mis créditos activos', '']
    for datos in pagina:
        lineas.extend([_detalle_credito(datos), ''])
    botones = [[{
        'text': f"📄 Crédito #{datos['credito']}",
        'callback_data': f"tgmenu:credito:{datos['credito']}",
    }] for datos in pagina]
    navegacion = []
    if offset > 0:
        navegacion.append({'text': '⬅️ Anteriores',
                           'callback_data': f'tgmenu:creditos:{max(0, offset - PAGE_SIZE)}'})
    if offset + PAGE_SIZE < len(ventas):
        navegacion.append({'text': 'Siguientes ➡️',
                           'callback_data': f'tgmenu:creditos:{offset + PAGE_SIZE}'})
    if navegacion:
        botones.append(navegacion)
    botones.append([{'text': '⬅️ Menú principal', 'callback_data': 'tgmenu:menu'}])
    return '\n'.join(lineas).rstrip(), botones


def detalle_credito(chat_id, credito_id):
    try:
        credito_id = int(credito_id)
    except (TypeError, ValueError):
        credito_id = 0
    datos = next((item for item in _ventas_activas(chat_id)
                  if item['credito'] == credito_id), None)
    if not datos:
        texto = 'Ese crédito no está disponible o ya no se encuentra activo.'
    else:
        texto = _detalle_credito(datos)
    return texto, [[{'text': '⬅️ Mis créditos', 'callback_data': 'tgmenu:creditos:0'},
                    {'text': '🏠 Menú', 'callback_data': 'tgmenu:menu'}]]


def movimientos_recientes(chat_id):
    clientes = _clientes_vinculados(chat_id)
    if not clientes:
        return menu_principal(chat_id)
    cliente_ids = [cliente.pk for cliente in clientes]
    recaudos = Recaudo.objects.select_related(
        'venta', 'venta__cliente', 'visita_blanco',
    ).filter(
        venta__cliente_id__in=cliente_ids,
        tienda_id=F('venta__tienda_id'),
        es_renovacion=False,
    ).order_by('-fecha_recaudo', '-pk')[:10]
    lineas = ['🧾 Últimos movimientos', '']
    for recaudo in recaudos:
        nombre = primer_nombre(recaudo.venta.cliente.nombres)
        if recaudo.valor_recaudo > 0:
            detalle = f"✅ Abono: {_dinero(recaudo.valor_recaudo)}"
        else:
            detalle = '⚠️ Visita sin abono'
        lineas.append(
            f"👤 {nombre} · 💳 Crédito #{recaudo.venta_id}\n"
            f"📅 {_fecha(recaudo.fecha_recaudo)} · {detalle}"
        )
        lineas.append('')
    if not recaudos:
        lineas.append('No hay movimientos recientes para tus créditos vinculados.')
    return '\n'.join(lineas).rstrip(), [
        [{'text': '📚 Mis créditos', 'callback_data': 'tgmenu:creditos:0'},
         {'text': '🏠 Menú', 'callback_data': 'tgmenu:menu'}],
    ]
