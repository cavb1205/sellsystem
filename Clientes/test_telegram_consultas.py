from datetime import date

from django.test import TestCase

from Clientes import test_telegram_vinculacion as fixtures
from Clientes.telegram_consultas import (
    creditos_activos, detalle_credito, menu_principal, movimientos_recientes,
)
from Recaudos.models import Recaudo
from Ventas.models import Venta


class ConsultasTests(TestCase):
    def setUp(self):
        fixtures.VinculacionTests.setUp(self)
        fixtures.VinculacionTests.vincular(self, self.clientes[0], chat=777)
        self.venta = Venta.objects.create(
            cliente=self.clientes[0], tienda=self.clientes[0].tienda,
            fecha_venta=date(2026, 9, 22), valor_venta=100,
            interes=20, cuotas=20, saldo_actual=120,
        )

    def test_menu_y_credito_muestran_primer_nombre_sin_ruta(self):
        texto, botones = menu_principal(777)
        self.assertIn('Mis créditos activos', texto + str(botones))
        texto, botones = creditos_activos(777)
        self.assertIn('👤 Cliente: Camilo', texto)
        self.assertIn('📊 Cuotas cubiertas: 0 de 20', texto)
        self.assertNotIn('Ruta:', texto)
        self.assertTrue(any('tgmenu:credito:' in boton['callback_data']
                            for fila in botones for boton in fila))

    def test_detalle_y_movimientos_son_solo_lectura(self):
        self.venta.estado_venta = 'Atrasado'
        self.venta.fecha_vencimiento = date(2026, 10, 20)
        self.venta.save(update_fields=['estado_venta', 'fecha_vencimiento'])
        texto, _ = detalle_credito(777, self.venta.pk)
        self.assertIn(f'Crédito #{self.venta.pk}', texto)
        self.assertIn('Cliente: Camilo', texto)
        self.assertIn('Estado: 🟠 Con atraso', texto)
        self.assertIn('Vencimiento: 20/10/2026', texto)
        self.assertNotIn('Ruta:', texto)

        Recaudo.objects.create(
            venta=self.venta, tienda=self.venta.tienda,
            fecha_recaudo=date(2026, 9, 22), valor_recaudo=6,
        )
        texto, _ = movimientos_recientes(777)
        self.assertIn('Camilo', texto)
        self.assertIn('Abono: $6', texto)
        self.assertNotIn('Ruta:', texto)

    def test_chat_no_vinculado_no_recibe_datos(self):
        texto, botones = creditos_activos(999)
        self.assertIn('No tienes créditos activos', texto)
        self.assertNotIn('Camilo', texto)
        self.assertEqual(botones[0][0]['callback_data'], 'tgmenu:menu')
