"""Selección circular y acotada de vínculos Telegram activos."""

from django.db import transaction

from Clientes.telegram_models import TelegramVinculo, TelegramWorkerCursor


CLAVE_CURSOR = 'clientes'


@transaction.atomic
def obtener_lote(limite):
    """Retorna hasta `limite` IDs una sola vez, empezando tras el último cursor."""
    if type(limite) is not int or limite < 1:
        raise ValueError('limite_invalido')
    cursor, _ = TelegramWorkerCursor.objects.get_or_create(clave=CLAVE_CURSOR)
    cursor = TelegramWorkerCursor.objects.select_for_update().get(pk=cursor.pk)
    despues = list(
        TelegramVinculo.objects.filter(activo=True, pk__gt=cursor.ultimo_vinculo_id)
        .order_by('id').values_list('id', flat=True)[:limite]
    )
    restantes = limite - len(despues)
    if restantes:
        antes = list(
            TelegramVinculo.objects.filter(activo=True, pk__lte=cursor.ultimo_vinculo_id)
            .order_by('id').values_list('id', flat=True)[:restantes]
        )
        despues.extend(antes)
    return despues


@transaction.atomic
def avanzar(vinculo_id):
    """Avanza solo tras intentar el vínculo: interrupciones no lo saltan."""
    cursor, _ = TelegramWorkerCursor.objects.get_or_create(clave=CLAVE_CURSOR)
    cursor = TelegramWorkerCursor.objects.select_for_update().get(pk=cursor.pk)
    cursor.ultimo_vinculo_id = vinculo_id
    cursor.save(update_fields=['ultimo_vinculo_id', 'actualizado_en'])
