from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Clientes', '0006_telegrammensaje'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramWorkerCursor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('clave', models.CharField(max_length=40, unique=True)),
                ('ultimo_vinculo_id', models.PositiveBigIntegerField(default=0)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddIndex(
            model_name='telegramevento',
            index=models.Index(fields=['estado', 'creado_en', 'id'],
                               name='tg_evento_retencion'),
        ),
    ]
