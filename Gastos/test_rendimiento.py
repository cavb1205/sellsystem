"""Listados de gastos: cantidad de consultas constante (sin N+1)."""
from datetime import date
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from Tiendas import test_suscripciones as base
from Gastos.models import Gasto, Tipo_Gasto
from Trabajadores.models import Perfil


@override_settings(ROOT_URLCONF='Tiendas.test_suscripciones')
class ListadoGastosConsultasTests(TestCase):
    def setUp(self):
        base.SuscripcionesTests.setUp(self)
        self.api.force_authenticate(self.admin)
        self.perfil = Perfil.objects.filter(trabajador=self.worker).first() or \
            Perfil.objects.create(trabajador=self.worker, tienda=self.ruta)

    def crear(self, n):
        for i in range(n):
            tipo = Tipo_Gasto.objects.create(tipo_gasto=f'Tipo {Tipo_Gasto.objects.count()}')
            Gasto.objects.create(fecha=date(2026,10,5), tipo_gasto=tipo, valor=10+i,
                                 trabajador=self.perfil, tienda=self.ruta)

    def consultas(self, url):
        with CaptureQueriesContext(connection) as ctx:
            r = self.api.get(url)
        self.assertEqual(r.status_code, 200, r.data)
        return len(ctx), r.data

    def test_consultas_no_crecen_con_los_gastos(self):
        urls = [f'/gastos/t/{self.ruta.pk}/', f'/gastos/list/2026-10-05/t/{self.ruta.pk}/',
                f'/gastos/list/2026-10-01/2026-10-31/t/{self.ruta.pk}/']
        self.crear(2)
        antes = {u: self.consultas(u)[0] for u in urls}
        self.crear(8)
        for u in urls:
            total, data = self.consultas(u)
            self.assertEqual(total, antes[u], u)
            self.assertEqual(len(data), 10)
            self.assertTrue(all(isinstance(g['tipo_gasto'], dict) and g['tipo_gasto']['tipo_gasto'] for g in data))
