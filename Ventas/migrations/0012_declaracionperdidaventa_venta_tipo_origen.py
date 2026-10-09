from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    # Solo esquema: no se fechan pérdidas antiguas ni se clasifican por ausencia.
    dependencies = [
        ('Ventas', '0011_ajusteventaadministrativo'),
        ('Tiendas', '0024_tienda_zona_horaria'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='venta', name='tipo_origen',
            field=models.CharField(blank=True, default='', editable=False, max_length=10,
                choices=[('Nuevo', 'Nuevo'), ('Renovacion', 'Renovación')]),
        ),
        migrations.CreateModel(
            name='DeclaracionPerdidaVenta',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('venta_id_ref', models.PositiveBigIntegerField()),
                ('tienda_id_ref', models.PositiveBigIntegerField(db_index=True)),
                ('tienda_nombre', models.CharField(max_length=200)),
                ('usuario_nombre', models.CharField(max_length=150)),
                ('registrada_en', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('fecha_declaracion', models.DateField(db_index=True)),
                ('zona_horaria', models.CharField(max_length=64)),
                ('fecha_venta_original', models.DateField()),
                ('capital_original', models.DecimalField(max_digits=12, decimal_places=2)),
                ('interes_contratado', models.DecimalField(max_digits=12, decimal_places=2)),
                ('abonos_reales_netos', models.DecimalField(max_digits=12, decimal_places=2)),
                ('saldo_declarado', models.DecimalField(max_digits=12, decimal_places=2)),
                ('capital_no_recuperado', models.DecimalField(max_digits=12, decimal_places=2)),
                ('interes_no_cobrado', models.DecimalField(max_digits=12, decimal_places=2)),
                ('saldo_consistente', models.BooleanField()),
                ('tienda', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='Tiendas.tienda')),
                ('usuario', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('venta', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='declaraciones_perdida', to='Ventas.venta')),
            ],
            options={
                'ordering': ['-registrada_en', '-id'],
                'indexes': [models.Index(fields=['tienda_id_ref', 'fecha_declaracion'], name='perdida_ruta_fecha_idx')],
            },
        ),
    ]
