from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('Ventas', '0010_venta_creado_por'),
    ]

    operations = [
        migrations.CreateModel(
            name='AjusteVentaAdministrativo',
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
                ('motivo', models.CharField(max_length=500)),
                ('fecha_venta_anterior', models.DateField()),
                ('fecha_venta_nueva', models.DateField()),
                ('cuotas_anteriores', models.PositiveIntegerField()),
                ('cuotas_nuevas', models.PositiveIntegerField()),
                (
                    'fecha_vencimiento_anterior',
                    models.DateField(blank=True, null=True),
                ),
                (
                    'fecha_vencimiento_nueva',
                    models.DateField(blank=True, null=True),
                ),
                (
                    'valor_cuota_anterior',
                    models.DecimalField(decimal_places=2, max_digits=12),
                ),
                (
                    'valor_cuota_nueva',
                    models.DecimalField(decimal_places=2, max_digits=12),
                ),
                ('creada', models.DateTimeField(auto_now_add=True)),
                (
                    'usuario',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='ajustes_ventas_administrativos',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'venta',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='ajustes_administrativos',
                        to='Ventas.venta',
                    ),
                ),
            ],
            options={
                'ordering': ['-creada', '-id'],
            },
        ),
    ]
