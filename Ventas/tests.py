from decimal import Decimal

from django.test import SimpleTestCase

from .riesgo import calcular_cuotas_atrasadas


class CuotasAtrasadasTests(SimpleTestCase):
    def test_abono_multiple_no_genera_atraso_negativo(self):
        self.assertEqual(
            calcular_cuotas_atrasadas(Decimal('18000'), 5, Decimal('270000')),
            Decimal('0.00'),
        )

    def test_abono_menor_al_ciclo_registrado_muestra_faltante(self):
        self.assertEqual(
            calcular_cuotas_atrasadas(Decimal('20000'), 4, Decimal('60000')),
            Decimal('1.00'),
        )
