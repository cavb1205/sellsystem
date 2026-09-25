from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def copiar_ruta_principal_a_asignaciones(apps, schema_editor):
    Perfil = apps.get_model('Trabajadores', 'Perfil')
    AsignacionTrabajador = apps.get_model('Trabajadores', 'AsignacionTrabajador')
    alias = schema_editor.connection.alias

    for perfil in Perfil.objects.using(alias).all().iterator():
        AsignacionTrabajador.objects.using(alias).get_or_create(
            perfil_id=perfil.pk,
            tienda_id=perfil.tienda_id,
            defaults={'activa': True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('Trabajadores', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='AsignacionTrabajador',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('activa', models.BooleanField(default=True)),
                ('asignada_en', models.DateTimeField(auto_now_add=True)),
                ('retirada_en', models.DateTimeField(blank=True, null=True)),
                ('asignada_por', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='asignaciones_trabajador_creadas',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('perfil', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='asignaciones_ruta',
                    to='Trabajadores.perfil',
                )),
                ('retirada_por', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='asignaciones_trabajador_retiradas',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('tienda', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='asignaciones_trabajador',
                    to='Tiendas.tienda',
                )),
            ],
            options={
                'ordering': ['tienda__nombre'],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('perfil', 'tienda'),
                        name='unique_asignacion_trabajador_tienda',
                    ),
                ],
            },
        ),
        migrations.RunPython(
            copiar_ruta_principal_a_asignaciones,
            migrations.RunPython.noop,
        ),
    ]
