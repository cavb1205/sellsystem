"""Solo las tablas del recorrido; no corregir diferencias legacy en este cambio."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('Tiendas', '0024_tienda_zona_horaria'),
    ]
    operations = [
        migrations.CreateModel(name='OrdenVisita', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('clientes', models.JSONField(default=list)),
            ('version', models.PositiveIntegerField(default=0)),
            ('actualizado_en', models.DateTimeField(blank=True, null=True)),
            ('actualizado_por_nombre', models.CharField(blank=True, max_length=200)),
            ('tienda', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='Tiendas.tienda')),
        ]),
        migrations.CreateModel(name='CambioOrdenVisita', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('version', models.PositiveIntegerField()),
            ('anterior', models.JSONField(default=list)),
            ('nuevo', models.JSONField(default=list)),
            ('usuario_nombre', models.CharField(max_length=200)),
            ('registrado_en', models.DateTimeField(default=django.utils.timezone.now)),
            ('tienda', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='Tiendas.tienda')),
            ('usuario', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.AddConstraint(model_name='cambioordenvisita', constraint=models.UniqueConstraint(
            fields=('tienda', 'version'), name='unique_cambio_orden_visita_version')),
    ]
