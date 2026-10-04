"""Endpoints administrativos; el chat receptor nunca se vincula desde el navegador."""
import hashlib
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from Clientes.models import Cliente
from Clientes.telegram_models import TelegramInvitacion, TelegramVinculo
from Clientes.telegram_vinculacion import (
    _usuario_autorizado, cancelar_invitacion, crear_invitacion,
    confirmar_vinculo, invitacion_vigente, revocar_vinculo,
)


class ConfirmacionSerializer(serializers.Serializer):
    chat_id = serializers.IntegerField(min_value=1)
    version = serializers.IntegerField(min_value=1)


class CancelacionInvitacionSerializer(serializers.Serializer):
    invitacion_id = serializers.IntegerField(min_value=1)


def _habilitado():
    return getattr(settings, 'TELEGRAM_CLIENTES_ENABLED', False) is True


@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def vinculacion(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    _usuario_autorizado(request.user, cliente)
    if not _habilitado():
        return Response({'habilitado': False}, status=503)
    if request.method == 'GET':
        vinculo = TelegramVinculo.objects.filter(cliente=cliente).first()
        data = {'habilitado': True, 'estado': 'sin_vincular', 'invitacion': None}
        if vinculo and vinculo.consentimiento_en:
            data.update(estado='activo' if vinculo.activo else 'pendiente_confirmacion',
                        chat_id=str(vinculo.chat_id), version=vinculo.version,
                        consentimiento_en=vinculo.consentimiento_en,
                        confirmado_en=vinculo.confirmado_en)
        else:
            pendiente = invitacion_vigente(cliente.pk)
            if pendiente:
                data['invitacion'] = {
                    'id': pendiente.pk, 'expira_en': pendiente.expira_en,
                }
        response = Response(data)
        response['Cache-Control'] = 'no-store'
        return response
    if request.method == 'DELETE':
        revocar_vinculo(cliente.pk, request.user)
        return Response(status=204)
    entrada = ConfirmacionSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    try:
        confirmar_vinculo(cliente.pk, request.user, **entrada.validated_data)
    except TelegramVinculo.DoesNotExist:
        return Response({'error': 'No hay solicitud pendiente'}, status=409)
    except ValidationError as exc:
        return Response({'error': exc.messages[0]}, status=409)
    return Response({'estado': 'activo'})


@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def invitacion(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    _usuario_autorizado(request.user, cliente)
    if request.method == 'DELETE':
        entrada = CancelacionInvitacionSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        cancelar_invitacion(cliente.pk, request.user, **entrada.validated_data)
        response = Response(status=204)
        response['Cache-Control'] = 'no-store'
        return response
    if not _habilitado():
        return Response({'habilitado': False}, status=503)
    username = getattr(settings, 'TELEGRAM_CLIENTES_BOT_USERNAME', '')
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', username) or not username.lower().endswith('bot'):
        return Response({'error': 'Bot de clientes pendiente de configuración'}, status=503)
    try:
        token, expira = crear_invitacion(cliente.pk, request.user)
    except ValidationError as exc:
        return Response({'error': exc.messages[0]}, status=409)
    # Identificar exactamente la invitación creada, incluso si otra petición
    # genera una nueva o el cliente la consume antes de terminar esta respuesta.
    invitacion_id = TelegramInvitacion.objects.values_list('id', flat=True).get(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
    )
    response = Response({'enlace': f'https://t.me/{username}?start={token}',
                         'expira_en': expira, 'invitacion_id': invitacion_id}, status=201)
    response['Cache-Control'] = 'no-store'
    return response
