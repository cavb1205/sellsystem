from datetime import datetime, timezone
from types import SimpleNamespace

from django.test import SimpleTestCase

from .fecha_operativa import fecha_operativa


class FechaOperativaTests(SimpleTestCase):
    def test_resuelve_el_dia_segun_la_zona_de_la_ruta(self):
        instante = datetime(2026, 9, 13, 2, 30, tzinfo=timezone.utc)
        chile = SimpleNamespace(zona_horaria='America/Santiago')
        colombia = SimpleNamespace(zona_horaria='America/Bogota')

        self.assertEqual(fecha_operativa(chile, instante).isoformat(), '2026-09-12')
        self.assertEqual(fecha_operativa(colombia, instante).isoformat(), '2026-09-12')

    def test_un_cambio_de_hora_puede_cambiar_el_dia_operativo(self):
        instante = datetime(2026, 9, 13, 4, 30, tzinfo=timezone.utc)
        chile = SimpleNamespace(zona_horaria='America/Santiago')

        self.assertEqual(fecha_operativa(chile, instante).isoformat(), '2026-09-13')
