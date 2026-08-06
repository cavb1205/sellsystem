import re

from django.db import migrations, models


def clasificar_alertas_preventivas(apps, schema_editor):
    AlertaOperativa = apps.get_model('Tiendas', 'AlertaOperativa')
    Venta = apps.get_model('Ventas', 'Venta')
    venta_ids = set(
        AlertaOperativa.objects.filter(tipo='RIESGO_CARTERA')
        .values_list('venta_id_ref', flat=True)
    )
    ventas_vigentes = set(
        Venta.objects.filter(id__in=venta_ids, estado_venta='Vigente')
        .values_list('id', flat=True)
    )

    for alerta in AlertaOperativa.objects.filter(tipo='RIESGO_CARTERA').iterator():
        if alerta.venta_id_ref not in ventas_vigentes:
            continue
        datos = alerta.datos if isinstance(alerta.datos, dict) else {}
        try:
            nivel = int(datos.get('nivel_cobranza', datos.get('nivel', 0)))
            cuotas_atrasadas = float(datos['cuotas_atrasadas'])
        except (KeyError, TypeError, ValueError):
            continue

        if nivel != 1 or cuotas_atrasadas > 0:
            continue

        plazo = datos.get('plazo') or 'Diario'
        umbral = datos.get('umbral_alerta') or {
            'Diario': 1,
            'Semanal': 7,
            'Mensual': 30,
        }.get(plazo, 1)
        unidad = 'día' if umbral == 1 else 'días'
        detalle = alerta.detalle or ''
        detalle = re.sub(
            r'⚠️ <b>Gestión de cartera:[^<]*</b>',
            f'🟡 <b>Seguimiento preventivo: ciclo {str(plazo).lower()} pendiente</b>',
            detalle,
            count=1,
        )
        detalle = re.sub(
            r'📌 Umbral de gestión cruzado:.*$',
            f'📌 No registra cuota vencida; pasó el intervalo esperado de {umbral} {unidad}. '
            'Verificar si corresponde cobrar hoy.',
            detalle,
            count=1,
            flags=re.MULTILINE,
        )
        datos['clasificacion'] = 'seguimiento_preventivo'
        nueva_clave = alerta.clave_dedupe.replace(
            'riesgo_cartera:', 'seguimiento_preventivo:', 1,
        )
        if nueva_clave == alerta.clave_dedupe:
            nueva_clave = f'seguimiento_preventivo:historica:{alerta.pk}'
        if AlertaOperativa.objects.exclude(pk=alerta.pk).filter(
            clave_dedupe=nueva_clave,
        ).exists():
            nueva_clave = f'{nueva_clave}:historica:{alerta.pk}'

        alerta.tipo = 'SEGUIMIENTO_PREVENTIVO'
        alerta.clave_dedupe = nueva_clave
        alerta.titulo = f'Venta #{alerta.venta_id_ref} requiere seguimiento preventivo'
        alerta.detalle = detalle
        alerta.datos = datos
        alerta.save(update_fields=['tipo', 'clave_dedupe', 'titulo', 'detalle', 'datos'])


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0021_alertaoperativa_sin_primer_abono'),
        ('Ventas', '0011_ajusteventaadministrativo'),
    ]

    operations = [
        migrations.AlterField(
            model_name='alertaoperativa',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('VENTA_CON_ALERTAS', 'Venta con señales operativas'),
                    ('RIESGO_CARTERA', 'Riesgo de cartera'),
                    ('SEGUIMIENTO_PREVENTIVO', 'Seguimiento preventivo'),
                    ('SIN_PRIMER_ABONO', 'Crédito sin primer abono'),
                    ('CIERRE_AUSENTE', 'Cierre de caja ausente'),
                    ('RESUMEN_OPERATIVO', 'Resumen operativo'),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
        migrations.RunPython(clasificar_alertas_preventivas, migrations.RunPython.noop),
    ]
