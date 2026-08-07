"""Integración con Telegram para notificar y aprobar solicitudes de pago.

Reemplaza el pipeline n8n + Gemini + WhatsApp Business API por un bot simple:
el admin recibe la foto del comprobante + datos y aprueba/rechaza con un toque.
"""
import io
import html
import logging

import requests
from django.conf import settings
from django.utils import timezone
from PIL import Image

logger = logging.getLogger(__name__)

API_BASE = 'https://api.telegram.org/bot{token}/{method}'
TIMEOUT = 15
MAX_DIMENSION = 1600
JPEG_QUALITY = 80


def _api(method):
    return API_BASE.format(token=settings.TELEGRAM_BOT_TOKEN, method=method)


def _configurado():
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_ADMIN_CHAT_ID)


def _dinero(valor):
    return f'${int(round(float(valor or 0))):,}'.replace(',', '.')


def comprimir_imagen(archivo):
    """Redimensiona a máx 1600px y recomprime como JPEG ~80%.
    Recibe un UploadedFile, devuelve (bytes, nombre) o (None, None) si falla."""
    try:
        img = Image.open(archivo)
        if img.mode in ('RGBA', 'P', 'LA'):
            img = img.convert('RGB')
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=JPEG_QUALITY, optimize=True)
        buffer.seek(0)
        return buffer.getvalue(), 'comprobante.jpg'
    except Exception as exc:
        logger.warning('No se pudo comprimir la imagen: %s', exc)
        return None, None


def _teclado(codigo):
    return {
        'inline_keyboard': [[
            {'text': '✅ Confirmar pago', 'callback_data': f'confirmar:{codigo}'},
            {'text': '❌ Rechazar', 'callback_data': f'rechazar:{codigo}'},
        ]]
    }


def _caption(solicitud):
    quien = solicitud.solicitada_por
    nombre_quien = quien.get_full_name() or quien.username if quien else '—'
    return (
        '🆕 <b>Nueva solicitud de pago</b>\n\n'
        f'🏪 Tienda: <b>{solicitud.tienda.nombre}</b> (#{solicitud.tienda_id})\n'
        f'👤 Solicita: {nombre_quien}\n'
        f'📋 Plan: <b>{solicitud.membresia.nombre}</b> — ${solicitud.membresia.precio:,.0f}\n'
        f'🔢 Código: <code>{solicitud.codigo}</code>\n'
        f'📅 Pre-activada (acceso inmediato 3 días)\n'
        f'🕐 Solicitada: {solicitud.creada:%d/%m/%Y %H:%M}'
    )


def notificar_solicitud(solicitud, comprobante_bytes=None):
    """Envía la solicitud al chat del admin. Devuelve el message_id o '' si falla."""
    if not _configurado():
        logger.info('Telegram no configurado — se omite notificación de %s', solicitud.codigo)
        return ''

    caption = _caption(solicitud)
    teclado = _teclado(solicitud.codigo)
    try:
        if comprobante_bytes:
            resp = requests.post(
                _api('sendPhoto'),
                data={
                    'chat_id': settings.TELEGRAM_ADMIN_CHAT_ID,
                    'caption': caption,
                    'parse_mode': 'HTML',
                    'reply_markup': requests.compat.json.dumps(teclado),
                },
                files={'photo': ('comprobante.jpg', comprobante_bytes, 'image/jpeg')},
                timeout=TIMEOUT,
            )
        else:
            resp = requests.post(
                _api('sendMessage'),
                json={
                    'chat_id': settings.TELEGRAM_ADMIN_CHAT_ID,
                    'text': caption + '\n\n⚠️ Sin comprobante adjunto',
                    'parse_mode': 'HTML',
                    'reply_markup': teclado,
                },
                timeout=TIMEOUT,
            )
        data = resp.json()
        if data.get('ok'):
            return str(data['result']['message_id'])
        logger.error('Telegram rechazó la notificación: %s', data)
    except Exception as exc:
        logger.error('Error enviando notificación a Telegram: %s', exc)
    return ''


