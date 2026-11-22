"""cuotas_del_dia: qué créditos entran en la base de cobranza de una fecha."""
from datetime import date
from decimal import Decimal
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from Clientes.models import Cliente
from Recaudos.models import Recaudo
from Tiendas.models import Tienda
from Ventas.cobranza_dia import cuotas_del_dia
from Ventas.models import Venta
from django.contrib.auth.models import User


class CuotasDelDiaTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user('admin-cobranza', is_staff=True)
        self.ruta = Tienda.objects.create(nombre='Ruta', administrador=admin, caja_inicial=0)
        self.cliente = Cliente.objects.create(tienda=self.ruta, identificacion='c1', nombres='C', apellidos='D',
                                              telefono_principal='1', direccion='x')

    def venta(self, saldo, **extra):
        datos = dict(tienda=self.ruta, cliente=self.cliente, fecha_venta=date(2026, 10, 1), valor_venta=1000,
                     interes=20, cuotas=20, plazo='Diario', saldo_actual=saldo,
                     estado_venta='Vigente' if saldo else 'Pagado', fecha_vencimiento=date(2026, 10, 21))
        datos.update(extra)
        return Venta.objects.create(**datos)

    def cobro(self, venta, fecha, valor):
        Recaudo.objects.create(tienda=self.ruta, venta=venta, fecha_recaudo=fecha, valor_recaudo=valor)

    def ids(self, fecha):
        return {v.pk: importe for v, importe in cuotas_del_dia(self.ruta, fecha)}

    def test_base_de_cobranza(self):
        vigente = self.venta(600)
        pagada_hoy = self.venta(0)                 # se pagó el mismo día: conserva su cuota
        self.cobro(pagada_hoy, date(2026, 10, 10), 1200)
        pagada_antes = self.venta(0)               # pagada antes de la fecha: fuera
        self.cobro(pagada_antes, date(2026, 10, 5), 1200)
        pagada_con_visita = self.venta(0)          # visita sin abono (valor 0) desde la fecha: fuera
        self.cobro(pagada_con_visita, date(2026, 10, 4), 1200)
        self.cobro(pagada_con_visita, date(2026, 10, 10), 0)
        perdida = self.venta(500, estado_venta='Perdida')
        futura = self.venta(1200, fecha_venta=date(2026, 10, 10))
        base = self.ids(date(2026, 10, 10))
        self.assertEqual(set(base), {vigente.pk, pagada_hoy.pk})
        self.assertEqual(base[vigente.pk], Decimal('60.00'))
        self.assertEqual(base[pagada_hoy.pk], Decimal('60.00'))
        self.assertNotIn(perdida.pk, base); self.assertNotIn(futura.pk, base)

    def test_una_sola_consulta(self):
        for _ in range(5):
            self.cobro(self.venta(0), date(2026, 10, 3), 1200)
        self.venta(600)
        with CaptureQueriesContext(connection) as q:
            list(cuotas_del_dia(self.ruta, date(2026, 10, 10)))
        self.assertEqual(len(q), 1)
