from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from Clientes.telegram_resumen import resumen_credito


class ResumenCreditoTests(TestCase):
    def venta(self, saldo='270', abonado='90', renovado='0'):
        movimientos = Mock()
        def queryset(**kw):
            return SimpleNamespace(
                aggregate=lambda **unused: {
                    'total': Decimal(renovado if kw['es_renovacion'] else abonado),
                    'ciclos': 0,
                },
                filter=lambda **unused: SimpleNamespace(
                    order_by=lambda *args: SimpleNamespace(first=lambda: None),
                ),
            )
        movimientos.filter.side_effect = queryset
        return SimpleNamespace(
            pk=1, tienda=SimpleNamespace(nombre='R1'),
            fecha_venta=date(2026, 9, 20), valor_venta=Decimal('300'),
            cuotas=12, plazo='Diario', saldo_actual=Decimal(saldo),
            total_a_pagar=lambda: Decimal('360'),
            valor_cuota=lambda: Decimal('30'), recaudo_set=movimientos,
        )

    def test_pago_parcial(self):
        datos = resumen_credito(self.venta())
        self.assertEqual(datos['progreso'], '25.00')
        self.assertEqual(datos['total_abonado'], '90')
        self.assertEqual(datos['saldo'], '270')

    def test_renovacion_no_equivale_a_pago(self):
        datos = resumen_credito(self.venta('0', '90', '270'))
        self.assertEqual(datos['progreso'], '25.00')
        self.assertEqual(datos['cierre'], 'renovacion')

    def test_inconsistencia_impide_publicar(self):
        with self.assertRaises(ValueError):
            resumen_credito(self.venta('200', '90'))

    def test_liquidacion_real(self):
        datos = resumen_credito(self.venta('0', '360'))
        self.assertEqual(datos['progreso'], '100.00')
        self.assertEqual(datos['cierre'], 'pagado')

    def test_no_expone_datos_internos(self):
        datos = resumen_credito(self.venta())
        for campo in ('identificacion', 'telefono', 'comentario', 'gps', 'url'):
            self.assertNotIn(campo, datos)
