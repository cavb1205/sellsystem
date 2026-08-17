from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('Tiendas', '0022_alertaoperativa_seguimiento_preventivo'),
    ]

    operations = [
        migrations.CreateModel(
            name='MovimientoCaja',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('tienda_nombre', models.CharField(blank=True, max_length=200)),
                (
                    'tipo',
                    models.CharField(
                        choices=[
                            ('VENTA', 'Crédito nuevo'),
                            ('RECAUDO', 'Abono recibido'),
                            ('GASTO', 'Gasto'),
                            ('UTILIDAD', 'Utilidad retirada'),
                            ('APORTE', 'Aporte de capital'),
                            ('AJUSTE', 'Ajuste de caja'),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                (
                    'accion',
                    models.CharField(
                        choices=[
                            ('CREACION', 'Creación'),
                            ('CORRECCION', 'Corrección'),
                            ('REVERSA', 'Reversa'),
                            ('AJUSTE', 'Ajuste'),
                        ],
                        default='CREACION',
                        max_length=20,
                    ),
                ),
                ('delta', models.DecimalField(decimal_places=2, max_digits=12)),
                ('saldo_anterior', models.DecimalField(decimal_places=2, max_digits=12)),
                ('saldo_posterior', models.DecimalField(decimal_places=2, max_digits=12)),
                ('creado_en', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('origen_tipo', models.CharField(blank=True, max_length=60)),
                ('origen_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('detalle', models.TextField(blank=True)),
                (
                    'tienda',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='movimientos_caja',
                        to='Tiendas.tienda',
                    ),
                ),
                (
                    'usuario',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='movimientos_caja',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['-creado_en', '-id'],
                'indexes': [
                    models.Index(fields=['tienda', '-creado_en'], name='Tiendas_mov_tienda_i_1c2d8b_idx'),
                    models.Index(fields=['tipo', '-creado_en'], name='Tiendas_mov_tipo_i_5b8c77_idx'),
                ],
            },
        ),
    ]
