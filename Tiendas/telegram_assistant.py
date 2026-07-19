"""Asistente privado de cartera por Telegram.

El asistente es estrictamente de solo lectura. Telegram solo transporta la
pregunta: los importes, estados y permisos se calculan en Django. Nunca acepta
una ruta, usuario o saldo enviados por el chat como fuente de autoridad.
"""
import html
import logging
from datetime import date

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Sum

from Clientes.models import Cliente
from Clientes.views import _calcular_score
from Recaudos.models import Recaudo
from Tiendas.models import Tienda
from Ventas.models import Venta

logger = logging.getLogger(__name__)
API_BASE = 'https://api.telegram.org/bot{token}/{method}'
TIMEOUT = 12
MAX_RESULTADOS = 10


def _api(method):
    return API_BASE.format(token=settings.TELEGRAM_BOT_TOKEN, method=method)


def _configurado():
    return bool(
        settings.TELEGRAM_BOT_TOKEN
        and settings.TELEGRAM_ASSISTANT_CHAT_ID
        and settings.TELEGRAM_ASSISTANT_USERNAME
    )


def _chat_autorizado(chat):
    """El ID numérico de chat es la autorización; @username no lo es."""
    return (
        _configurado()
        and chat.get('type') == 'private'
        and str(chat.get('id', '')) == str(settings.TELEGRAM_ASSISTANT_CHAT_ID)
    )


def _dinero(valor):
    return f'${int(round(float(valor or 0))):,}'.replace(',', '.')


def _fecha(valor):
    return valor.strftime('%d/%m/%Y') if valor else '—'


def _escape(valor):
    return html.escape(str(valor or '—'))


