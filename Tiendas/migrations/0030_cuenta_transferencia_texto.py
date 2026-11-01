from django.db import migrations, models


def copiar_cuenta(apps, schema_editor):
    Cuenta = apps.get_model('Tiendas', 'CuentaDestino')
    for cuenta in Cuenta.objects.using(schema_editor.connection.alias).all():
        campos = [cuenta.titular, cuenta.banco, cuenta.tipo, cuenta.numero]
        if all(campo.strip() for campo in campos):
            cuenta.datos_transferencia = '\n'.join(campo.strip() for campo in campos)
            cuenta.save(using=schema_editor.connection.alias, update_fields=['datos_transferencia'])


class Migration(migrations.Migration):
    dependencies = [('Tiendas', '0029_reporte_rutas_telegram')]
    operations = [
        migrations.AddField(model_name='cuentadestino', name='datos_transferencia',
            field=models.TextField(blank=True, default='')),
        migrations.RunPython(copiar_cuenta, migrations.RunPython.noop),
    ]