def marcar_procesada(solicitud, accion, admin_nombre):
    """Edita el mensaje original para reflejar que ya fue procesado y quita los botones."""
    if not _configurado() or not solicitud.telegram_message_id:
        return

    if accion == 'confirmar':
        extra = (
            f'\n\n✅ <b>Confirmado</b> por {admin_nombre}\n'
            f'Membresía activa hasta {solicitud.tienda.tienda_membresia.fecha_vencimiento:%d/%m/%Y}'
            if hasattr(solicitud.tienda, 'tienda_membresia') else
            f'\n\n✅ <b>Confirmado</b> por {admin_nombre}'
        )
    else:
        extra = f'\n\n❌ <b>Rechazado</b> por {admin_nombre}'

    nuevo_texto = _caption(solicitud) + extra
    metodo = 'editMessageCaption' if solicitud.comprobante else 'editMessageText'
    payload = {
        'chat_id': settings.TELEGRAM_ADMIN_CHAT_ID,
        'message_id': solicitud.telegram_message_id,
        'parse_mode': 'HTML',
        'reply_markup': {'inline_keyboard': []},
    }
    payload['caption' if solicitud.comprobante else 'text'] = nuevo_texto
    try:
        requests.post(_api(metodo), json=payload, timeout=TIMEOUT)
    except Exception as exc:
        logger.warning('No se pudo editar el mensaje de Telegram: %s', exc)


def _enviar_alerta(texto, teclado=None):
    """Envía un mensaje de texto simple (sin botones) al chat del admin.
    Nunca lanza excepción: las alertas son informativas y no deben romper el
    flujo que las dispara (registro, creación de ruta, etc.)."""
    if not _configurado():
        logger.info('Telegram no configurado — se omite alerta')
        return ''
    try:
        payload = {
            'chat_id': settings.TELEGRAM_ADMIN_CHAT_ID,
            'text': texto,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        }
        if teclado:
            payload['reply_markup'] = teclado
        resp = requests.post(
            _api('sendMessage'),
            json=payload,
            timeout=TIMEOUT,
        )
        data = resp.json()
        if data.get('ok'):
            return str(data['result']['message_id'])
        logger.error('Telegram rechazó la alerta: %s', data)
    except Exception as exc:
        logger.error('Error enviando alerta a Telegram: %s', exc)
    return ''


def _teclado_alerta(alerta_id):
    return {
        'inline_keyboard': [[
            {
                'text': '✅ Marcar revisada',
                'callback_data': f'alerta:revisar:{alerta_id}',
            },
            {
                'text': '✔ Resolver',
                'callback_data': f'alerta:resolver:{alerta_id}',
            },
        ]]
    }


def notificar_alerta_operativa(texto, alerta_id=None):
    """Publica una alerta operativa con acciones rápidas en Telegram."""
    return _enviar_alerta(
        texto,
        teclado=_teclado_alerta(alerta_id) if alerta_id else None,
    )


def notificar_correccion_venta(venta, usuario, ajuste):
    """Avisa al chat administrativo cuando se corrige un crédito con pagos."""
    nombre_usuario = usuario.get_full_name() or usuario.username
    cliente = venta.cliente.nombres
    if getattr(venta.cliente, 'apellidos', ''):
        cliente = f'{cliente} {venta.cliente.apellidos}'
    texto = (
        '🛠️ <b>Corrección administrativa de crédito</b>\n\n'
        f'🏪 Ruta: <b>{html.escape(str(venta.tienda.nombre))}</b> (#{venta.tienda_id})\n'
        f'👤 Cliente: <b>{html.escape(str(cliente))}</b>\n'
        f'📋 Venta: <b>#{venta.id}</b>\n'
        f'📅 Fecha: {ajuste.fecha_venta_anterior:%d/%m/%Y} → '
        f'{ajuste.fecha_venta_nueva:%d/%m/%Y}\n'
        f'🔢 Cuotas: {ajuste.cuotas_anteriores} → {ajuste.cuotas_nuevas}\n'
        f'📝 Motivo: {html.escape(ajuste.motivo)}\n'
        f'👮 Administrador: {html.escape(nombre_usuario)}'
    )
    return _enviar_alerta(texto)


def _editar_alerta_operativa(alerta, callback, nuevo_estado, admin_nombre):
    """Actualiza el mensaje original y elimina sus botones."""
    if not _configurado():
        return
    etiqueta = 'revisada' if nuevo_estado == 'revisada' else 'resuelta'
    texto = (
        f'{alerta.detalle}\n\n'
        f'✅ <b>Alerta {etiqueta}</b> por {html.escape(admin_nombre)}'
    )
    mensaje = callback.get('message', {})
    payload = {
        'chat_id': settings.TELEGRAM_ADMIN_CHAT_ID,
        'message_id': mensaje.get('message_id'),
        'text': texto,
        'parse_mode': 'HTML',
        'reply_markup': {'inline_keyboard': []},
    }
    try:
        requests.post(_api('editMessageText'), json=payload, timeout=TIMEOUT)
    except Exception as exc:
        logger.warning('No se pudo actualizar la alerta en Telegram: %s', exc)


