"""Outbox: se invoca dentro de la transacción financiera, después de guardar saldo.

No usa la red. Si la transacción se revierte, el evento también desaparece.
La activación sigue deshabilitada por defecto.
"""
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from uuid import UUID

from Clientes.telegram_models import TelegramEvento, TelegramVinculo
from Clientes.telegram_resumen import resumen_credito, ResumenInconsistente
from Clientes.telegram_vinculacion import _alcance


def registrar_evento(venta, recaudo=None, *, accion=None, operacion_id=None, ajuste=None):
    """Para corrección/reversa, operacion_id identifica la mutación, no el pago.

    Debe generarse y conservarse en el backend para reutilizarlo al reintentar.
    En reversas, el llamador conserva el ID del objeto antes de eliminarlo.
    """
    if getattr(settings, 'TELEGRAM_CLIENTES_ENABLED', False) is not True:
        return None
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('El evento requiere la transacción financiera')
    if not _alcance(venta.cliente):
        return None
    if venta.cliente.tienda_id != venta.tienda_id:
        raise ValueError('Cliente y crédito pertenecen a rutas diferentes')
    vinculo = TelegramVinculo.objects.select_for_update().filter(
        cliente_id=venta.cliente_id, activo=True,
        consentimiento_en__isnull=False, confirmado_en__isnull=False,
    ).first()
    if not vinculo:
        return None
    tipo = 'credito'
    origen = f'credito:{venta.pk}'
    if ajuste is not None:
        if not ajuste.pk or ajuste.venta_id != venta.pk or recaudo is not None or accion is not None:
            raise ValueError('Ajuste fuera del crédito autorizado')
        if ajuste.fecha_venta_anterior == ajuste.fecha_venta_nueva and ajuste.cuotas_anteriores == ajuste.cuotas_nuevas:
            return None
        tipo = 'ajuste_credito'
        origen = f'ajuste:{ajuste.pk}'
    if recaudo is not None:
        if recaudo.venta_id != venta.pk or recaudo.tienda_id != venta.tienda_id:
            raise ValueError('Recaudo fuera del crédito autorizado')
        if recaudo.es_renovacion and accion != 'renovacion':
            return None  # Las renovaciones necesitan su evento específico.
        if recaudo.valor_recaudo > 0 and not recaudo.visita_blanco_id:
            tipo = 'abono'
        elif recaudo.valor_recaudo == 0 and recaudo.visita_blanco_id:
            tipo = 'falla'
        elif accion not in ('correccion', 'reversa'):
            raise ValueError('Movimiento ambiguo; requiere revisión')
        origen = f'recaudo:{recaudo.pk}'
    if accion in ('edicion_credito', 'eliminacion_credito'):
        if recaudo is not None or ajuste is not None or not venta.pk:
            raise ValueError('Referencia de crédito inválida')
        operacion = str(UUID(str(operacion_id)))
        tipo = accion
        origen = f'{accion}:{venta.pk}:{operacion}'
    elif accion is not None:
        if accion not in ('correccion', 'reversa', 'renovacion') or recaudo is None or not recaudo.pk:
            raise ValueError('Acción o referencia inválida')
        if accion == 'renovacion' and not recaudo.es_renovacion:
            raise ValueError('La renovación requiere un asiento de renovación')
        try:
            operacion = str(UUID(str(operacion_id)))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Se requiere un identificador de operación UUID') from None
        tipo = accion
        origen = f'{accion}:{recaudo.pk}:{operacion}'
    clave = f'{vinculo.pk}:{vinculo.version}:{origen}'
    existente = TelegramEvento.objects.filter(clave=clave).first()
    if existente:
        return existente
    try:
        datos = resumen_credito(venta)
    except ResumenInconsistente:
        # Solo errores de conciliación conocidos. No ocultar errores de código,
        # permisos ni base de datos. La incidencia comparte el rollback financiero.
        return TelegramEvento.objects.create(
            vinculo=vinculo, version_vinculo=vinculo.version, clave=clave,
            tipo=tipo, credito_id_ref=venta.pk,
            registro_id_ref=recaudo.pk if recaudo else None,
            estado='fallido',
            datos={'requiere_revision': True, 'codigo': 'saldo_no_conciliado',
                   'registrado_en': timezone.now().isoformat()},
        )
    if venta.origen_renovacion_id:
        datos['origen_renovacion'] = venta.origen_renovacion_id
    datos['registrado_en'] = timezone.now().isoformat()
    if ajuste is not None:
        datos['ajuste'] = ajuste.pk
        datos['fecha_anterior'] = ajuste.fecha_venta_anterior.isoformat()
        datos['cuotas_anteriores'] = ajuste.cuotas_anteriores
        datos['valor_cuota_anterior'] = str(ajuste.valor_cuota_anterior)
        datos['vencimiento'] = ajuste.fecha_vencimiento_nueva.isoformat() if ajuste.fecha_vencimiento_nueva else None
    if recaudo is not None:
        datos.update(registro=recaudo.pk, fecha_movimiento=recaudo.fecha_recaudo.isoformat(),
                     monto_movimiento=str(recaudo.valor_recaudo))
    return TelegramEvento.objects.create(
        vinculo=vinculo, version_vinculo=vinculo.version, clave=clave,
        tipo=tipo, credito_id_ref=venta.pk,
        registro_id_ref=recaudo.pk if recaudo else None, datos=datos,
    )
