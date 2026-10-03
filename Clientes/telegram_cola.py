"""Reserva durable de avisos. No usa la red ni modifica operaciones financieras.

Un resultado incierto bloquea los avisos posteriores de esa ficha: no se
reenvía automáticamente un mensaje que Telegram podría haber recibido.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from Clientes.models import Cliente
from Clientes.telegram_models import TelegramEvento, TelegramVinculo
from Clientes.telegram_vinculacion import _alcance
from Clientes.telegram_mensajes import mensaje_evento
from dataclasses import dataclass

MAX_INTENTOS = 3


def _datos_entrega(codigo):
    """Conserva solo el estado técnico; nunca el detalle financiero enviado."""
    return {
        'entrega': {
            'codigo': codigo,
            'actualizado_en': timezone.now().isoformat(),
        },
    }


@dataclass(frozen=True)
class PreparacionEnvio:
    evento_id: int
    intento: int
    version_vinculo: int
    chat_id: int
    texto: str


@transaction.atomic
def reservar_siguiente(vinculo_id):
    if (getattr(settings, 'TELEGRAM_CLIENTES_ENABLED', False) is not True
            or getattr(settings, 'TELEGRAM_CLIENTES_DELIVERY_ENABLED', False) is not True):
        return None
    referencia = TelegramVinculo.objects.filter(pk=vinculo_id).values('cliente_id').first()
    if not referencia:
        return None
    # Mismo orden que los servicios de revocación: cliente, vínculo, evento.
    cliente = Cliente.objects.select_for_update().get(pk=referencia['cliente_id'])
    vinculo = TelegramVinculo.objects.select_for_update().get(pk=vinculo_id)
    eventos = TelegramEvento.objects.filter(vinculo=vinculo).exclude(
        estado__in=['enviado', 'cancelado'])
    if (not vinculo.activo or not vinculo.consentimiento_en or not vinculo.confirmado_en
            or vinculo.chat_id <= 0 or not _alcance(cliente)):
        eventos.update(estado='cancelado', datos={})
        return None
    eventos.exclude(version_vinculo=vinculo.version).update(estado='cancelado', datos={})
    evento = eventos.select_for_update().order_by('id').first()
    if not evento:
        return None
    ahora = timezone.now()
    if evento.estado == 'procesando':
        if evento.proximo_intento is None or evento.proximo_intento <= ahora:
            evento.estado = 'incierto'
            evento.datos = _datos_entrega('resultado_incierto')
            evento.save(update_fields=['estado', 'datos'])
        return None
    if evento.estado != 'pendiente' or evento.datos.get('requiere_revision'):
        return None
    if evento.proximo_intento and evento.proximo_intento > ahora:
        return None
    if evento.intentos >= MAX_INTENTOS:
        evento.estado = 'fallido'
        evento.datos = _datos_entrega('intentos_agotados')
        evento.save(update_fields=['estado', 'datos'])
        return None
    evento.estado = 'procesando'
    evento.intentos += 1
    evento.proximo_intento = ahora + timedelta(minutes=2)
    evento.save(update_fields=['estado', 'intentos', 'proximo_intento'])
    return evento


@transaction.atomic
def preparar_envio(evento_id, intento, version_vinculo):
    """Revalida autorización y construye el texto justo antes del HTTP."""
    referencia = TelegramEvento.objects.filter(pk=evento_id).values(
        'vinculo_id', 'vinculo__cliente_id').first()
    if not referencia:
        return None
    cliente = Cliente.objects.select_for_update().get(pk=referencia['vinculo__cliente_id'])
    vinculo = TelegramVinculo.objects.select_for_update().get(pk=referencia['vinculo_id'])
    evento = TelegramEvento.objects.select_for_update().get(pk=evento_id)
    autorizado = (
        evento.estado == 'procesando' and evento.intentos == intento
        and evento.version_vinculo == version_vinculo
        and vinculo.version == version_vinculo and vinculo.activo
        and vinculo.chat_id > 0 and vinculo.consentimiento_en
        and vinculo.confirmado_en and _alcance(cliente)
    )
    if not autorizado:
        if evento.estado == 'procesando' and evento.intentos == intento:
            evento.estado = 'cancelado'
            evento.proximo_intento = None
            evento.datos = _datos_entrega('cancelado_sin_autorizacion')
            evento.save(update_fields=['estado', 'proximo_intento', 'datos'])
        return None
    try:
        texto = mensaje_evento(evento)
    except (KeyError, TypeError, ValueError):
        evento.estado = 'fallido'
        evento.proximo_intento = None
        evento.datos = _datos_entrega('datos_no_publicables')
        evento.save(update_fields=['estado', 'proximo_intento', 'datos'])
        return None
    return PreparacionEnvio(
        evento_id=evento.pk, intento=evento.intentos,
        version_vinculo=evento.version_vinculo,
        chat_id=vinculo.chat_id, texto=texto,
    )


@transaction.atomic
def finalizar_reserva(evento_id, intento, version_vinculo, resultado):
    """Aplica solo la respuesta de la reserva vigente, sin invocar la red.