def procesar_callback_alerta(callback):
    """Procesa botones de alertas desde el chat administrativo autorizado."""
    data = callback.get('data', '')
    if not data.startswith('alerta:'):
        return False
    chat_id = str(callback.get('message', {}).get('chat', {}).get('id', ''))
    if chat_id != str(settings.TELEGRAM_ADMIN_CHAT_ID):
        return False
    try:
        _, accion, alerta_id = data.split(':', 2)
        alerta_id = int(alerta_id)
    except (TypeError, ValueError):
        responder_callback(callback.get('id'), 'Acción inválida')
        return True

    from Tiendas.models import AlertaOperativa

    alerta = AlertaOperativa.objects.filter(pk=alerta_id).first()
    if not alerta:
        responder_callback(callback.get('id'), 'Alerta no encontrada')
        return True
    if accion not in ('revisar', 'resolver'):
        responder_callback(callback.get('id'), 'Acción inválida')
        return True

    nuevo_estado = 'revisada' if accion == 'revisar' else 'resuelta'
    if alerta.estado == 'resuelta' and nuevo_estado == 'revisada':
        responder_callback(callback.get('id'), 'La alerta ya está resuelta')
        return True
    alerta.estado = nuevo_estado
    alerta.save(update_fields=['estado', 'actualizada'])
    admin_tg = callback.get('from', {})
    admin_nombre = (
        f"{admin_tg.get('first_name', '')} {admin_tg.get('last_name', '')}".strip()
        or admin_tg.get('username', 'admin')
    )
    _editar_alerta_operativa(alerta, callback, nuevo_estado, admin_nombre)
    responder_callback(callback.get('id'), f'✅ Alerta {nuevo_estado}')
    return True


def notificar_resumen_operativo(rutas, fecha):
    """Envía el resumen separado de cartera de la jornada cerrada."""
    if not rutas:
        return _enviar_alerta(
            f'📊 <b>Resumen operativo</b> · {fecha:%d/%m/%Y}\n\n'
            'No hubo actividad operativa para resumir.'
        )

    lineas = [
        f'📊 <b>Resumen de cartera</b> · {fecha:%d/%m/%Y}\n',
        'Jornada cerrada · excepciones que requieren revisión.\n',
    ]
    for ruta in rutas[:35]:
        excepciones = (
            not ruta['cierre']
            or ruta['pendiente_recaudo'] > 0
            or ruta['sin_gestion'] > 0
            or ruta['fallas'] > 0
            or ruta['alertas_urgentes'] > 0
            or ruta['alertas_criticas'] > 0
        )
        estado = '⚠️' if excepciones else '✅'
        programacion = (
            f'{_dinero(ruta["recaudo"])} / {_dinero(ruta["esperado"])}'
            if ruta['programadas'] else 'sin cuotas programadas'
        )
        riesgos = (
            f'{ruta["alertas_urgentes"]} urgentes · '
            f'{ruta["alertas_criticas"]} críticas'
            if ruta['alertas_urgentes'] or ruta['alertas_criticas']
            else 'sin riesgos urgentes'
        )
        cierre = 'cierre OK' if ruta['cierre'] else '⚠️ cierre ausente'
        lineas.append(
            f'{estado} <b>{html.escape(str(ruta["nombre"]))}</b>\n'
            f'  💰 Cobro programado: <b>{programacion}</b> · '
            f'pendiente {_dinero(ruta["pendiente_recaudo"])}\n'
            f'  👥 Sin gestión: <b>{ruta["sin_gestion"]}</b> · '
            f'No pago: <b>{ruta["fallas"]}</b> · {cierre}\n'
            f'  ⚠️ Riesgo: <b>{riesgos}</b> · '
            f'Umbrales nuevos: <b>{ruta["alertas_nuevas"]}</b>'
        )
        for riesgo in ruta.get('riesgos_detalle', []):
            severidad = '🔴' if riesgo['severidad'] == 'critica' else '🟠'
            lineas.append(
                f'  {severidad} {html.escape(str(riesgo["cliente"]))} · '
                f'venta #{riesgo["venta_id"]} · saldo {_dinero(riesgo["saldo"])}'
            )
    if len(rutas) > 35:
        lineas.append(f'\n<i>Se muestran 35 de {len(rutas)} rutas con actividad.</i>')
    return _enviar_alerta('\n\n'.join(lineas))