def _enviar(chat_id, texto, teclado=None):
    payload = {
        'chat_id': chat_id,
        'text': texto,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    if teclado:
        payload['reply_markup'] = {'inline_keyboard': teclado}
    try:
        requests.post(_api('sendMessage'), json=payload, timeout=TIMEOUT)
    except Exception as exc:
        logger.warning('No se pudo enviar respuesta del asistente: %s', exc)


def _responder_callback(callback_id, texto='Listo'):
    try:
        requests.post(
            _api('answerCallbackQuery'),
            json={'callback_query_id': callback_id, 'text': texto},
            timeout=TIMEOUT,
        )
    except Exception as exc:
        logger.warning('No se pudo responder callback del asistente: %s', exc)


def _usuario_y_rutas():
    """Resuelve rutas vigentes asignadas en Tienda_Administrador.

    No se usa Tienda.administrador: ese campo conserva el dueño histórico de
    una ruta y puede incluir rutas que cavb1205 ya no administra. La tabla
    Tienda_Administrador es la asignación explícita que define el alcance del
    asistente.
    """
    usuario = User.objects.filter(username=settings.TELEGRAM_ASSISTANT_USERNAME).first()
    if not usuario:
        logger.error('Usuario del asistente Telegram no encontrado: %s', settings.TELEGRAM_ASSISTANT_USERNAME)
        return None, Tienda.objects.none()
    rutas = Tienda.objects.filter(
        tienda_administrador__administrador=usuario
    ).distinct()
    return usuario, rutas


def _botones_principales():
    return [
        [
            {'text': '📊 Resumen', 'callback_data': 'asst:resumen'},
            {'text': '🏪 Rutas', 'callback_data': 'asst:rutas'},
        ],
        [
            {'text': '🔴 Vencidos', 'callback_data': 'asst:vencidos'},
            {'text': '⚠️ Riesgo', 'callback_data': 'asst:riesgo'},
        ],
    ]


def _ventas_activas(rutas):
    return Venta.objects.filter(tienda__in=rutas).exclude(estado_venta__in=['Pagado', 'Perdida'])


def _texto_resumen(rutas):
    activas = _ventas_activas(rutas)
    vencidas = activas.filter(estado_venta='Vencido')
    atrasadas = activas.filter(estado_venta='Atrasado')
    recaudo_hoy = Recaudo.objects.filter(
        tienda__in=rutas,
        fecha_recaudo=date.today(),
        visita_blanco__isnull=True,
        es_renovacion=False,
    ).aggregate(total=Sum('valor_recaudo'))['total'] or 0
    por_cobrar = activas.aggregate(total=Sum('saldo_actual'))['total'] or 0
    vencido = vencidas.aggregate(total=Sum('saldo_actual'))['total'] or 0
    return (
        '📊 <b>Resumen de tu cartera</b>\n'
        f'👤 Administrador: <b>{_escape(settings.TELEGRAM_ASSISTANT_USERNAME)}</b>\n'
        f'🏪 Rutas administradas: <b>{rutas.count()}</b>\n\n'
        f'💰 Por cobrar: <b>{_dinero(por_cobrar)}</b>\n'
        f'✅ Recaudado hoy: <b>{_dinero(recaudo_hoy)}</b>\n'
        f'🔴 Vencidos: <b>{vencidas.count()}</b> · {_dinero(vencido)}\n'
        f'🟡 Atrasados: <b>{atrasadas.count()}</b>\n'
        f'📋 Créditos activos: <b>{activas.count()}</b>'
    )


def _texto_rutas(rutas):
    if not rutas.exists():
        return '🏪 No hay rutas asociadas a este administrador.'
    lineas = ['🏪 <b>Tus rutas administradas</b>']
    teclado = []
    for ruta in rutas.order_by('nombre')[:MAX_RESULTADOS]:
        ventas = _ventas_activas(rutas.filter(id=ruta.id))
        saldo = ventas.aggregate(total=Sum('saldo_actual'))['total'] or 0
        vencidos = ventas.filter(estado_venta='Vencido').count()
        aviso = f' · 🔴 {vencidos}' if vencidos else ''
        lineas.append(f'\n• <b>{_escape(ruta.nombre)}</b>\n  {_dinero(saldo)} por cobrar{aviso}')
        teclado.append([{'text': f'📍 {_escape(ruta.nombre)[:30]}', 'callback_data': f'asst:ruta:{ruta.id}'}])
    if rutas.count() > MAX_RESULTADOS:
        lineas.append(f'\n<i>Mostrando las primeras {MAX_RESULTADOS} rutas.</i>')
    return '\n'.join(lineas), teclado


def _texto_ruta(ruta, rutas):
    ventas = _ventas_activas(rutas.filter(id=ruta.id))
    vencidas = ventas.filter(estado_venta='Vencido')
    recaudo_hoy = Recaudo.objects.filter(
        tienda=ruta, fecha_recaudo=date.today(), visita_blanco__isnull=True, es_renovacion=False
    ).aggregate(total=Sum('valor_recaudo'))['total'] or 0
    saldo = ventas.aggregate(total=Sum('saldo_actual'))['total'] or 0
    vencido = vencidas.aggregate(total=Sum('saldo_actual'))['total'] or 0
    texto = (
        f'📍 <b>{_escape(ruta.nombre)}</b>\n\n'
        f'💰 Por cobrar: <b>{_dinero(saldo)}</b>\n'
        f'✅ Recaudado hoy: <b>{_dinero(recaudo_hoy)}</b>\n'
        f'🔴 Vencidos: <b>{vencidas.count()}</b> · {_dinero(vencido)}\n'
        f'🟡 Atrasados: <b>{ventas.filter(estado_venta="Atrasado").count()}</b>\n'
        f'📋 Créditos activos: <b>{ventas.count()}</b>'
    )
    teclado = [
        [
            {'text': '🔴 Ver vencidos', 'callback_data': f'asst:vencidos:{ruta.id}'},
            {'text': '⚠️ Riesgo', 'callback_data': f'asst:riesgo:{ruta.id}'},
        ],
        [{'text': '⬅️ Tus rutas', 'callback_data': 'asst:rutas'}],
    ]
    return texto, teclado


def _texto_vencidos(rutas):
    ventas = list(
        Venta.objects.filter(tienda__in=rutas, estado_venta='Vencido')
        .select_related('cliente', 'tienda')
        .order_by('-saldo_actual')[:MAX_RESULTADOS]
    )
    if not ventas:
        return '✅ <b>Sin créditos vencidos</b> en las rutas consultadas.', _botones_principales()
    total = sum(v.saldo_actual or 0 for v in ventas)
    lineas = [f'🔴 <b>Créditos vencidos</b>\nMostrando {len(ventas)} · {_dinero(total)}']
    teclado = []
    for venta in ventas:
        cliente = f'{venta.cliente.nombres} {venta.cliente.apellidos}'.strip()
        lineas.append(
            f'\n• <b>{_escape(cliente)}</b> — {_escape(venta.tienda.nombre)}\n'
            f'  Saldo: <b>{_dinero(venta.saldo_actual)}</b> · {venta.dias_sin_abono()} días sin abono'
        )
        teclado.append([{'text': f'👤 {_escape(cliente)[:28]}', 'callback_data': f'asst:cliente:{venta.cliente_id}'}])
    return '\n'.join(lineas), teclado


def _texto_cliente(cliente, rutas):
    if not rutas.filter(id=cliente.tienda_id).exists():
        return '⛔ Cliente fuera de tus rutas autorizadas.', _botones_principales()
    score = _calcular_score(cliente.id, cliente.tienda_id)
    activas = list(
        Venta.objects.filter(cliente=cliente).exclude(estado_venta__in=['Pagado', 'Perdida'])
        .order_by('-fecha_venta')
    )
    nombre = f'{cliente.nombres} {cliente.apellidos}'.strip()
    lineas = [
        f'👤 <b>{_escape(nombre)}</b>\n'
        f'🏪 {_escape(cliente.tienda.nombre)}\n\n'
        f'📈 Score: <b>{score["score"]}/100 · {score["nivel"]}</b>\n'
        f'💳 Cupo disponible: <b>{_dinero(score["cupo_disponible"])}</b>\n'
        f'💰 Saldo vigente: <b>{_dinero(score["saldo_vigente"])}</b>',
    ]
    if score['senales']:
        lineas.append('\n⚠️ <b>Señales de riesgo</b>\n' + '\n'.join(f'• {_escape(s)}' for s in score['senales']))
    if activas:
        lineas.append('\n📋 <b>Créditos en curso</b>')
        for venta in activas[:3]:
            lineas.append(
                f'• {venta.estado_venta}: {_dinero(venta.saldo_actual)} · '
                f'{venta.dias_sin_abono()} días sin abono'
            )
    else:
        lineas.append('\n✅ Sin créditos activos.')
    return '\n'.join(lineas), _botones_principales()


def _texto_riesgo(rutas):
    # Se parte de clientes con cartera aún abierta: así evitamos evaluar toda la
    # base histórica al responder desde un webhook.
    clientes = (
        Cliente.objects.filter(tienda__in=rutas, venta__estado_venta__in=['Vigente', 'Atrasado', 'Vencido'])
        .select_related('tienda').distinct()[:100]
    )
    evaluados = []
    for cliente in clientes:
        score = _calcular_score(cliente.id, cliente.tienda_id)
        if score['score'] < 60 or score['senales']:
            evaluados.append((score, cliente))
    evaluados.sort(key=lambda item: (item[0]['score'], -item[0]['saldo_vigente']))
    evaluados = evaluados[:MAX_RESULTADOS]
    if not evaluados:
        return '✅ <b>Sin alertas de riesgo activas</b> en las rutas consultadas.', _botones_principales()
    lineas = ['⚠️ <b>Clientes que requieren atención</b>']
    teclado = []
    for score, cliente in evaluados:
        nombre = f'{cliente.nombres} {cliente.apellidos}'.strip()
        senal = score['senales'][0] if score['senales'] else 'Score bajo'
        lineas.append(
            f'\n• <b>{_escape(nombre)}</b> — {score["score"]}/100 · {_escape(score["nivel"])}\n'
            f'  {_escape(senal)} · Saldo {_dinero(score["saldo_vigente"])}'
        )
        teclado.append([{'text': f'👤 {_escape(nombre)[:28]}', 'callback_data': f'asst:cliente:{cliente.id}'}])
    return '\n'.join(lineas), teclado


def _ayuda():
    return (
        '🤖 <b>Asistente privado de cartera</b>\n\n'
        'Consulto únicamente las rutas administradas por <b>cavb1205</b>.\n\n'
        '<b>Comandos</b>\n'
        '/resumen — estado global de tu cartera\n'
        '/rutas — tus rutas administradas\n'
        '/ruta Nombre — detalle de una ruta\n'
        '/vencidos [Ruta] — créditos vencidos\n'
        '/cliente Nombre — estado, score y cupo\n'
        '/riesgo [Ruta] — clientes con señales de deterioro\n'
        '/ayuda — este menú'
    )


def _buscar_ruta(argumento, rutas):
    termino = argumento.strip()
    if not termino:
        return None, []
    encontradas = list(rutas.filter(nombre__icontains=termino).order_by('nombre')[:5])
    return (encontradas[0] if len(encontradas) == 1 else None), encontradas


def _buscar_cliente(argumento, rutas):
    palabras = [p for p in argument.strip().split() if p]
    if not palabras:
        return []
    consulta = Q()
    for palabra in palabras:
        consulta &= Q(nombres__icontains=palabra) | Q(apellidos__icontains=palabra) | Q(identificacion__icontains=palabra)
    return list(Cliente.objects.filter(consulta, tienda__in=rutas).select_related('tienda').order_by('nombres')[:5])


def _seleccionar_ruta(encontradas, titulo):
    texto = f'🔎 <b>{titulo}</b>\nSelecciona una ruta:'
    teclado = [[{'text': f'📍 {_escape(r.nombre)[:30]}', 'callback_data': f'asst:ruta:{r.id}'}] for r in encontradas]
    return texto, teclado


def _procesar_comando(chat_id, texto):
    _, rutas = _usuario_y_rutas()
    if not rutas.exists():
        _enviar(chat_id, '⛔ No encontré rutas autorizadas para el asistente.'); return
    partes = texto.strip().split(maxsplit=1)
    comando = partes[0].lower().split('@')[0]
    argumento = partes[1] if len(partes) > 1 else ''
    if comando in ('/start', '/ayuda', '/help'):
        _enviar(chat_id, _ayuda(), _botones_principales())
    elif comando in ('/resumen', '/hoy'):
        _enviar(chat_id, _texto_resumen(rutas), _botones_principales())
    elif comando == '/rutas':
        texto_rutas, teclado = _texto_rutas(rutas)
        _enviar(chat_id, texto_rutas, teclado)
    elif comando in ('/ruta', '/vencidos', '/riesgo'):
        ruta, encontradas = _buscar_ruta(argumento, rutas)
        if argumento and not encontradas:
            _enviar(chat_id, '🔎 No encontré una ruta con ese nombre. Usa /rutas para ver las disponibles.'); return
        if argumento and not ruta:
            _enviar(chat_id, *_seleccionar_ruta(encontradas, 'Hay varias rutas')) ; return
        alcance = rutas.filter(id=ruta.id) if ruta else rutas
        if comando == '/ruta':
            if not ruta:
                _enviar(chat_id, 'Escribe /ruta seguido del nombre. Ejemplo: <code>/ruta Norte</code>'); return
            respuesta, teclado = _texto_ruta(ruta, rutas)
        elif comando == '/vencidos':
            respuesta, teclado = _texto_vencidos(alcance)
        else:
            respuesta, teclado = _texto_riesgo(alcance)
        _enviar(chat_id, respuesta, teclado)
    elif comando == '/cliente':
        encontrados = _buscar_cliente(argumento, rutas)
        if not encontrados:
            _enviar(chat_id, '🔎 No encontré ese cliente en tus rutas. Usa <code>/cliente Nombre Apellido</code>.'); return
        if len(encontrados) == 1:
            respuesta, teclado = _texto_cliente(encontrados[0], rutas)
        else:
            respuesta = '🔎 <b>Encontré varios clientes</b>. Selecciona uno:'
            teclado = [[{'text': f'👤 {_escape(c.nombres)} {_escape(c.apellidos)} · {_escape(c.tienda.nombre)[:15]}', 'callback_data': f'asst:cliente:{c.id}'}] for c in encontrados]
        _enviar(chat_id, respuesta, teclado)
    else:
        _enviar(chat_id, 'No reconozco ese comando. Usa /ayuda.', _botones_principales())


def procesar_mensaje(message):
    """Procesa mensajes de Telegram. Devuelve True si pertenecen al asistente."""
    chat = message.get('chat', {})
    if not _chat_autorizado(chat):
        return False
    texto = (message.get('text') or '').strip()
    if texto:
        _procesar_comando(chat['id'], texto)
    return True


def procesar_callback(callback):
    """Procesa botones asst:* únicamente desde el chat privado autorizado."""
    mensaje = callback.get('message', {})
    chat = mensaje.get('chat', {})
    data = callback.get('data', '')
    if not data.startswith('asst:') or not _chat_autorizado(chat):
        return False
    callback_id = callback.get('id')
    _, rutas = _usuario_y_rutas()
    if not rutas.exists():
        _responder_callback(callback_id, 'Sin rutas autorizadas'); return True
    partes = data.split(':')
    accion = partes[1] if len(partes) > 1 else ''
    ruta_id = partes[2] if len(partes) > 2 else None
    if accion == 'resumen':
        respuesta, teclado = _texto_resumen(rutas), _botones_principales()
    elif accion == 'rutas':
        respuesta, teclado = _texto_rutas(rutas)
    elif accion == 'ruta' and ruta_id:
        ruta = rutas.filter(id=ruta_id).first()
        if not ruta:
            _responder_callback(callback_id, 'Ruta no autorizada'); return True
        respuesta, teclado = _texto_ruta(ruta, rutas)
    elif accion == 'vencidos':
        alcance = rutas.filter(id=ruta_id) if ruta_id else rutas
        respuesta, teclado = _texto_vencidos(alcance)
    elif accion == 'riesgo':
        alcance = rutas.filter(id=ruta_id) if ruta_id else rutas
        respuesta, teclado = _texto_riesgo(alcance)
    elif accion == 'cliente' and ruta_id:
        cliente = Cliente.objects.select_related('tienda').filter(id=ruta_id, tienda__in=rutas).first()
        if not cliente:
            _responder_callback(callback_id, 'Cliente no autorizado'); return True
        respuesta, teclado = _texto_cliente(cliente, rutas)
    else:
        _responder_callback(callback_id, 'Acción inválida'); return True
    _responder_callback(callback_id)
    _enviar(chat['id'], respuesta, teclado)
    return True


def configurar_comandos():
    """Registra el menú de comandos oficial en Telegram. Se llama al desplegar."""
    if not _configurado():
        return False
    comandos = [
        {'command': 'resumen', 'description': 'Estado global de tu cartera'},
        {'command': 'rutas', 'description': 'Tus rutas administradas'},
        {'command': 'vencidos', 'description': 'Créditos vencidos'},
        {'command': 'riesgo', 'description': 'Clientes con señales de riesgo'},
        {'command': 'cliente', 'description': 'Consultar cliente por nombre'},
        {'command': 'ayuda', 'description': 'Ayuda del asistente'},
    ]
    try:
        respuesta = requests.post(_api('setMyCommands'), json={'commands': comandos}, timeout=TIMEOUT)
        return bool(respuesta.json().get('ok'))
    except Exception as exc:
        logger.warning('No se pudieron configurar comandos Telegram: %s', exc)
        return False
