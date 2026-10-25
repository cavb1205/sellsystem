from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from Tiendas import test_suscripciones as fixtures
from Trabajadores.models import Perfil
from Trabajadores.serializers import UserCreateSerializer
from Trabajadores.views import register_user
from unittest.mock import patch
from django.urls import path

urlpatterns=[*fixtures.urlpatterns,path('register/',register_user)]


@override_settings(ROOT_URLCONF=__name__)
class PasswordPolicyTests(TestCase):
    def setUp(self):
        fixtures.SuscripcionesTests.setUp(self)
        Perfil.objects.create(trabajador=self.admin,tienda=self.ruta)
        self.api=APIClient();self.api.force_authenticate(self.admin)
        cache.clear();self.addCleanup(cache.clear)

    def test_password_debil_no_cambia_la_clave_vigente(self):
        before=self.worker.password
        for password in ['12345678','abcdefgh','worker-pruebas']:
            result=self.api.post(f'/trabajadores/password/{self.worker.perfil.pk}/',
                {'passwordNuevo':password},format='json')
            self.assertEqual(result.status_code,400,result.data)
            self.assertIn('contraseña',result.data['error'])
            self.worker.refresh_from_db();self.assertEqual(self.worker.password,before)

    def test_serializer_de_cuentas_rechaza_claves_comunes_y_no_serializa_password(self):
        weak=UserCreateSerializer(data={'username':'cuenta-ficticia','password':'12345678'})
        self.assertFalse(weak.is_valid())
        self.assertIn('password',weak.errors)
        strong=UserCreateSerializer(data={'username':'cuenta-ficticia',
            'password':'Clave-Unica-Para-Prueba-2026!','first_name':'Prueba'})
        self.assertTrue(strong.is_valid(),strong.errors)
        user=strong.save()
        user.refresh_from_db()
        self.assertTrue(user.check_password('Clave-Unica-Para-Prueba-2026!'))
        self.assertNotEqual(user.password,'Clave-Unica-Para-Prueba-2026!')
        self.assertNotIn('password',strong.data)

    def test_registro_y_alta_de_worker_debiles_no_crean_usuarios(self):
        before=User.objects.count()
        result=self.api.post(f'/trabajadores/create/t/{self.ruta.pk}/',
            {'username':'nuevo-ficticio','password':'12345678','first_name':'Prueba',
             'last_name':'Local','identificacion':'doc-prueba','telefono':'000','direccion':'Prueba'},format='json')
        self.assertEqual(result.status_code,400,result.data)
        self.api.force_authenticate(None)
        result=self.api.post('/register/',{'username':'nuevo-ficticio','password':'12345678',
                                         'nombre_ruta':'No crear'},format='json')
        self.assertEqual(result.status_code,400,result.data)
        self.assertEqual(User.objects.count(),before)
