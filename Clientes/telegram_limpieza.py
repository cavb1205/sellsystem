"""Registro mínimo y limpieza programada de mensajes del bot de clientes."""
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from Clientes.telegram_models import TelegramEvento, TelegramMensaje


RETENCION = timedelta(hours=24)
LIMITE_TELEGRAM = timedelta(hours=48)
RETENCION_EVENTOS_PENDIENTES = timedelta(days=7)
REINTENTO_SEGUNDOS = 3600


def registrar_entrada(chat_id, mensaje_id):
    """Guarda solo IDs; nunca conserva el texto enviado por el cliente."""
    if (type(chat_id) is not int or chat_id <= 0
            or type(mensaje_id) is not int or mensaje_id <= 0):
        return False
    TelegramMensaje.objects.get_or_create(
        chat_id=chat_id,
        mensaje_id=mensaje_id,
        defaults={'direccion': 'entrante'},
    )
    return True


def preparar_salida(chat_id):
    """Reserva metadatos antes del HTTP para no enviar sin poder rastrear."""
    if type(chat_id) is not int or chat_id <= 0:
        raise ValueError('chat_invalido')
    return TelegramMensaje.objects.create(
        chat_id=chat_id,
        direccion='saliente',
    )


def finalizar_salida(registro_id, resultado):
    registro = TelegramMensaje.objects.filter(pk=registro_id).first()
    if not registro:
        return
    if (resultado.estado == 'enviado'
            and type(resultado.mensaje_id) is int
            and resultado.mensaje_id > 0):
        ya_registrado = TelegramMensaje.objects.filter(
            chat_id=registro.chat_id,
            mensaje_id=resultado.mensaje_id,
        ).exclude(pk=registro.pk).exists()
        if ya_registrado:
            registro.delete()
            return
        registro.mensaje_id = resultado.mensaje_id
        registro.save(update_fields=['mensaje_id'])
    elif resultado.estado == 'incierto':
        # Puede haberse enviado; sin message_id no es posible borrarlo luego.
        registro.proximo_intento = timezone.now() + LIMITE_TELEGRAM
        registro.save(update_fields=['proximo_intento'])
    else:
        registro.delete()


def limpiar_mensajes_vencidos(limite=100):
    """Borra mensajes de 24h; descarta metadatos al llegar al límite de 48h."""
    if type(limite) is not int or not 1 <= limite <= 1000:
        raise ValueError('limite_invalido')
    if getattr(settings, 'TELEGRAM_CLIENTES_LIMPIEZA_ENABLED', False) is not True:
        return {'estado': 'desactivado', 'borrados': 0, 'vencidos': 0, 'pendientes': 0}

    ahora = timezone.now()
    limite_borrado = ahora - RETENCION
    limite_maximo = ahora - LIMITE_TELEGRAM

    vencidos_ids = list(
        TelegramMensaje.objects.filter(registrado_en__lte=limite_maximo)
        .order_by('registrado_en', 'id').values_list('id', flat=True)[:limite]
    )
    vencidos = TelegramMensaje.objects.filter(pk__in=vencidos_ids).delete()[0]

    candidatos = list(
        TelegramMensaje.objects.filter(
            registrado_en__lte=limite_borrado,
            registrado_en__gt=limite_maximo,
            mensaje_id__isnull=False,
        ).filter(
            Q(proximo_intento__isnull=True) | Q(proximo_intento__lte=ahora)
        ).order_by('registrado_en', 'id')[:limite]
    )

    borrados = 0
    pendientes = 0
    mensajes_por_chat = {}
    for mensaje in candidatos:
        mensajes_por_chat.setdefault(mensaje.chat_id, []).append(mensaje)

    from Clientes.telegram_transporte import eliminar_mensajes

    for chat_id, mensajes in mensajes_por_chat.items():
        for inicio in range(0, len(mensajes), 100):
            lote = mensajes[inicio:inicio + 100]
            resultado = eliminar_mensajes(
                chat_id, [mensaje.mensaje_id for mensaje in lote],
            )
            ids = [mensaje.pk for mensaje in lote]
            if resultado.estado == 'borrado':
                TelegramMensaje.objects.filter(pk__in=ids).delete()
                borrados += len(lote)
                continue
            espera = resultado.reintentar_en or REINTENTO_SEGUNDOS
            proximo = timezone.now() + timedelta(seconds=espera)
            for mensaje in lote:
                mensaje.intentos += 1
                mensaje.proximo_intento = proximo
                mensaje.save(update_fields=['intentos', 'proximo_intento'])
            pendientes += len(lote)

    return {
        'estado': 'procesado', 'borrados': borrados,
        'vencidos': vencidos, 'pendientes': pendientes,
    }


def limpiar_datos_vencidos(limite=500):
    """Elimina snapshots financieros terminales y vence pendientes de 7 días.

    Mantiene las filas y sus claves idempotentes; solo depura el JSON sensible.
    Cada grupo se procesa en un lote acotado para no cargar la base.
    """
    if type(limite) is not int or not 1 <= limite <= 1000:
        raise ValueError('limite_invalido')
    if getattr(settings, 'TELEGRAM_CLIENTES_LIMPIEZA_ENABLED', False) is not True:
        return {'estado': 'desactivado', 'scrubbed': 0, 'expirados': 0}

    ahora = timezone.now()
    terminales = ['enviado', 'incierto', 'fallido', 'cancelado']
    ids_terminales = list(
        TelegramEvento.objects.filter(estado__in=terminales)
        .exclude(datos={}).order_by('id').values_list('id', flat=True)[:limite]
    )
    scrubbed = TelegramEvento.objects.filter(pk__in=ids_terminales).update(datos={})

    vencen = list(
        TelegramEvento.objects.filter(
            estado__in=['pendiente', 'procesando'],
            creado_en__lte=ahora - RETENCION_EVENTOS_PENDIENTES,
        ).order_by('id').values_list('id', flat=True)[:limite]
    )
    expirados = TelegramEvento.objects.filter(pk__in=vencen).update(
        estado='fallido', datos={'entrega': {
            'codigo': 'expirado_por_retencion',
            'actualizado_en': ahora.isoformat(),
        }}, proximo_intento=None,
    )
    return {'estado': 'procesado', 'scrubbed': scrubbed, 'expirados': expirados}
