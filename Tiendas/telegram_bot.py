"""Integración con Telegram para notificar y aprobar solicitudes de pago.

Reemplaza el pipeline n8n + Gemini + WhatsApp Business API por un bot simple:
el admin recibe la foto del comprobante + datos y aprueba/rechaza con un toque.
"""
import io
import logging

import requests
from django.conf import settings
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
