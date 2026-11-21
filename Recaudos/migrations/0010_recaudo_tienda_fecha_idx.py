# Escrita a mano. En PostgreSQL crea el índice con CONCURRENTLY para no
# bloquear escrituras en Recaudos_recaudo; en otros motores (tests) usa el
# camino normal.
from django.db import migrations, models

INDICE = models.Index(fields=['tienda', 'fecha_recaudo'], name='recaudo_tienda_fecha_idx')


def crear_indice(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(
            'CREATE INDEX CONCURRENTLY IF NOT EXISTS "recaudo_tienda_fecha_idx" '
            'ON "Recaudos_recaudo" ("tienda_id", "fecha_recaudo")'
        )
    else:
        schema_editor.add_index(apps.get_model('Recaudos', 'Recaudo'), INDICE)


def borrar_indice(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP INDEX CONCURRENTLY IF EXISTS "recaudo_tienda_fecha_idx"')
    else:
        schema_editor.remove_index(apps.get_model('Recaudos', 'Recaudo'), INDICE)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('Recaudos', '0009_recaudo_es_renovacion'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.AddIndex(model_name='recaudo', index=INDICE)],
            database_operations=[migrations.RunPython(crear_indice, borrar_indice)],
        ),
    ]
