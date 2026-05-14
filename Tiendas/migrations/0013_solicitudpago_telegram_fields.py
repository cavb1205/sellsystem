from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('Tiendas', '0012_unique_tienda_nombre_por_admin'),
    ]

    operations = [
        migrations.AlterField(
            model_name='solicitudpago',
            name='estado',
            field=models.CharField(
                choices=[
                    ('pendiente', 'Esperando comprobante'),
                    ('pendiente_confirmacion', 'Pre-activada — esperando confirmación'),
                    ('procesando', 'Validando'),
                    ('aprobada', 'Aprobada'),
                    ('confirmada', 'Confirmada'),
                    ('pre_aprobada', 'Pre-aprobada (revisión manual)'),
                    ('rechazada', 'Rechazada'),
                    ('expirada', 'Expirada'),
                ],
                default='pendiente',
                max_length=25,
            ),
        ),
        migrations.AddField(
            model_name='solicitudpago',
            name='comprobante',
            field=models.ImageField(blank=True, null=True, upload_to='comprobantes/%Y/%m/'),
        ),
        migrations.AddField(
            model_name='solicitudpago',
            name='solicitada_por',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='solicitudes_pago_creadas',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='solicitudpago',
            name='telegram_message_id',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name='solicitudpago',
            name='revisada_por',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='solicitudes_pago_revisadas',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
