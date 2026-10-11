from django.db import migrations, models


def recuperar_montos_verificables(apps, schema_editor):
    """No inventa una tarifa histórica ni modifica el ledger de ingresos."""
    Solicitud = apps.get_model('Tiendas', 'SolicitudPago')
    Pago = apps.get_model('Tiendas', 'PagoMembresia')
    alias = schema_editor.connection.alias
    for solicitud in Solicitud.objects.using(alias).filter(monto_acordado__isnull=True).iterator():
        montos = list(Pago.objects.using(alias).filter(solicitud_id=solicitud.pk)
            .values_list('monto', flat=True).distinct()[:2])
        if len(montos) == 1:
            Solicitud.objects.using(alias).filter(pk=solicitud.pk).update(monto_acordado=montos[0])


class Migration(migrations.Migration):
    dependencies = [('Tiendas', '0026_vencimiento_manual')]
    operations = [
        migrations.AddField(model_name='membresia', name='version',
            field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name='solicitudpago', name='monto_acordado',
            field=models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True)),
        migrations.RunPython(recuperar_montos_verificables, migrations.RunPython.noop),
    ]
