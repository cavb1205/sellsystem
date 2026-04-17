from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('Tiendas', '__latest__'),
        ('Trabajadores', '__latest__'),
    ]

    operations = [
        migrations.CreateModel(
            name='Publicidad',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fecha', models.DateField(auto_now_add=True)),
                ('hora', models.DateTimeField(auto_now_add=True)),
                ('latitud', models.DecimalField(decimal_places=7, max_digits=10)),
                ('longitud', models.DecimalField(decimal_places=7, max_digits=10)),
                ('precision_gps', models.FloatField(blank=True, null=True)),
                ('nota', models.CharField(blank=True, max_length=150, null=True)),
                ('tienda', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='publicidades', to='Tiendas.tienda')),
                ('trabajador', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='publicidades', to='Trabajadores.perfil')),
            ],
            options={
                'ordering': ['-hora'],
            },
        ),
    ]
