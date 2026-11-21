"""Índice (tienda, fecha_recaudo): migración escrita a mano coherente con el modelo."""
from io import StringIO
from django.core.management import call_command
from django.db import connection
from django.test import TestCase


class IndiceRecaudosTests(TestCase):
    def test_migraciones_al_dia_con_el_modelo(self):
        salida = StringIO()
        try:
            call_command('makemigrations', 'Recaudos', '--check', '--dry-run', stdout=salida)
        except SystemExit:
            self.fail('Recaudos tiene cambios sin migración:\n' + salida.getvalue())

    def test_indice_creado(self):
        with connection.cursor() as c:
            restricciones = connection.introspection.get_constraints(c, 'Recaudos_recaudo')
        indice = restricciones.get('recaudo_tienda_fecha_idx')
        self.assertIsNotNone(indice)
        self.assertEqual(indice['columns'], ['tienda_id', 'fecha_recaudo'])
