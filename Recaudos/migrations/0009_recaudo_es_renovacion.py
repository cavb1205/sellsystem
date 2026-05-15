from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Recaudos', '0008_add_gps_fields_to_recaudo'),
    ]

    operations = [
        migrations.AddField(
            model_name='recaudo',
            name='es_renovacion',
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
