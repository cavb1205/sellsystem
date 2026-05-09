from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0011_solicitudpago_tienda_membresia_pre_activada'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='tienda',
            constraint=models.UniqueConstraint(
                fields=['nombre', 'administrador'],
                name='unique_tienda_nombre_por_admin'
            ),
        ),
    ]
