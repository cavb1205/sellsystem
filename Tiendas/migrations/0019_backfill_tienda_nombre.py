# Backfill: copia el nombre actual de cada tienda al snapshot tienda_nombre
# de sus pagos, para que el informe de ingresos siga cuadrando si la ruta se
# elimina. Reversible (el reverse limpia el snapshot).

from django.db import migrations


def backfill(apps, schema_editor):
    PagoMembresia = apps.get_model('Tiendas', 'PagoMembresia')
    for pago in PagoMembresia.objects.filter(tienda__isnull=False).select_related('tienda'):
        if not pago.tienda_nombre:
            pago.tienda_nombre = pago.tienda.nombre
            pago.save(update_fields=['tienda_nombre'])


def reverse(apps, schema_editor):
    PagoMembresia = apps.get_model('Tiendas', 'PagoMembresia')
    PagoMembresia.objects.update(tienda_nombre='')


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0018_pagomembresia_snapshot_y_archivado'),
    ]

    operations = [
        migrations.RunPython(backfill, reverse),
    ]
