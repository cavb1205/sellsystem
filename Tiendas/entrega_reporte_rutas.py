"""Entrega persistida; reintentos solo cuando se sabe que Telegram rechazó el envío.

sendMessage no ofrece idempotencia externa. Un timeout o una respuesta ambigua
se conserva como incierta y NO se reenvía automáticamente para evitar duplicados.
Los errores no incluyen URLs, tokens, chats ni texto financiero en logs.
"""
import json
import logging
from datetime import timedelta
from uuid import uuid4
import requests
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from Tiendas.models import ReporteRutasTelegram
from Tiendas.alertas_operativas import rutas_del_usuario, usuario_alertas_configurado
from Tiendas.reporte_rutas import partes_resumen

logger = logging.getLogger(__name__)
MAX_INTENTOS = 3


def botones_habilitados(usuario):
    return bool(getattr(settings, 'TELEGRAM_ASSISTANT_USERNAME', '') == usuario.username
        and str(getattr(settings, 'TELEGRAM_ASSISTANT_CHAT_ID', '')) == str(settings.TELEGRAM_ADMIN_CHAT_ID))


def preparar_reporte(usuario, fecha, rutas, corte=None):
    autorizadas = set(rutas_del_usuario(usuario).values_list('id', flat=True))
    filas = [r for r in rutas if r['id'] in autorizadas]
    corte = corte or timezone.now()
    with transaction.atomic():
        reporte, creado = ReporteRutasTelegram.objects.get_or_create(usuario=usuario, fecha=fecha,
            defaults={'chat_ref': str(settings.TELEGRAM_ADMIN_CHAT_ID), 'corte': corte,
                'rutas': json.loads(json.dumps(filas, cls=DjangoJSONEncoder))})
        if creado:
            nombres = {r['id']: r['nombre'] for r in filas}
            partes = partes_resumen(filas, fecha, corte)
            for parte in partes:
                parte.update(estado='pendiente', intentos=0, mensaje_id='', error='')
                if botones_habilitados(usuario):
                    parte['teclado'] = {'inline_keyboard': [[{
                        'text': 'Detalle · ' + ' '.join(str(nombres[ruta]).split())[:38],
                        'callback_data': f'asst:rep:{reporte.pk}:{ruta}:p:0'}]
                        for ruta in parte['rutas']]}
            reporte.partes = partes
            reporte.save(update_fields=['partes', 'actualizada'])
    return reporte


def enviar_parte(parte, chat_ref):
    payload = {'chat_id': chat_ref, 'text': parte['texto'], 'parse_mode': 'HTML',
        'disable_web_page_preview': True}
    if parte.get('teclado'):
        payload['reply_markup'] = parte['teclado']
    try:
        respuesta = requests.post(
            f'https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage',
            json=payload, timeout=15)
        datos = respuesta.json()
        if datos.get('ok') is True and isinstance(datos.get('result', {}).get('message_id'), int):
            return {'estado': 'enviado', 'mensaje_id': str(datos['result']['message_id']), 'error': ''}
        if datos.get('ok') is False and respuesta.status_code < 500:
            espera = datos.get('parameters', {}).get('retry_after', 60)
            espera = min(max(60, espera if isinstance(espera, int) else 60), 86400)
            return {'estado': 'fallido', 'error': 'rechazo_telegram',
                'proximo_intento': (timezone.now() + timedelta(seconds=espera)).isoformat()}
        return {'estado': 'incierto', 'error': 'respuesta_ambigua'}
    except requests.RequestException:
        return {'estado': 'incierto', 'error': 'red_sin_confirmacion'}
    except (ValueError, TypeError, AttributeError):
        return {'estado': 'incierto', 'error': 'respuesta_no_valida'}


def entregar_reporte(reporte_id):
    reporte = ReporteRutasTelegram.objects.select_related('usuario').get(pk=reporte_id)
    configurado = usuario_alertas_configurado()
    if not configurado or configurado.pk != reporte.usuario_id or not settings.TELEGRAM_BOT_TOKEN:
        return {'completo': False, 'omitido': True}
    if str(settings.TELEGRAM_ADMIN_CHAT_ID) != reporte.chat_ref:
        return {'completo': False, 'omitido': True}
    autorizadas = set(rutas_del_usuario(configurado).values_list('id', flat=True))
    for indice in range(len(reporte.partes)):
        nonce = uuid4().hex
        with transaction.atomic():
            actual = ReporteRutasTelegram.objects.select_for_update().get(pk=reporte_id)
            parte = actual.partes[indice]
            ahora = timezone.now()
            if parte['estado'] == 'enviando':
                inicio = timezone.datetime.fromisoformat(parte['inicio'])
                if ahora - inicio > timedelta(minutes=2):
                    parte.update(estado='incierto', error='proceso_sin_confirmacion')
                    actual.save(update_fields=['partes', 'actualizada'])
                continue
            if parte['estado'] not in ('pendiente', 'fallido') or parte['intentos'] >= MAX_INTENTOS:
                continue
            if parte.get('proximo_intento') and ahora < timezone.datetime.fromisoformat(parte['proximo_intento']):
                continue
            # Revalidar vínculos inmediatamente antes de enviar cada parte.
            autorizadas = set(rutas_del_usuario(configurado).values_list('id', flat=True))
            if not set(parte['rutas']).issubset(autorizadas):
                parte.update(estado='bloqueado', error='alcance_modificado')
                actual.save(update_fields=['partes', 'actualizada'])
                continue
            parte.update(estado='enviando', inicio=ahora.isoformat(), nonce=nonce,
                intentos=parte['intentos'] + 1)
            actual.save(update_fields=['partes', 'actualizada'])
            envio = dict(parte)
        resultado = enviar_parte(envio, reporte.chat_ref)
        with transaction.atomic():
            actual = ReporteRutasTelegram.objects.select_for_update().get(pk=reporte_id)
            parte = actual.partes[indice]
            if parte.get('nonce') == nonce and parte['estado'] == 'enviando':
                parte.update(resultado)
                actual.save(update_fields=['partes', 'actualizada'])
        if resultado['estado'] != 'enviado':
            logger.warning('Reporte %s, parte %s: %s', reporte_id, indice + 1, resultado['error'])
    reporte.refresh_from_db()
    estados = [p['estado'] for p in reporte.partes]
    return {'completo': bool(estados) and all(e == 'enviado' for e in estados),
        'enviadas': estados.count('enviado'), 'total': len(estados),
        'inciertas': estados.count('incierto'), 'fallidas': estados.count('fallido')}


def reintentar_pendientes(usuario, hasta):
    # Los reportes anteriores conservan su fecha/foto; nunca se regeneran.
    for reporte in ReporteRutasTelegram.objects.filter(usuario=usuario,
            fecha__gte=hasta - timedelta(days=7), fecha__lt=hasta).order_by('fecha'):
        if any(p['estado'] in ('pendiente', 'fallido', 'enviando') for p in reporte.partes):
            entregar_reporte(reporte.pk)
