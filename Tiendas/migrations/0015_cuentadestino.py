from django.db import migrations, models


def seed_cuenta(apps, schema_editor):
    """Siembra el registro único con los valores actuales de settings (variables.py)."""
    from django.conf import settings
    CuentaDestino = apps.get_model('Tiendas', 'CuentaDestino')
    CuentaDestino.objects.get_or_create(
        pk=1,
        defaults={
            'banco': getattr(settings, 'CUENTA_DESTINO_BANCO', '') or '',
            'numero': getattr(settings, 'CUENTA_DESTINO_NUMERO', '') or '',
            'titular': getattr(settings, 'CUENTA_DESTINO_TITULAR', '') or '',
            'tipo': getattr(settings, 'CUENTA_DESTINO_TIPO', '') or '',
        },
    )


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0014_solicitudpago_fecha_vencimiento_previa'),
    ]

    operations = [
        migrations.CreateModel(
            name='CuentaDestino',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('banco', models.CharField(blank=True, max_length=100)),
                ('numero', models.CharField(blank=True, max_length=100)),
                ('titular', models.CharField(blank=True, max_length=100)),
                ('tipo', models.CharField(blank=True, max_length=100)),
                ('actualizada', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.RunPython(seed_cuenta, unseed),
    ]
