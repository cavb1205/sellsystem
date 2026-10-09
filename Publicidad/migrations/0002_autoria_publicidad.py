from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def conservar_autores(apps, schema_editor):
    Publicidad = apps.get_model('Publicidad', 'Publicidad')
    # Solo autoría; no reescribir fecha, coordenadas, nota ni hora históricas.
    # No deducir el rol anterior del rol actual de la cuenta.
    pendientes = []
    for punto in Publicidad.objects.using(schema_editor.connection.alias).select_related('trabajador__trabajador').iterator(chunk_size=500):
        if punto.trabajador_id:
            user = punto.trabajador.trabajador
            punto.registrado_por_id = user.pk
            punto.autor_nombre = (f'{user.first_name} {user.last_name}'.strip() or user.username)[:180]
            pendientes.append(punto)
        if len(pendientes) == 500:
            Publicidad.objects.using(schema_editor.connection.alias).bulk_update(pendientes, ['registrado_por', 'autor_nombre'])
            pendientes = []
    if pendientes:
        Publicidad.objects.using(schema_editor.connection.alias).bulk_update(pendientes, ['registrado_por', 'autor_nombre'])


class Migration(migrations.Migration):
    dependencies = [('Publicidad', '0001_initial'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AlterField(model_name='publicidad', name='trabajador', field=models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='publicidades', to='Trabajadores.perfil')),
        migrations.AlterField(model_name='publicidad', name='fecha', field=models.DateField(default=django.utils.timezone.localdate, editable=False)),
        migrations.AddField(model_name='publicidad', name='registrado_por', field=models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='publicidad_registrada', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='publicidad', name='autor_nombre', field=models.CharField(blank=True, default='', max_length=180)),
        migrations.AddField(model_name='publicidad', name='autor_rol', field=models.CharField(default='Historico', max_length=15,
            choices=[('Administrador', 'Administrador'), ('Trabajador', 'Trabajador'), ('Historico', 'Histórico')])),
        migrations.AddField(model_name='publicidad', name='solicitud_id', field=models.UUIDField(blank=True, editable=False, null=True)),
        migrations.AddConstraint(model_name='publicidad', constraint=models.UniqueConstraint(fields=('tienda','solicitud_id'),
            condition=models.Q(solicitud_id__isnull=False), name='publicidad_solicitud_unica')),
        migrations.RunPython(conservar_autores, migrations.RunPython.noop),
    ]
