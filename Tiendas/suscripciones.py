"""Correcciones de suscripción; no modifica caja, créditos ni recaudos."""
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .fecha_operativa import fecha_operativa
from .models import CambioSuscripcion, PagoMembresia, SolicitudPago, Tienda_Membresia

ESTADOS_PENDIENTES = ('pendiente', 'pendiente_confirmacion', 'procesando', 'pre_aprobada', 'aprobada')


def snapshot(tm):
    return {key: (value.isoformat() if hasattr(value, 'isoformat') else value)
        for key, value in {
            'membresia_id': tm.membresia_id, 'estado': tm.estado,
            'fecha_activacion': tm.fecha_activacion, 'fecha_vencimiento': tm.fecha_vencimiento,
            'pre_activada_hasta': tm.pre_activada_hasta, 'archivada': tm.archivada,
            'fecha_archivado': tm.fecha_archivado, 'vencida_manualmente': tm.vencida_manualmente,
            'vencimiento_manual_fecha': tm.vencimiento_manual_fecha, 'motivo': tm.motivo,
        }.items()}


def registrar_cambio(tm, anterior, accion, usuario=None, motivo='', pago=None, solicitud_codigo=''):
    """El llamador mantiene el bloqueo de membresía y la transacción."""
    tm.version += 1
    tm.save(update_fields=['version'])
    return CambioSuscripcion.objects.create(tienda=tm.tienda, tienda_nombre=tm.tienda.nombre,
        version=tm.version, accion=accion, motivo=motivo, usuario=usuario,
        usuario_nombre=usuario.username if usuario else 'Telegram',
        anterior=anterior, nuevo=snapshot(tm), pago=pago, solicitud_codigo=solicitud_codigo)


def anular_pago(pago, motivo, usuario=None):
    if pago.estado == 'anulado':
        return
    pago.estado = 'anulado'
    pago.anulado_motivo = motivo
    pago.anulado_en = timezone.now()
    pago.anulado_por = usuario
    pago.save(update_fields=['estado', 'anulado_motivo', 'anulado_en', 'anulado_por'])


def pendientes(tm):
    return SolicitudPago.objects.filter(tienda_id=tm.tienda_id, estado__in=ESTADOS_PENDIENTES)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def vencer_suscripcion(request, pk):
    if not request.user.is_superuser:
        return Response({'error': 'Solo root puede vencer una suscripción.'}, status=403)
    data = request.data
    if not isinstance(data, dict):
        return Response({'error': 'Se espera un objeto JSON.'}, status=400)
    motivo = data.get('motivo')
    version = data.get('version')
    pago_id = data.get('pago_id')
    if not isinstance(motivo, str) or not 5 <= len(motivo.strip()) <= 500:
        return Response({'error': 'Escribe un motivo de entre 5 y 500 caracteres.'}, status=400)
    if type(version) is not int or version < 0:
        return Response({'error': 'Recarga el detalle para obtener una versión válida.'}, status=400)
    if pago_id is not None and (type(pago_id) is not int or pago_id <= 0):
        return Response({'error': 'Selecciona un pago manual válido.'}, status=400)
    acknowledgement = data.get('confirmar_solicitudes_pendientes', False)
    if type(acknowledgement) is not bool:
        return Response({'error': 'Confirmación de solicitudes inválida.'}, status=400)
    with transaction.atomic():
        tm = Tienda_Membresia.objects.select_for_update().select_related('tienda').filter(tienda_id=pk).first()
        if tm is None:
            return Response({'error': 'Ruta o suscripción no encontrada.'}, status=404)
        if tm.version != version:
            return Response({'error': 'La suscripción cambió. Recarga el detalle antes de confirmar.',
                'code': 'suscripcion_modificada'}, status=409)
        if pendientes(tm).exists() and not acknowledgement:
            return Response({'error': 'Hay solicitudes de pago pendientes. Confirma que las revisaste; no serán anuladas.',
                'code': 'solicitudes_pendientes'}, status=409)
        pago = None
        if pago_id is not None:
            pago = PagoMembresia.objects.select_for_update().filter(pk=pago_id,
                tienda_id=tm.tienda_id, origen='manual', solicitud__isnull=True, estado='vigente').first()
            if pago is None:
                return Response({'error': 'El pago no pertenece a esta ruta, no es manual o ya está anulado.'}, status=400)
        if tm.vencida_manualmente and pago is None:
            return Response({'message': 'La ruta ya está vencida manualmente.', 'version': tm.version})
        anterior = snapshot(tm)
        if pago:
            anular_pago(pago, motivo.strip(), request.user)
        if tm.vencida_manualmente:
            # Corregir un ingreso después de vencer no cambia la fecha ni
            # reinicia el plazo de archivado de la suspensión original.
            registrar_cambio(tm, anterior, 'anulacion_ingreso_manual', request.user, motivo.strip(), pago)
            return Response({'message': 'Ingreso manual anulado. La ruta continúa vencida.',
                'version': tm.version, 'pago_anulado': pago.pk})
        tm.estado = 'Vencida'
        tm.fecha_vencimiento = fecha_operativa(tm.tienda)
        tm.pre_activada_hasta = None
        tm.vencida_manualmente = True
        tm.vencimiento_manual_fecha = timezone.now()
        tm.motivo = motivo.strip()
        tm.save(update_fields=['estado', 'fecha_vencimiento', 'pre_activada_hasta',
            'vencida_manualmente', 'vencimiento_manual_fecha', 'motivo'])
        registrar_cambio(tm, anterior, 'vencimiento_manual', request.user, motivo.strip(), pago)
    response = Response({'message': 'Suscripción vencida. El historial y la cartera se conservaron.',
        'version': tm.version, 'pago_anulado': pago.pk if pago else None})
    response['Cache-Control'] = 'no-store'
    return response