def notificar_nuevo_usuario(user, tienda, telefono=''):
    """Avisa al admin que se registró un usuario nuevo en la app."""
    nombre = user.get_full_name() or user.username
    ahora = timezone.localtime(timezone.now())
    texto = (
        '👋 <b>Nuevo registro en la app</b>\n\n'
        f'👤 Usuario: <b>{nombre}</b> (@{user.username})\n'
        f'🏪 Ruta inicial: <b>{tienda.nombre}</b> (#{tienda.id})\n'
        f'📞 Teléfono: {telefono or "—"}\n'
        f'🕐 {ahora:%d/%m/%Y %H:%M}'
    )
    return _enviar_alerta(texto)


def notificar_nueva_ruta(tienda, user, total_rutas=None):
    """Avisa al admin que un usuario existente creó una ruta adicional."""
    nombre = user.get_full_name() or user.username
    ahora = timezone.localtime(timezone.now())
    extra_total = f'\n📊 Rutas totales del usuario: {total_rutas}' if total_rutas else ''
    texto = (
        '🆕 <b>Nueva ruta creada</b>\n\n'
        f'👤 Usuario: <b>{nombre}</b> (@{user.username})\n'
        f'🏪 Ruta: <b>{tienda.nombre}</b> (#{tienda.id})'
        f'{extra_total}\n'
        f'💳 Nace en <b>Pendiente Pago</b> (sin trial)\n'
        f'🕐 {ahora:%d/%m/%Y %H:%M}'
    )
    return _enviar_alerta(texto)


def responder_callback(callback_id, texto):
    """Muestra el toast de confirmación al admin tras tocar un botón."""
    if not _configurado():
        return
    try:
        requests.post(
            _api('answerCallbackQuery'),
            json={'callback_query_id': callback_id, 'text': texto},
            timeout=TIMEOUT,
        )
    except Exception as exc:
        logger.warning('No se pudo responder el callback de Telegram: %s', exc)


def _linea_admin(user, telefono=''):
    nombre = user.get_full_name() or user.username if user else '—'
    usuario = f' (@{user.username})' if user else ''
    tel = f' · 📞 {telefono}' if telefono else ''
    return f'👤 Admin: <b>{nombre}</b>{usuario}{tel}'


def notificar_bloqueo_impago(tienda, admin, fecha_vencimiento, telefono=''):
    """Avisa que una ruta pasó a Vencida (bloqueada) por falta de pago."""
    texto = (
        '🔒 <b>Ruta bloqueada por impago</b>\n\n'
        f'🏪 Ruta: <b>{tienda.nombre}</b> (#{tienda.id})\n'
        f'{_linea_admin(admin, telefono)}\n'
        f'📅 Venció: {fecha_vencimiento:%d/%m/%Y}\n'
        '💡 Momento de contactar para retención.'
    )
    return _enviar_alerta(texto)


def notificar_trial_sin_convertir(tienda, admin, fecha_vencimiento, telefono=''):
    """Avisa que el trial de 7 días de una ruta expiró sin que pagaran."""
    texto = (
        '⌛ <b>Trial terminado sin convertir</b>\n\n'
        f'🏪 Ruta: <b>{tienda.nombre}</b> (#{tienda.id})\n'
        f'{_linea_admin(admin, telefono)}\n'
        f'📅 Trial venció: {fecha_vencimiento:%d/%m/%Y}\n'
        '💡 Oportunidad de seguimiento comercial.'
    )
    return _enviar_alerta(texto)


def notificar_resumen_diario(s, fecha=None):
    """Envía el resumen separado de membresías con fecha explícita."""
    fecha = fecha or timezone.localdate()
    texto = (
        f'📊 <b>Resumen de membresías</b> · corte {timezone.localdate():%d/%m/%Y}\n\n'
        f'🆕 <b>Actividad del {fecha:%d/%m/%Y}</b>\n'
        f'  • Usuarios nuevos: <b>{s["nuevos_usuarios"]}</b>\n'
        f'  • Rutas nuevas: <b>{s["nuevas_rutas"]}</b>\n\n'
        '📈 <b>Estado actual de membresías</b>\n'
        f'  • Rutas activas: <b>{s["rutas_activas"]}</b>\n'
        f'  • Por vencer (≤3 días): <b>{s["por_vencer"]}</b>\n'
        f'  • Bloqueadas en esta revisión: <b>{s["bloqueadas"]}</b>\n'
        f'  • Trials sin convertir: <b>{s["trials_perdidos"]}</b>\n\n'
        f'💰 <b>Ingresos del {fecha:%d/%m/%Y}</b>\n'
        f'  • Día: <b>${s["ingreso_ayer"]:,.0f}</b>\n'
        f'  • Últimos 7 días: <b>${s["ingreso_semana"]:,.0f}</b>'
    )
    return _enviar_alerta(texto)
