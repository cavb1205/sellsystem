from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0019_backfill_tienda_nombre'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AlertaOperativa',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('VENTA_CON_ALERTAS', 'Venta con señales operativas'), ('RIESGO_CARTERA', 'Riesgo de cartera'), ('CIERRE_AUSENTE', 'Cierre de caja ausente'), ('RESUMEN_OPERATIVO', 'Resumen operativo')], db_index=True, max_length=40)),
                ('severidad', models.CharField(choices=[('media', 'Media'), ('alta', 'Alta'), ('critica', 'Crítica')], default='media', max_length=10)),
                ('estado', models.CharField(choices=[('nueva', 'Nueva'), ('revisada', 'Revisada'), ('resuelta', 'Resuelta')], db_index=True, default='nueva', max_length=10)),
                ('clave_dedupe', models.CharField(db_index=True, max_length=255, unique=True)),
                ('titulo', models.CharField(max_length=200)),
                ('detalle', models.TextField()),
                ('tienda_id_ref', models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ('cliente_id_ref', models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ('venta_id_ref', models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ('datos', models.JSONField(blank=True, default=dict)),
                ('telegram_message_id', models.CharField(blank=True, max_length=50)),
                ('ocurrencias', models.PositiveIntegerField(default=1)),
                ('creada', models.DateTimeField(auto_now_add=True)),
                ('actualizada', models.DateTimeField(auto_now=True)),
                ('ultima_notificacion', models.DateTimeField(blank=True, null=True)),
                ('trabajador', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='alertas_operativas_generadas', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-creada', '-id'],
                'indexes': [
                    models.Index(fields=['tipo', 'estado'], name='Tiendas_al_tipo_5e0c7a_idx'),
                    models.Index(fields=['tienda_id_ref', 'creada'], name='Tiendas_al_tienda__d2e08c_idx'),
                ],
            },
        ),
    ]
