"""Interés por defecto declarado por la ruta (datos ficticios, SQLite, sin red)."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.test import APIClient

from Tiendas import test_suscripciones as fixtures
from Tiendas.models import Membresia, Tienda
from Tiendas.serializers import TiendaListaSerializer, TiendaSerializer
from Trabajadores.models import Perfil
from Trabajadores.views import register_user
from Ventas.models import Venta

urlpatterns = [*fixtures.urlpatterns, path('register/', register_user)]

CLAVE = 'Clave-Unica-Para-Prueba-2026!'


@override_settings(ROOT_URLCONF=__name__)
class InteresRutaTests(TestCase):
    def setUp(self):
        fixtures.SuscripcionesTests.setUp(self)
        Membresia.objects.create(nombre='Prueba', precio=0)
        self.api = APIClient()
        cache.clear()
        self.addCleanup(cache.clear)
        telegram = patch.multiple('Tiendas.telegram_bot',
                                  notificar_nueva_ruta=lambda *a, **k: None,
                                  notificar_nuevo_usuario=lambda *a, **k: None)
        telegram.start()
        self.addCleanup(telegram.stop)

    def request(self, user, method, url, data=None):
        self.api.force_authenticate(user)
        return getattr(self.api, method)(url, data or {}, format='json')

    # --- Creación de rutas -------------------------------------------------

    def test_nueva_ruta_exige_interes_y_aceptacion(self):
        casos = [
            ({}, 'interes_defecto'),
            ({'interes_defecto': ''}, 'interes_defecto'),
            ({'interes_defecto': 101, 'acepta_responsabilidad_interes': True}, 'interes_defecto'),
            ({'interes_defecto': -1, 'acepta_responsabilidad_interes': True}, 'interes_defecto'),
            ({'interes_defecto': 12.5, 'acepta_responsabilidad_interes': True}, 'interes_defecto'),
            ({'interes_defecto': 20}, 'acepta_responsabilidad_interes'),
            ({'interes_defecto': 20, 'acepta_responsabilidad_interes': False}, 'acepta_responsabilidad_interes'),
        ]
        for extra, campo in casos:
            with self.subTest(extra=extra):
                res = self.request(self.admin, 'post', '/tiendas/create/',
                                   {'nombre': 'Ruta sin tasa', **extra})
                self.assertEqual(res.status_code, 400, res.data)
                self.assertIn(campo, res.data)
        self.assertFalse(Tienda.objects.filter(nombre='Ruta sin tasa').exists())

    def test_nueva_ruta_guarda_tasa_y_quien_la_declaro(self):
        res = self.request(self.admin, 'post', '/tiendas/create/', {
            'nombre': 'Ruta con tasa', 'interes_defecto': 15,
            'acepta_responsabilidad_interes': True})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertNotIn('acepta_responsabilidad_interes', res.data)
        ruta = Tienda.objects.get(nombre='Ruta con tasa')
        self.assertEqual(ruta.interes_defecto, 15)
        self.assertEqual(ruta.interes_aceptado_por, self.admin)
        self.assertIsNotNone(ruta.interes_aceptado_en)

    def test_root_que_crea_ruta_ajena_queda_como_quien_acepto(self):
        res = self.request(self.root, 'post', '/tiendas/create/', {
            'nombre': 'Ruta creada por root', 'administrador': self.admin.pk,
            'interes_defecto': 0, 'acepta_responsabilidad_interes': True})
        self.assertEqual(res.status_code, 200, res.data)
        ruta = Tienda.objects.get(nombre='Ruta creada por root')
        self.assertEqual((ruta.administrador, ruta.interes_aceptado_por, ruta.interes_defecto),
                         (self.admin, self.root, 0))

    def test_registro_sin_tasa_no_deja_usuario_huerfano(self):
        antes = User.objects.count()
        self.api.force_authenticate(None)
        res = self.api.post('/register/', {'username': 'registro-ficticio', 'password': CLAVE,
                                           'nombre_ruta': 'Negocio ficticio'}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('interes_defecto', res.data)
        self.assertEqual(User.objects.count(), antes)
        self.assertFalse(Tienda.objects.filter(nombre='Negocio ficticio').exists())

    def test_registro_con_tasa_crea_ruta_con_aceptacion(self):
        self.api.force_authenticate(None)
        res = self.api.post('/register/', {
            'username': 'registro-ficticio', 'password': CLAVE,
            'nombre_ruta': 'Negocio ficticio', 'interes_defecto': 20,
            'acepta_responsabilidad_interes': True}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        usuario = User.objects.get(username='registro-ficticio')
        ruta = Tienda.objects.get(nombre='Negocio ficticio')
        self.assertEqual((ruta.interes_defecto, ruta.interes_aceptado_por), (20, usuario))
        self.assertTrue(Perfil.objects.filter(trabajador=usuario, tienda=ruta).exists())

    # --- Rutas existentes y configuración ---------------------------------

    def test_rutas_existentes_quedan_sin_tasa_y_no_pueden_activarla(self):
        self.assertIsNone(self.ruta.interes_defecto)
        res = self.request(self.admin, 'patch', f'/tiendas/{self.ruta.pk}/settings/', {
            'interes_defecto': 10, 'acepta_responsabilidad_interes': True})
        self.assertEqual(res.status_code, 400, res.data)
        self.ruta.refresh_from_db()
        self.assertIsNone(self.ruta.interes_defecto)

    def test_cambiar_tasa_exige_aceptacion_y_la_registra(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(interes_defecto=20)
        res = self.request(self.admin, 'patch', f'/tiendas/{self.ruta.pk}/settings/',
                           {'interes_defecto': 10, 'telefono': '111'})
        self.assertEqual(res.status_code, 400, res.data)
        self.ruta.refresh_from_db()
        self.assertEqual((self.ruta.interes_defecto, self.ruta.telefono), (20, None))

        res = self.request(self.admin, 'patch', f'/tiendas/{self.ruta.pk}/settings/', {
            'interes_defecto': 10, 'acepta_responsabilidad_interes': True})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['interes_defecto'], 10)
        self.ruta.refresh_from_db()
        self.assertEqual((self.ruta.interes_defecto, self.ruta.interes_aceptado_por), (10, self.admin))

    def test_trabajador_no_cambia_tasa_de_la_ruta(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(interes_defecto=20)
        res = self.request(self.worker, 'patch', f'/tiendas/{self.ruta.pk}/settings/', {
            'interes_defecto': 50, 'acepta_responsabilidad_interes': True})
        self.assertEqual(res.status_code, 403, res.data)
        self.ruta.refresh_from_db()
        self.assertEqual(self.ruta.interes_defecto, 20)

    def test_put_de_ruta_no_cambia_la_tasa(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(interes_defecto=20)
        res = self.request(self.admin, 'put', f'/tiendas/{self.ruta.pk}/update/',
                           {'nombre': 'Ruta A', 'interes_defecto': 90})
        self.assertEqual(res.status_code, 200, res.data)
        self.ruta.refresh_from_db()
        self.assertEqual(self.ruta.interes_defecto, 20)

    def test_la_ruta_expone_su_tasa(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(interes_defecto=18)
        self.ruta.refresh_from_db()
        self.assertEqual(TiendaSerializer(self.ruta).data['interes_defecto'], 18)
        self.assertEqual(TiendaListaSerializer(self.ruta).data['interes_defecto'], 18)
        self.assertIsNone(TiendaSerializer(self.otra).data['interes_defecto'])

    # --- Créditos ----------------------------------------------------------

    def datos_venta(self, **extra):
        return {'fecha_venta': '2026-10-10', 'cliente': self.cliente.pk,
                'valor_venta': '1000', 'cuotas': 20, 'plazo': 'Diario', **extra}

    def test_trabajador_solo_usa_la_tasa_de_la_ruta(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(interes_defecto=12)
        url = f'/ventas/create/t/{self.ruta.pk}/'
        for interes in (20, '13', 'abc'):
            with self.subTest(interes=interes):
                res = self.request(self.worker, 'post', url, self.datos_venta(interes=interes))
                self.assertEqual(res.status_code, 403, res.data)
        self.assertEqual(Venta.objects.filter(tienda=self.ruta).count(), 1)

        res = self.request(self.worker, 'post', url, self.datos_venta(interes=12))
        self.assertEqual(res.status_code, 200, res.data)
        res = self.request(self.worker, 'post', url, self.datos_venta())
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['interes'], 12)
        self.assertEqual(res.data['saldo_actual'], '1120.00')

    def test_admin_puede_usar_otra_tasa(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(interes_defecto=12)
        res = self.request(self.admin, 'post', f'/ventas/create/t/{self.ruta.pk}/',
                           self.datos_venta(interes=25))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['interes'], 25)

    def test_ruta_sin_tasa_conserva_el_comportamiento_anterior(self):
        res = self.request(self.worker, 'post', f'/ventas/create/t/{self.ruta.pk}/',
                           self.datos_venta(interes=20))
        self.assertEqual(res.status_code, 200, res.data)

    def test_trabajador_no_renueva_con_otra_tasa(self):
        Tienda.objects.filter(pk=self.ruta.pk).update(interes_defecto=12)
        res = self.request(self.worker, 'post', f'/ventas/{self.venta.pk}/renovar/t/{self.ruta.pk}/',
                           {'fecha_venta': '2026-10-10', 'interes': 20, 'cuotas': 20})
        self.assertEqual(res.status_code, 403, res.data)
        self.venta.refresh_from_db()
        self.assertEqual(self.venta.estado_venta, 'Vigente')
