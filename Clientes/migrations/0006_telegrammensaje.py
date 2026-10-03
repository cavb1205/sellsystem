from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Clientes', '0005_telegraminvitacion_telegramvinculo_telegramevento'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramMensaje',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('chat_id', models.BigIntegerField()),
                ('mensaje_id', models.BigIntegerField(blank=True, null=True)),
                ('direccion', models.CharField(choices=[('entrante', 'Entrante'),
                                                        ('saliente', 'Saliente')],
                                               max_length=10)),
                ('registrado_en', models.DateTimeField(auto_now_add=True)),
                ('intentos', models.PositiveSmallIntegerField(default=0)),
                ('proximo_intento', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'indexes': [models.Index(fields=['registrado_en', 'id'],
                                         name='tg_mensaje_limpieza_idx')],
            },
        ),
        migrations.AddConstraint(
            model_name='telegrammensaje',
            constraint=models.UniqueConstraint(fields=('chat_id', 'mensaje_id'),
                                               name='tg_mensaje_chat_id_unico'),
        ),
    ]
