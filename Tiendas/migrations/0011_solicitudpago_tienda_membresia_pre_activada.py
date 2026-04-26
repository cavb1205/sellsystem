from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0010_tienda_cupo_minimo_nuevo'),
    ]

    operations = [
        # Nuevo estado 'Pre-activada' en Tienda_Membresia (solo amplía max_length y choices, no rompe datos)
        migrations.AlterField(
            model_name='tienda_membresia',
            name='estado',
            field=models.CharField(
                choices=[
                    ('Activa', 'Activa'),
                    ('Vencida', 'Vencida'),
                    ('Pendiente Pago', 'Pendiente Pago'),
                    ('Pre-activada', 'Pre-activada'),
                ],
                default='Activa',
                max_length=50,
            ),
        ),
        # Campo nullable para pre-activación temporal
        migrations.AddField(
            model_name='tienda_membresia',
            name='pre_activada_hasta',
            field=models.DateField(blank=True, null=True),
        ),
        # Modelo SolicitudPago
        migrations.CreateModel(
            name='SolicitudPago',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo', models.CharField(db_index=True, max_length=12, unique=True)),
                ('estado', models.CharField(
                    choices=[
                        ('pendiente', 'Esperando comprobante'),
                        ('procesando', 'Validando'),
                        ('aprobada', 'Aprobada'),
                        ('pre_aprobada', 'Pre-aprobada (revisión manual)'),
                        ('rechazada', 'Rechazada'),
                        ('expirada', 'Expirada'),
                    ],
                    default='pendiente',
                    max_length=20,
                )),
                ('wa_from_number', models.CharField(blank=True, max_length=30)),
                ('wa_message_id', models.CharField(blank=True, max_length=100)),
                ('extraccion_ia', models.JSONField(blank=True, default=dict)),
                ('confianza_ia', models.FloatField(blank=True, null=True)),
                ('referencia_bancaria', models.CharField(blank=True, db_index=True, max_length=100)),
                ('monto_detectado', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('motivo_rechazo', models.TextField(blank=True)),
                ('creada', models.DateTimeField(auto_now_add=True)),
                ('procesada', models.DateTimeField(blank=True, null=True)),
                ('expira', models.DateTimeField()),
                ('membresia', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='Tiendas.membresia')),
                ('tienda', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='solicitudes_pago', to='Tiendas.tienda')),
            ],
        ),
    ]
