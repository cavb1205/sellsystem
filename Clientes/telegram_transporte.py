"""Transporte de clientes: una sola petición, sin reintentos ni logs de secretos.

El worker debe revalidar destinatario y vínculo antes de llamar este módulo.
No usar desde una transacción financiera ni desde las vistas de recaudos.
"""
from dataclasses import dataclass
import logging
import re

import requests
from django.conf import settings
from django.db import DatabaseError

from Clientes.telegram_limpieza import finalizar_salida, preparar_salida


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultadoEnvio:
    estado: str
    codigo: str
    mensaje_id: int = None
    reintentar_en: int = None


@dataclass(frozen=True)
class ResultadoBorrado:
    estado: str
    reintentar_en: int = None


def enviar_texto(chat_id, texto):
    return _enviar_payload(chat_id, texto)


def enviar_menu(chat_id, texto, botones):
    """Envía una consulta con botones inline, sin aceptar datos financieros."""
    if (not isinstance(botones, list) or len(botones) > 20
            or any(not isinstance(fila, list) or len(fila) > 3 for fila in botones)):
        return ResultadoEnvio('fallido', 'menu_invalido')
    for fila in botones:
        for boton in fila:
            if (not isinstance(boton, dict)
                    or not isinstance(boton.get('text'), str)
                    or not isinstance(boton.get('callback_data'), str)
                    or not 1 <= len(boton['callback_data'].encode('utf-8')) <= 64):
                return ResultadoEnvio('fallido', 'menu_invalido')
    return _enviar_payload(
        chat_id, texto, {'inline_keyboard': botones} if botones else None,
    )


def _enviar_payload(chat_id, texto, reply_markup=None):
    if (getattr(settings, 'TELEGRAM_CLIENTES_ENABLED', False) is not True
            or getattr(settings, 'TELEGRAM_CLIENTES_DELIVERY_ENABLED', False) is not True):
        return ResultadoEnvio('omitido', 'envios_desactivados')
    token = getattr(settings, 'TELEGRAM_CLIENTES_BOT_TOKEN', '')
    if (not isinstance(token, str) or not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token)
            or token == getattr(settings, 'TELEGRAM_BOT_TOKEN', None)):
        return ResultadoEnvio('fallido', 'configuracion_bot_clientes')
    if type(chat_id) is not int or chat_id <= 0:
        return ResultadoEnvio('fallido', 'chat_no_privado')
    if not isinstance(texto, str) or not texto.strip() or len(texto.encode('utf-16-le')) // 2 > 4096:
        return ResultadoEnvio('fallido', 'texto_invalido')
    try:
        registro = preparar_salida(chat_id)
    except (DatabaseError, ValueError):
        return ResultadoEnvio('fallido', 'registro_mensaje')

    def finalizar(resultado):
        try:
            finalizar_salida(registro.pk, resultado)
        except DatabaseError:
            # No imprimir chat_id, texto ni token en los logs.
            logger.error('No se pudo actualizar el registro de retención de Telegram.')
        return resultado

    try:
        payload = {'chat_id': chat_id, 'text': texto,
                   'link_preview_options': {'is_disabled': True}}
        if reply_markup is not None:
            payload['reply_markup'] = reply_markup
        respuesta = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json=payload,
            timeout=(5, 15), allow_redirects=False,
        )
    except requests.RequestException:
        # Un timeout o corte puede ocurrir después de aceptar el mensaje.
        return finalizar(ResultadoEnvio('incierto', 'conexion_interrumpida'))
    try:
        datos = respuesta.json()
    except ValueError:
        return finalizar(ResultadoEnvio('incierto', 'respuesta_no_json'))
    if not isinstance(datos, dict):
        return finalizar(ResultadoEnvio('incierto', 'respuesta_invalida'))
    resultado = datos.get('result')
    if respuesta.status_code == 200 and datos.get('ok') is True and isinstance(resultado, dict):
        mensaje_id = resultado.get('message_id')
        chat = resultado.get('chat')
        if (type(mensaje_id) is int and mensaje_id > 0 and isinstance(chat, dict)
                and chat.get('id') == chat_id and chat.get('type') == 'private'):
            return finalizar(ResultadoEnvio('enviado', 'confirmado', mensaje_id=mensaje_id))
        return finalizar(ResultadoEnvio('incierto', 'confirmacion_invalida'))
    if datos.get('ok') is False and datos.get('error_code') == respuesta.status_code:
        if respuesta.status_code == 429:
            parametros = datos.get('parameters')
            espera = parametros.get('retry_after') if isinstance(parametros, dict) else None
            if type(espera) is int and 1 <= espera <= 86400:
                return finalizar(ResultadoEnvio(
                    'pendiente', 'limite_telegram', reintentar_en=espera,
                ))
        if respuesta.status_code in (400, 401, 403, 404):
            return finalizar(ResultadoEnvio('fallido', f'rechazado_{respuesta.status_code}'))
    return finalizar(ResultadoEnvio('incierto', 'respuesta_no_confirmada'))


