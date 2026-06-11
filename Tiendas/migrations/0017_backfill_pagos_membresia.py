# Backfill: crea un PagoMembresia por cada SolicitudPago confirmada existente,
# usando el precio actual del plan (mejor dato disponible para la historia previa).
# Reversible: el reverse elimina solo los pagos vinculados a una solicitud.

from django.db import migrations


def backfill(apps, schema_editor):
    SolicitudPago = apps.get_model('Tiendas', 'SolicitudPago')
    PagoMembresia = apps.get_model('Tiendas', 'PagoMembresia')

    confirmadas = SolicitudPago.objects.filter(
        estado='confirmada'
    ).select_related('membresia')

    pagos = []
    for s in confirmadas:
        if PagoMembresia.objects.filter(solicitud=s).exists():
            continue
        fecha = s.procesada.date() if s.procesada else s.creada.date()
        pagos.append(PagoMembresia(
            tienda_id=s.tienda_id,
            membresia_id=s.membresia_id,
            monto=s.membresia.precio,
            fecha=fecha,
            origen='panel' if s.revisada_por_id else 'telegram',
            solicitud=s,
            registrado_por_id=s.revisada_por_id,
        ))
    PagoMembresia.objects.bulk_create(pagos)


def reverse(apps, schema_editor):
    PagoMembresia = apps.get_model('Tiendas', 'PagoMembresia')
    PagoMembresia.objects.filter(solicitud__isnull=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0016_pagomembresia'),
    ]

    operations = [
        migrations.RunPython(backfill, reverse),
    ]
