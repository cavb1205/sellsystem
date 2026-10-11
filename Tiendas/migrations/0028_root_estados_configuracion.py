from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('Tiendas', '0027_root_integridad'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name='cuentadestino', name='version', field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name='solicitudpago', name='cuenta_acordada', field=models.JSONField(default=dict, blank=True)),
        migrations.AddField(model_name='solicitudpago', name='confirmada_fuera_plazo', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='solicitudpago', name='motivo_revision', field=models.CharField(max_length=500, blank=True)),
        migrations.CreateModel(name='CambioConfiguracionRoot', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('tipo', models.CharField(max_length=10, choices=[('cuenta', 'Cuenta bancaria'), ('planes', 'Tarifas')])),
            ('usuario_nombre', models.CharField(max_length=200)),
            ('motivo', models.CharField(max_length=500)),
            ('fecha', models.DateTimeField(default=django.utils.timezone.now)),
            ('anterior', models.JSONField(default=dict)), ('nuevo', models.JSONField(default=dict)),
            ('usuario', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
        ]),
    ]