def enviar_consentimiento(chat_id, token):
    """Muestra consentimiento sin datos financieros ni URL administrativa."""
    if (getattr(settings, 'TELEGRAM_CLIENTES_ENABLED', False) is not True
            or getattr(settings, 'TELEGRAM_CLIENTES_DELIVERY_ENABLED', False) is not True):
        return ResultadoEnvio('omitido', 'envios_desactivados')
    if type(chat_id) is not int or chat_id <= 0 or not isinstance(token, str):
        return ResultadoEnvio('fallido', 'solicitud_invalida')
    token_bot = getattr(settings, 'TELEGRAM_CLIENTES_BOT_TOKEN', '')
    if (not isinstance(token_bot, str)
            or not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token_bot)
            or token_bot == getattr(settings, 'TELEGRAM_BOT_TOKEN', None)):
        return ResultadoEnvio('fallido', 'configuracion_bot_clientes')
    return _enviar_payload(
        chat_id,
        'Hay una solicitud para vincular tus avisos de crédito a este chat. '
        'Acepta solo si reconoces la invitación de tu administrador.',
        {'inline_keyboard': [[
            {'text': 'Acepto vincular este chat', 'callback_data': f'tgcliente:aceptar:{token}'},
            {'text': 'No vincular', 'callback_data': f'tgcliente:rechazar:{token}'},
        ]]},
    )


def eliminar_mensaje(chat_id, mensaje_id):
    """Elimina un mensaje privado antes del límite de 48 horas de Telegram."""
    if (type(chat_id) is not int or chat_id <= 0
            or type(mensaje_id) is not int or mensaje_id <= 0):
        return ResultadoBorrado('fallido')
    token = getattr(settings, 'TELEGRAM_CLIENTES_BOT_TOKEN', '')
    if (not isinstance(token, str) or not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token)
            or token == getattr(settings, 'TELEGRAM_BOT_TOKEN', None)):
        return ResultadoBorrado('fallido')
    try:
        respuesta = requests.post(
            f'https://api.telegram.org/bot{token}/deleteMessage',
            json={'chat_id': chat_id, 'message_id': mensaje_id},
            timeout=(5, 15), allow_redirects=False,
        )
    except requests.RequestException:
        return ResultadoBorrado('pendiente')
    try:
        datos = respuesta.json()
    except ValueError:
        return ResultadoBorrado('pendiente')
    if respuesta.status_code == 200 and isinstance(datos, dict) and datos.get('ok') is True:
        return ResultadoBorrado('borrado')
    if respuesta.status_code == 429 and isinstance(datos, dict):
        parametros = datos.get('parameters')
        espera = parametros.get('retry_after') if isinstance(parametros, dict) else None
        if type(espera) is int and 1 <= espera <= 86400:
            return ResultadoBorrado('pendiente', reintentar_en=espera)
    descripcion = datos.get('description', '') if isinstance(datos, dict) else ''
    if (respuesta.status_code == 400 and isinstance(descripcion, str)
            and ('message to delete not found' in descripcion.lower()
                 or 'message not found' in descripcion.lower())):
        # El usuario pudo haber borrado el mensaje por su cuenta.
        return ResultadoBorrado('borrado')
    return ResultadoBorrado('pendiente')


def eliminar_mensajes(chat_id, mensajes_id):
    """Elimina hasta 100 mensajes de un chat en una llamada de Bot API."""
    if (type(chat_id) is not int or chat_id <= 0
            or not isinstance(mensajes_id, list)
            or not 1 <= len(mensajes_id) <= 100
            or any(type(mensaje_id) is not int or mensaje_id <= 0
                   for mensaje_id in mensajes_id)):
        return ResultadoBorrado('fallido')
    token = getattr(settings, 'TELEGRAM_CLIENTES_BOT_TOKEN', '')
    if (not isinstance(token, str) or not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token)
            or token == getattr(settings, 'TELEGRAM_BOT_TOKEN', None)):
        return ResultadoBorrado('fallido')
    try:
        respuesta = requests.post(
            f'https://api.telegram.org/bot{token}/deleteMessages',
            json={'chat_id': chat_id, 'message_ids': mensajes_id},
            timeout=(5, 15), allow_redirects=False,
        )
    except requests.RequestException:
        return ResultadoBorrado('pendiente')
    try:
        datos = respuesta.json()
    except ValueError:
        return ResultadoBorrado('pendiente')
    if (respuesta.status_code == 200 and isinstance(datos, dict)
            and datos.get('ok') is True and datos.get('result') is True):
        return ResultadoBorrado('borrado')
    if respuesta.status_code == 429 and isinstance(datos, dict):
        parametros = datos.get('parameters')
        espera = parametros.get('retry_after') if isinstance(parametros, dict) else None
        if type(espera) is int and 1 <= espera <= 86400:
            return ResultadoBorrado('pendiente', reintentar_en=espera)
    return ResultadoBorrado('pendiente')


def responder_callback(callback_id, texto):
    """Confirma visualmente un botón; no contiene información financiera."""
    if not isinstance(callback_id, str) or not callback_id or len(callback_id) > 256:
        return ResultadoEnvio('fallido', 'callback_invalido')
    token_bot = getattr(settings, 'TELEGRAM_CLIENTES_BOT_TOKEN', '')
    if (getattr(settings, 'TELEGRAM_CLIENTES_ENABLED', False) is not True
            or getattr(settings, 'TELEGRAM_CLIENTES_DELIVERY_ENABLED', False) is not True
            or not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token_bot)
            or token_bot == getattr(settings, 'TELEGRAM_BOT_TOKEN', None)):
        return ResultadoEnvio('omitido', 'envios_desactivados')
    try:
        respuesta = requests.post(
            f'https://api.telegram.org/bot{token_bot}/answerCallbackQuery',
            json={'callback_query_id': callback_id, 'text': str(texto)[:200]},
            timeout=(5, 15), allow_redirects=False,
        )
        datos = respuesta.json()
    except (requests.RequestException, ValueError):
        return ResultadoEnvio('incierto', 'conexion_interrumpida')
    if respuesta.status_code == 200 and isinstance(datos, dict) and datos.get('ok') is True:
        return ResultadoEnvio('enviado', 'confirmado')
    return ResultadoEnvio('incierto', 'respuesta_no_confirmada')
