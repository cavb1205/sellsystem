"""Contrato de publicidad, roles y conservación histórica; BD aislada."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
from django.apps import apps
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from rest_framework.test import APIClient
from Publicidad.models import Publicidad
from Tiendas.models import Tienda, MovimientoCaja
from Trabajadores.models import Perfil, AsignacionTrabajador

urlpatterns = [path('publicidad/', include('Publicidad.urls'))]


@override_settings(ROOT_URLCONF=__name__)
class PublicidadTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin-local', is_staff=True, first_name='Administrador')
        self.ajeno = User.objects.create_user('ajeno', is_staff=True)
        self.ruta = Tienda.objects.create(nombre='Ruta A', administrador=self.admin, zona_horaria='America/Santiago')
        self.otra = Tienda.objects.create(nombre='Ruta ajena', administrador=self.ajeno)
        self.worker = User.objects.create_user('operador-a')
        self.companero = User.objects.create_user('operador-b')
        self.perfil = Perfil.objects.create(trabajador=self.worker, tienda=self.ruta, identificacion='1', telefono='000')
        self.perfil_b = Perfil.objects.create(trabajador=self.companero, tienda=self.ruta, identificacion='2', telefono='000')
        self.api = APIClient()
        # UTC 10/10, pero todavía 09/10 en Santiago: manda la fecha de la ruta.
        self.reloj = patch('Tiendas.fecha_operativa.timezone.now', return_value=datetime(2026,10,10,1,tzinfo=timezone.utc))
        self.reloj.start(); self.addCleanup(self.reloj.stop)
        self.red = patch('requests.sessions.Session.request', side_effect=AssertionError('Red externa prohibida'))
        self.red.start(); self.addCleanup(self.red.stop)

    def registrar(self, user=None, ruta=None, **extra):
        self.api.force_authenticate(user or self.worker)
        return self.api.post(f'/publicidad/create/t/{(ruta or self.ruta).pk}/',
            dict(latitud=-22.45, longitud=-68.9, precision_gps=15, nota='Local', **extra), format='json')

    def punto(self, perfil=None, **extra):
        perfil = perfil or self.perfil
        datos = dict(tienda=self.ruta, trabajador=perfil, registrado_por=perfil.trabajador,
            autor_nombre=perfil.trabajador.username, autor_rol='Trabajador', fecha=date(2026,10,9),
            latitud=-22.4, longitud=-68.9, precision_gps=10)
        datos.update(extra)
        return Publicidad.objects.create(**datos)

    def lista(self, user=None, inicio='2026-10-09', fin='2026-10-09', ruta=None, propios=False):
        self.api.force_authenticate(user or self.worker)
        return self.api.get(f'/publicidad/rango/{inicio}/{fin}/t/{(ruta or self.ruta).pk}/', {'propios':'1'} if propios else {})

    def test_admin_sin_perfil_y_worker_autoria_real_fecha_no_manipulable(self):
        for user, rol in [(self.admin,'Administrador'),(self.worker,'Trabajador')]:
            res = self.registrar(user, registrado_por=self.ajeno.pk, autor_nombre='Falso', autor_rol='Falso', fecha='2000-01-01', tienda=self.otra.pk)
            self.assertEqual(res.status_code, 201, res.data)
            self.assertEqual(res.data['registrado_por'], user.pk)
            self.assertEqual(res.data['autor_rol'], rol)
            self.assertEqual(res.data['fecha'], '2026-10-09')
            self.assertEqual(res.data['tienda'], self.ruta.pk)
        self.assertIsNone(Publicidad.objects.get(registrado_por=self.admin).trabajador_id)
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_admin_con_perfil_no_se_disfraza_como_worker(self):
        Perfil.objects.create(trabajador=self.admin, tienda=self.ruta, identificacion='3', telefono='000')
        self.assertEqual(self.registrar(self.admin).data['autor_rol'], 'Administrador')

    def test_worker_ve_equipo_historia_y_solo_propios_sin_hr(self):
        propio = self.punto()
        otro = self.punto(self.perfil_b)
        ayer = self.punto(fecha=date(2026,10,8))
        data = self.lista(inicio='2026-10-08').data
        self.assertEqual({p['id'] for p in data}, {propio.pk,otro.pk,ayer.pk})
        self.assertEqual({p['id'] for p in self.lista(inicio='2026-10-08', propios=True).data}, {propio.pk,ayer.pk})
        permisos = {p['id']:p['permite_eliminar'] for p in data}
        self.assertEqual(permisos, {propio.pk:True, otro.pk:False, ayer.pk:False})
        self.assertTrue(all(p['permite_eliminar'] for p in self.lista(self.admin,inicio='2026-10-08').data))

    def test_rutas_ajenas_y_asignaciones_activas(self):
        for user in [self.worker,self.admin,self.ajeno]:
            ruta = self.ruta if user == self.ajeno else self.otra
            self.assertEqual(self.lista(user,ruta=ruta).status_code,403)
            self.assertEqual(self.registrar(user,ruta=ruta).status_code,403)
        adicional = Tienda.objects.create(nombre='Ruta adicional', administrador=self.admin)
        AsignacionTrabajador.objects.create(perfil=self.perfil, tienda=self.ruta)
        asignacion = AsignacionTrabajador.objects.create(perfil=self.perfil, tienda=adicional)
        self.assertEqual(self.lista(ruta=adicional).status_code,200)
        self.assertEqual(self.registrar(ruta=adicional).status_code,201)
        asignacion.activa=False; asignacion.save()
        self.assertEqual(self.lista(ruta=adicional).status_code,403)

    def test_rango_estricto_y_limite_no_trunca(self):
        for inicio,fin in [('2026-02-30','2026-10-09'),('20261009','2026-10-09'),('2026-10-09','2026-10-08'),('2026-10-09','2026-10-10'),('2026-07-01','2026-10-09')]:
            self.assertEqual(self.lista(inicio=inicio,fin=fin).status_code,400)
        self.assertEqual(self.lista(inicio='2026-07-09').status_code,200) # 93 días inclusive.
        Publicidad.objects.bulk_create([Publicidad(tienda=self.ruta, fecha=date(2026,10,9), latitud=0,longitud=0) for _ in range(5001)])
        res = self.lista()
        self.assertEqual(res.status_code,400)
        self.assertIn('5.000',res.data['error'])

    def test_gps_valido_y_precision_finita(self):
        self.api.force_authenticate(self.worker)
        base = dict(latitud=0,longitud=0,precision_gps=0)
        url=f'/publicidad/create/t/{self.ruta.pk}/'
        self.assertEqual(self.api.post(url,base,format='json').status_code,201)
        for campo,valor in [('latitud',91),('longitud',-181),('latitud',None),('latitud',''),('precision_gps',-1),('precision_gps','NaN'),('precision_gps','Infinity')]:
            self.assertEqual(self.api.post(url,{**base,campo:valor},format='json').status_code,400,(campo,valor))

    def test_reintento_nonce_conflicto_y_compatibilidad_legacy(self):
        nonce=str(uuid4())
        primero=self.registrar(solicitud_id=nonce)
        segundo=self.registrar(solicitud_id=nonce)
        self.assertEqual((primero.status_code,segundo.status_code),(201,200))
        self.assertEqual(primero.data['id'],segundo.data['id'])
        self.assertEqual(Publicidad.objects.count(),1)
        self.assertEqual(self.registrar(self.companero,solicitud_id=nonce).status_code,409)
        self.api.force_authenticate(self.worker)
        conflicto=self.api.post(f'/publicidad/create/t/{self.ruta.pk}/',dict(latitud=0,longitud=0,nota='Otra',solicitud_id=nonce),format='json')
        self.assertEqual(conflicto.status_code,409)
        self.assertEqual(self.registrar().status_code,201) # cliente antiguo sin nonce.

    def test_eliminar_solo_propios_hoy_admin_historia_y_ruta(self):
        hoy=self.punto(); ayer=self.punto(fecha=date(2026,10,8)); ajeno=self.punto(self.perfil_b)
        self.api.force_authenticate(self.worker)
        for punto in [ayer,ajeno]:
            self.assertEqual(self.api.delete(f'/publicidad/{punto.pk}/delete/').status_code,403)
        self.assertEqual(self.api.delete(f'/publicidad/{hoy.pk}/delete/').status_code,204)
        self.api.force_authenticate(self.ajeno)
        self.assertEqual(self.api.delete(f'/publicidad/{ayer.pk}/delete/').status_code,403)
        self.api.force_authenticate(self.admin)
        self.assertEqual(self.api.delete(f'/publicidad/{ayer.pk}/delete/').status_code,204)

    def test_conservar_historia_y_perfil_no_borra_puntos(self):
        import importlib
        punto=self.punto(registrado_por=None,autor_nombre='',autor_rol='Historico',fecha=date(2025,1,2))
        punto.refresh_from_db()
        original=(punto.fecha,punto.hora,punto.latitud,punto.longitud,punto.nota)
        migracion=importlib.import_module('Publicidad.migrations.0002_autoria_publicidad')
        migracion.conservar_autores(apps,SimpleNamespace(connection=connection))
        punto.refresh_from_db()
        self.assertEqual(punto.registrado_por_id,self.worker.pk)
        self.assertEqual(punto.autor_nombre,self.worker.username)
        self.assertEqual(punto.autor_rol,'Historico')
        self.assertEqual((punto.fecha,punto.hora,punto.latitud,punto.longitud,punto.nota),original)
        self.perfil.delete(); punto.refresh_from_db()
        self.assertIsNone(punto.trabajador_id)
        self.assertEqual(punto.autor_nombre,'operador-a')

    def test_listado_sin_n_mas_uno_y_sin_mutaciones(self):
        self.punto()
        with CaptureQueriesContext(connection) as pocas:
            self.assertEqual(self.lista().status_code,200)
        for _ in range(20): self.punto()
        with CaptureQueriesContext(connection) as muchas:
            self.assertEqual(self.lista().status_code,200)
        self.assertEqual(len(pocas),len(muchas))
        self.assertEqual(Publicidad.objects.count(),21)
        self.assertFalse(MovimientoCaja.objects.exists())
