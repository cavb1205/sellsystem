from django.test import TestCase

from Clientes import test_telegram_vinculacion as fixtures
from Clientes.telegram_cursor import avanzar, obtener_lote


class TelegramCursorTests(TestCase):
    def setUp(self):
        fixtures.VinculacionTests.setUp(self)
        self.vinculos = [
            fixtures.VinculacionTests.vincular(self, cliente, chat=12345 + indice)
            for indice, cliente in enumerate(self.clientes)
        ]

    def test_reparte_vinculos_y_da_la_vuelta_sin_repetir_dentro_del_lote(self):
        ids = [vinculo.pk for vinculo in self.vinculos]
        self.assertEqual(obtener_lote(20), ids)
        avanzar(ids[0])
        self.assertEqual(obtener_lote(1), ids[1:])
        avanzar(ids[1])
        self.assertEqual(obtener_lote(20), ids)

    def test_omite_vinculos_inactivos_y_rechaza_limite_invalido(self):
        self.vinculos[0].activo = False
        self.vinculos[0].save(update_fields=['activo'])
        self.assertEqual(obtener_lote(20), [self.vinculos[1].pk])
        with self.assertRaises(ValueError):
            obtener_lote(0)
