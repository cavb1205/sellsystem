# Migración escrita a mano: SOLO los cambios deseados.
# (makemigrations local arrastra drift preexistente —alters de id,
# caja_inicial y Tienda_Administrador— que no debe tocarse en producción.)
#
# Fase 1: blinda los ingresos ante el borrado de una ruta (SET_NULL + snapshot).
# Fase 2: archivado de rutas inactivas en Tienda_Membresia.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0017_backfill_pagos_membresia'),
    ]

    operations = [
        migrations.AddField(
            model_name='pagomembresia',
            name='tienda_nombre',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AlterField(
            model_name='pagomembresia',
            name='tienda',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='pagos_membresia', to='Tiendas.tienda',
            ),
        ),
        migrations.AddField(
            model_name='tienda_membresia',
            name='archivada',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='tienda_membresia',
            name='fecha_archivado',
            field=models.DateField(blank=True, null=True),
        ),
    ]
