"""Listado paginado de gastos: filtros, totales del filtro completo y compatibilidad."""
from datetime import date
from decimal import Decimal
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from Tiendas import test_suscripciones as base
from Gastos.models import Gasto, Tipo_Gasto
from Trabajadores.models import Perfil


@override_settings(ROOT_URLCONF='Tiendas.test_suscripciones')
class GastosPaginadosTests(TestCase):
    def setUp(self):
        base.SuscripcionesTests.setUp(self)
        self.api.force_authenticate(self.admin)
        self.perfil = Perfil.objects.get(trabajador=self.worker)
        self.luz = Tipo_Gasto.objects.create(tipo_gasto='Luz')
        self.gas = Tipo_Gasto.objects.create(tipo_gasto='Gasolina')
        # 25 gastos: días 1..25 de octubre; pares = Gasolina; valor = día * 10.
        for d in range(1, 26):
            Gasto.objects.create(fecha=date(2026, 10, d), tipo_gasto=self.gas if d % 2 == 0 else self.luz,
                                 valor=d * 10, trabajador=self.perfil, tienda=self.ruta)
        Gasto.objects.create(fecha=date(2026, 10, 5), tipo_gasto=self.luz, valor=999,
                             trabajador=self.perfil, tienda=self.otra)  # otra ruta: nunca aparece
        self.url = f'/gastos/t/{self.ruta.pk}/'

    def get(self, qs=''):
        return self.api.get(self.url + qs)

    def test_primera_pagina_y_totales(self):
        r = self.get('?page=1&limit=10')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['total_registros'], 25)
        self.assertEqual(r.data['total_monto'], f'{sum(d * 10 for d in range(1, 26))}.00')
        self.assertEqual((r.data['pagina'], r.data['paginas'], r.data['limite']), (1, 3, 10))
        self.assertEqual(len(r.data['resultados']), 10)
        ids = [g['id'] for g in r.data['resultados']]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(r.data['resultados'][0]['tipo_gasto']['tipo_gasto'], 'Luz')

    def test_ultima_pagina_y_pagina_fuera_de_rango(self):
        self.assertEqual(len(self.get('?page=3&limit=10').data['resultados']), 5)
        r = self.get('?page=99&limit=10')
        self.assertEqual((r.data['pagina'], len(r.data['resultados'])), (3, 5))

    def test_filtros_fecha_y_tipo(self):
        r = self.get(f'?page=1&limit=100&desde=2026-10-10&hasta=2026-10-20&tipo={self.gas.pk}')
        dias = sorted(int(g['fecha'][-2:]) for g in r.data['resultados'])
        self.assertEqual(dias, [10, 12, 14, 16, 18, 20])
        self.assertEqual(r.data['total_registros'], 6)
        self.assertEqual(Decimal(r.data['total_monto']), Decimal(sum(dias) * 10))

    def test_sin_resultados(self):
        r = self.get('?page=1&desde=2027-01-01')
        self.assertEqual((r.data['total_registros'], r.data['total_monto'], r.data['paginas'], r.data['resultados']), (0, '0.00', 1, []))

    def test_parametros_invalidos(self):
        for qs in ('?page=0', '?page=x', '?page=1&limit=0', '?page=1&limit=101', '?page=1&desde=10-10-2026', '?page=1&tipo=abc'):
            self.assertEqual(self.get(qs).status_code, 400, qs)

    def test_sin_page_mantiene_respuesta_historica(self):
        r = self.get()
        self.assertIsInstance(r.data, list)
        self.assertEqual(len(r.data), 25)

    def test_consultas_constantes(self):
        with CaptureQueriesContext(connection) as q:
            self.get('?page=1&limit=25')
        with CaptureQueriesContext(connection) as q2:
            self.get('?page=2&limit=5')
        self.assertEqual(len(q), len(q2))

    def test_otra_ruta_sin_acceso(self):
        self.api.force_authenticate(self.worker)
        self.assertEqual(self.api.get(f'/gastos/t/{self.otra.pk}/?page=1').status_code, 403)
