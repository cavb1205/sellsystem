"""Servicios de vinculación. Sin envíos ni búsqueda de identidades por teléfono."""
import hashlib
import secrets
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from Clientes.models import Cliente
from Clientes.telegram_models import TelegramEvento, TelegramInvitacion, TelegramVinculo
from Tiendas.models import Tienda_Administrador
from Trabajadores.models import Perfil


def _alcance(cliente):
    return Tienda_Administrador.objects.filter(
        tienda_id=cliente.tienda_id, tienda__estado=True,
        administrador__is_active=True,
    ).exists()


def _usuario_autorizado(usuario, cliente):
    es_administrador = (
        usuario and usuario.is_staff
        and Tienda_Administrador.objects.filter(
            tienda_id=cliente.tienda_id, administrador=usuario).exists()
    )
    es_trabajador = Perfil.objects.filter(
        trabajador=usuario, tienda_id=cliente.tienda_id,
    ).exists()
    if not (usuario and usuario.is_authenticated and usuario.is_active
            and _alcance(cliente) and (es_administrador or es_trabajador)):
        raise PermissionDenied('Cliente fuera del alcance autorizado')


def _privado(chat_id):
    # Solo chats privados; el receptor se obtiene del update verificado del bot.
    if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id <= 0:
        raise ValidationError('Se requiere un chat privado válido')


def _cancelar(vinculo):
    TelegramEvento.objects.filter(vinculo=vinculo).exclude(
        estado__in=['enviado', 'cancelado'],
    ).update(estado='cancelado', datos={})


@transaction.atomic
def crear_invitacion(cliente_id, usuario):
    cliente = Cliente.objects.select_for_update().get(pk=cliente_id)
    _usuario_autorizado(usuario, cliente)
    if TelegramVinculo.objects.filter(cliente=cliente).filter(
            consentimiento_en__isnull=False).exists():
        raise ValidationError('Revoca primero la vinculación existente')
    ahora = timezone.now()
    TelegramInvitacion.objects.filter(cliente=cliente, consumida_en__isnull=True).update(
        consumida_en=ahora,
    )
    token = secrets.token_urlsafe(32)
    invitacion = TelegramInvitacion.objects.create(
        cliente=cliente, token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expira_en=ahora + timedelta(minutes=15), creada_por=usuario,
    )
    return token, invitacion.expira_en


def invitacion_pendiente(token):
    """Valida un token sin consumirlo ni crear el vínculo."""
    if not isinstance(token, str) or len(token) != 43:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    invitacion = TelegramInvitacion.objects.select_related('cliente').filter(
        token_hash=digest,
        consumida_en__isnull=True,
        expira_en__gt=timezone.now(),
    ).first()
    if (not invitacion or not _alcance(invitacion.cliente)
            or TelegramVinculo.objects.filter(
                cliente=invitacion.cliente, consentimiento_en__isnull=False,
            ).exists()):
        return None
    return invitacion


@transaction.atomic
def aceptar_invitacion(token, chat_id, consentimiento=False):
    _privado(chat_id)
    if consentimiento is not True:
        raise ValidationError('Se requiere consentimiento explícito')
    if not isinstance(token, str) or len(token) != 43:
        raise ValidationError('Invitación inválida o vencida')
    digest = hashlib.sha256(token.encode()).hexdigest()
    referencia = TelegramInvitacion.objects.filter(token_hash=digest).first()
    if not referencia:
        raise ValidationError('Invitación inválida o vencida')
    cliente = Cliente.objects.select_for_update().get(pk=referencia.cliente_id)
    invitacion = TelegramInvitacion.objects.select_for_update().get(pk=referencia.pk)
    ahora = timezone.now()
    if invitacion.consumida_en or invitacion.expira_en <= ahora or not _alcance(cliente):
        raise ValidationError('Invitación inválida o vencida')
    vinculo = TelegramVinculo.objects.filter(cliente=cliente).first()
    if vinculo and vinculo.consentimiento_en:
        raise ValidationError('La ficha ya tiene una vinculación')
    if vinculo:
        _cancelar(vinculo)
        vinculo.version += 1
        vinculo.chat_id = chat_id
    else:
        vinculo = TelegramVinculo(cliente=cliente, chat_id=chat_id)
    vinculo.activo = False
    vinculo.consentimiento_en = ahora
    vinculo.confirmado_en = None
    vinculo.confirmado_por = None
    vinculo.save()
    invitacion.consumida_en = ahora
    invitacion.save(update_fields=['consumida_en'])
    return vinculo


@transaction.atomic
def confirmar_vinculo(cliente_id, usuario, chat_id, version):
    cliente = Cliente.objects.select_for_update().get(pk=cliente_id)
    _usuario_autorizado(usuario, cliente)
    vinculo = TelegramVinculo.objects.get(cliente=cliente)
    if (vinculo.chat_id != chat_id or vinculo.version != version
            or not vinculo.consentimiento_en):
        raise ValidationError('La solicitud cambió; vuelve a revisarla')
    vinculo.activo = True
    vinculo.confirmado_en = timezone.now()
    vinculo.confirmado_por = usuario
    vinculo.save()
    return vinculo


@transaction.atomic
def revocar_vinculo(cliente_id, usuario):
    cliente = Cliente.objects.select_for_update().get(pk=cliente_id)
    _usuario_autorizado(usuario, cliente)
    vinculo = TelegramVinculo.objects.filter(cliente=cliente).first()
    if vinculo:
        _cancelar(vinculo)
        vinculo.activo = False
        vinculo.consentimiento_en = None
        vinculo.confirmado_en = None
        vinculo.confirmado_por = None
        vinculo.version += 1
        vinculo.save()
    TelegramInvitacion.objects.filter(cliente=cliente, consumida_en__isnull=True).update(
        consumida_en=timezone.now(),
    )


def cliente_autorizado(chat_id, cliente_id):
    _privado(chat_id)
    vinculo = TelegramVinculo.objects.select_related('cliente').filter(
        cliente_id=cliente_id, chat_id=chat_id, activo=True,
        consentimiento_en__isnull=False, confirmado_en__isnull=False,
    ).first()
    if not vinculo or not _alcance(vinculo.cliente):
        raise PermissionDenied('Cliente no autorizado')
    return vinculo.cliente
