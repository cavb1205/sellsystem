"""Webhook aislado para el bot de clientes.

No comparte el webhook del bot administrativo. El token solo se acepta en
``/start`` y callback_data, nunca se muestra ni se usa para consultar dinero.
"""
import hmac
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from Clientes.telegram_transporte import (
    enviar_consentimiento,
    enviar_menu,
    enviar_texto,
    responder_callback,
)
from Clientes.telegram_consultas import (
    creditos_activos, detalle_credito, menu_principal, movimientos_recientes,
)
from Clientes.telegram_vinculacion import aceptar_invitacion, invitacion_pendiente
from Clientes.telegram_limpieza import registrar_entrada


TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
ACCEPT_RE = re.compile(r'^tgcliente:(aceptar|rechazar):([A-Za-z0-9_-]{43})$')
START_RE = re.compile(r'^/start(?:@[A-Za-z0-9_]+)?(?:\s+([A-Za-z0-9_-]{43}))?$')
COMMAND_RE = re.compile(r'^/(?:menu|creditos|movimientos)(?:@[A-Za-z0-9_]+)?$')
MENU_CALLBACK_RE = re.compile(r'^tgmenu:(menu|creditos|movimientos)(?::(\d+))?$|^tgmenu:credito:(\d+)$')


def _habilitado():
    return (
        getattr(settings, 'TELEGRAM_CLIENTES_ENABLED', False) is True
        and getattr(settings, 'TELEGRAM_CLIENTES_DELIVERY_ENABLED', False) is True
    )


def _chat_privado(update):
    message = update.get('message') or update.get('callback_query', {}).get('message') or {}
    chat = message.get('chat') if isinstance(message, dict) else None
    chat_id = chat.get('id') if isinstance(chat, dict) else None
    if (not isinstance(chat, dict) or chat.get('type') != 'private'
            or type(chat_id) is not int or chat_id <= 0):
        return None
    return chat_id


def _mensaje(chat_id, texto):
    resultado = enviar_texto(chat_id, texto)
    # El webhook siempre devuelve 200 para que Telegram no reenvíe el update;
    # el transporte registra el resultado solo cuando hay un flujo de entrega.
    return resultado


def _consulta(chat_id, accion, valor=None):
    if accion == 'menu':
        texto, botones = menu_principal(chat_id)
    elif accion == 'creditos':
        texto, botones = creditos_activos(chat_id, valor or 0)
    elif accion == 'movimientos':
        texto, botones = movimientos_recientes(chat_id)
    else:
        texto, botones = detalle_credito(chat_id, valor)
    return enviar_menu(chat_id, texto, botones)


@api_view(['POST'])
@permission_classes([AllowAny])
def webhook(request):
    esperado = getattr(settings, 'TELEGRAM_CLIENTES_WEBHOOK_SECRET', '')
    recibido = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not esperado or not hmac.compare_digest(str(recibido), str(esperado)):
        return Response({'error': 'forbidden'}, status=403)
    if not _habilitado():
        return Response({'ok': True, 'procesado': False})
    update = request.data if isinstance(request.data, dict) else {}
    chat_id = _chat_privado(update)
    if chat_id is None:
        return Response({'ok': True, 'procesado': False})

    message = update.get('message')
    if isinstance(message, dict):
        try:
            registrar_entrada(chat_id, message.get('message_id'))
        except DatabaseError:
            # Telegram puede reenviar el update; el registro es idempotente.
            return Response({'ok': False}, status=503)

    callback = update.get('callback_query')
    if isinstance(callback, dict):
        data = callback.get('data', '')
        match = ACCEPT_RE.fullmatch(data) if isinstance(data, str) else None
        if match:
            accion, token = match.groups()
            responder_callback(callback.get('id'), 'Procesando solicitud')
            if accion == 'rechazar':
                _mensaje(chat_id, 'No se vinculó este chat. Si fue un error, solicita una nueva invitación.')
                return Response({'ok': True, 'procesado': True})
            try:
                aceptar_invitacion(token, chat_id, consentimiento=True)
            except (ValidationError, ValueError):
                _mensaje(chat_id, 'La invitación ya venció o no está disponible. Solicita una nueva.')
                return Response({'ok': True, 'procesado': True})
            _mensaje(chat_id, '✅ ¡Chat vinculado correctamente! Desde ahora recibirás aquí los avisos y podrás consultar tus créditos con /menu.')
            return Response({'ok': True, 'procesado': True})
        menu_match = MENU_CALLBACK_RE.fullmatch(data) if isinstance(data, str) else None
        if not menu_match:
            return Response({'ok': True, 'procesado': False})
        responder_callback(callback.get('id'), 'Actualizando información')
        grupos = menu_match.groups()
        if grupos[0] == 'menu':
            _consulta(chat_id, 'menu')
        elif grupos[0] == 'creditos':
            _consulta(chat_id, 'creditos', grupos[1] or 0)
        elif grupos[0] == 'movimientos':
            _consulta(chat_id, 'movimientos')
        else:
            _consulta(chat_id, 'credito', grupos[2])
        return Response({'ok': True, 'procesado': True})

    text = message.get('text') if isinstance(message, dict) else None
    texto_recibido = text.strip() if isinstance(text, str) else ''
    match = START_RE.fullmatch(texto_recibido) if isinstance(text, str) else None
    if not match and COMMAND_RE.fullmatch(texto_recibido):
        comando = texto_recibido.split('@', 1)[0].lstrip('/').lower()
        _consulta(chat_id, 'menu' if comando == 'menu' else comando)
        return Response({'ok': True, 'procesado': True})
    if not match:
        _mensaje(chat_id, 'Escribe /menu para consultar tus créditos y movimientos.')
        return Response({'ok': True, 'procesado': True})
    token = match.group(1)
    if not token:
        _consulta(chat_id, 'menu')
        return Response({'ok': True, 'procesado': True})
    if not TOKEN_RE.fullmatch(token) or not invitacion_pendiente(token):
        _mensaje(chat_id, 'La invitación ya venció o no está disponible. Solicita una nueva.')
        return Response({'ok': True, 'procesado': True})
    enviar_consentimiento(chat_id, token)
    return Response({'ok': True, 'procesado': True})