Un worker antiguo no puede sobrescribir otra reserva o una revocación.
Las confirmaciones tardías de reservas inciertas requieren revisión manual.
"""
    referencia = TelegramEvento.objects.filter(pk=evento_id).values(
        'vinculo_id', 'vinculo__cliente_id').first()
    if not referencia:
        return False
    cliente = Cliente.objects.select_for_update().get(pk=referencia['vinculo__cliente_id'])
    vinculo = TelegramVinculo.objects.select_for_update().get(pk=referencia['vinculo_id'])
    evento = TelegramEvento.objects.select_for_update().get(pk=evento_id)
    if (evento.estado != 'procesando' or evento.intentos != intento
            or evento.version_vinculo != version_vinculo):
        return False
    if (vinculo.version != version_vinculo or not vinculo.activo
            or not vinculo.consentimiento_en or not vinculo.confirmado_en
            or not _alcance(cliente)):
        evento.estado = 'cancelado'
        evento.proximo_intento = None
        evento.datos = _datos_entrega('cancelado_sin_autorizacion')
        evento.save(update_fields=['estado', 'proximo_intento', 'datos'])
        return False
    estado = resultado.estado
    codigo = 'resultado_incierto'
    proximo = None
    mensaje_id = None
    if estado == 'enviado' and type(resultado.mensaje_id) is int and resultado.mensaje_id > 0:
        mensaje_id = resultado.mensaje_id
        codigo = 'confirmado'
    elif (estado == 'pendiente' and resultado.codigo == 'limite_telegram'
          and type(resultado.reintentar_en) is int and 1 <= resultado.reintentar_en <= 86400):
        if intento >= MAX_INTENTOS:
            estado, codigo = 'fallido', 'intentos_agotados'
        else:
            proximo = timezone.now() + timedelta(seconds=resultado.reintentar_en)
            codigo = 'limite_telegram'
    elif estado == 'omitido' and resultado.codigo == 'envios_desactivados':
        estado, codigo = 'pendiente', 'envios_desactivados'
        # El transporte no hizo petición: no consume un intento de entrega.
        evento.intentos -= 1
    elif estado == 'fallido':
        codigo = resultado.codigo if isinstance(resultado.codigo, str) else 'envio_rechazado'
    else:
        estado = 'incierto'
    evento.estado = estado
    evento.mensaje_id = mensaje_id
    evento.proximo_intento = proximo
    # Los reintentos expresamente reprogramados necesitan su snapshot; cualquier
    # otro resultado es terminal y debe retener solo metadatos no financieros.
    if estado == 'pendiente':
        evento.datos = {**evento.datos, **_datos_entrega(codigo)}
    else:
        evento.datos = _datos_entrega(codigo)
    evento.save(update_fields=['estado', 'mensaje_id', 'proximo_intento', 'datos', 'intentos'])
    return True
