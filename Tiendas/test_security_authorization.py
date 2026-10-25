"""Regresiones de autorización con datos ficticios, SQLite y red prohibida."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.test import APIClient

from Aportes.models import Aporte
from Clientes.models import Cliente
from Gastos.models import Gasto, Tipo_Gasto
from Tiendas.models import Membresia, MovimientoCaja, Tienda, Tienda_Administrador
from Tiendas import test_suscripciones as fixtures
from Trabajadores.models import AsignacionTrabajador, Perfil
from Trabajadores.serializers import UserLoginSerializer, UserSerializer
from Trabajadores.views import Login
from Utilidades.models import Utilidad

urlpatterns = [*fixtures.urlpatterns, path('login/', Login.as_view())]


@override_settings(ROOT_URLCONF=__name__)
class SecurityAuthorizationTests(TestCase):
    def setUp(self):
        fixtures.SuscripcionesTests.setUp(self)
        self.root_profile = Perfil.objects.create(trabajador=self.root, tienda=self.ruta)
        self.admin_profile = Perfil.objects.create(trabajador=self.admin, tienda=self.ruta)
        self.ajeno = User.objects.create_user('administrador-ajeno-ficticio', is_staff=True)
        self.ruta_ajena = Tienda.objects.create(nombre='Ruta ajena ficticia', administrador=self.ajeno)
        self.perfil_ajeno = Perfil.objects.create(trabajador=self.ajeno, tienda=self.ruta_ajena)
        self.coadmin = User.objects.create_user('coadministrador-ficticio', is_staff=True)
        self.perfil_coadmin = Perfil.objects.create(trabajador=self.coadmin, tienda=self.ruta)
        Tienda_Administrador.objects.create(tienda=self.ruta, administrador=self.coadmin)
        self.api = APIClient()

    def request(self, user, method, url, data=None):
        self.api.force_authenticate(user)
        return getattr(self.api, method)(url, data or {}, format='json')

    def datos_perfil(self, perfil, **extra):
        return {'username': perfil.trabajador.username, 'first_name': 'Nombre',
                'last_name': 'Ficticio', 'identificacion': 'doc-ficticio',
                'telefono': '000', 'direccion': 'Dirección ficticia',
                'is_active': True, 'is_staff': perfil.trabajador.is_staff, **extra}

    def test_admin_no_cambia_password_root_ni_otro_admin_aunque_compartan_ruta(self):
        for perfil in (self.root_profile, self.perfil_coadmin):
            anterior = perfil.trabajador.password
            res = self.request(self.admin, 'post', f'/trabajadores/password/{perfil.pk}/',
                               {'passwordNuevo': 'Clave-Ficticia-Nueva-123!'})
            self.assertEqual(res.status_code, 403)
            perfil.trabajador.refresh_from_db()
            self.assertEqual(perfil.trabajador.password, anterior)

    def test_admin_no_edita_ni_elimina_cuentas_privilegiadas(self):
        for perfil in (self.root_profile, self.perfil_coadmin):
            antes = perfil.trabajador.username
            for method, suffix in [('put', 'update'), ('delete', 'delete')]:
                res = self.request(self.admin, method, f'/trabajadores/{perfil.pk}/{suffix}/',
                                   self.datos_perfil(perfil, username='Intruso', is_active=False))
                self.assertEqual(res.status_code, 403)
                perfil.trabajador.refresh_from_db()
                self.assertTrue(perfil.trabajador.is_active)
                self.assertEqual(perfil.trabajador.username, antes)

    def test_admin_no_convierte_worker_en_admin_y_no_escribe_parcialmente(self):
        perfil = self.worker.perfil
        res = self.request(self.admin, 'put', f'/trabajadores/{perfil.pk}/update/',
                           self.datos_perfil(perfil, is_staff=True, first_name='Cambio no permitido'))
        self.assertEqual(res.status_code, 403)
        self.worker.refresh_from_db()
        perfil.refresh_from_db()
        self.assertFalse(self.worker.is_staff)
        self.assertEqual(self.worker.first_name, '')
        self.assertEqual(perfil.telefono, '000')

    def test_admin_edita_y_restablece_password_de_su_worker(self):
        perfil = self.worker.perfil
        res = self.request(self.admin, 'put', f'/trabajadores/{perfil.pk}/update/',
                           self.datos_perfil(perfil, first_name='Cambio permitido'))
        self.assertEqual(res.status_code, 200, res.data)
        res = self.request(self.admin, 'post', f'/trabajadores/password/{perfil.pk}/',
                           {'passwordNuevo': 'Clave-Ficticia-Nueva-123!'})
        self.assertEqual(res.status_code, 200, res.data)
        self.worker.refresh_from_db()
        self.assertTrue(self.worker.check_password('Clave-Ficticia-Nueva-123!'))

    def test_worker_no_restablece_password_de_terceros(self):
        for perfil in (self.admin_profile, self.root_profile, self.perfil_ajeno):
            res = self.request(self.worker, 'post', f'/trabajadores/password/{perfil.pk}/',
                               {'passwordNuevo': 'Clave-Ficticia-Nueva-123!'})
            self.assertEqual(res.status_code, 403)

    def test_own_password_change_sigue_funcionando(self):
        for user, perfil in [(self.worker, self.worker.perfil),
                             (self.admin, self.admin_profile), (self.root, self.root_profile)]:
            res = self.request(user, 'post', f'/trabajadores/password/{perfil.pk}/',
                               {'passwordNuevo': 'Clave-Ficticia-Propia-123!'})
            self.assertEqual(res.status_code, 200, res.data)

    def test_root_puede_gestionar_usuario_fuera_de_su_ruta(self):
        res = self.request(self.root, 'post', f'/trabajadores/password/{self.perfil_ajeno.pk}/',
                           {'passwordNuevo': 'Clave-Ficticia-Soporte-123!'})
        self.assertEqual(res.status_code, 200, res.data)
        res = self.request(self.root, 'put', f'/trabajadores/{self.worker.perfil.pk}/update/',
                           self.datos_perfil(self.worker.perfil, is_staff=True))
        self.assertEqual(res.status_code, 200, res.data)

    def test_perfiles_privilegiados_no_son_asignables_como_workers(self):
        for perfil in (self.root_profile, self.perfil_coadmin):
            res = self.request(self.admin, 'post', f'/trabajadores/{perfil.pk}/asignar/t/{self.ruta.pk}/')
            self.assertEqual(res.status_code, 400)
            self.assertFalse(AsignacionTrabajador.objects.filter(perfil=perfil).exists())

    def test_edicion_y_password_de_usuario_ajeno_se_rechazan(self):
        for method, url, data in [
            ('put', f'/trabajadores/{self.perfil_ajeno.pk}/update/', self.datos_perfil(self.perfil_ajeno)),
            ('post', f'/trabajadores/password/{self.perfil_ajeno.pk}/', {'passwordNuevo':'Prueba-Ficticia-123!'})]:
            self.assertEqual(self.request(self.admin, method, url, data).status_code, 403)

    def gastos_compartidos(self):
        categoria = Tipo_Gasto.objects.create(tipo_gasto='Compartida ficticia')
        for ruta in (self.ruta, self.ruta_ajena):
            Gasto.objects.create(fecha=date(2026, 10, 9), tipo_gasto=categoria,
                                 valor=100, trabajador=self.worker.perfil, tienda=ruta)
        return categoria

    def test_catalogo_global_no_se_modifica_por_admin_worker_ni_anonimo(self):
        categoria = self.gastos_compartidos()
        for user in (self.admin, self.worker, None):
            for method, url in [('post','/gastos/tipo/create/'),
                                ('put', f'/gastos/tipo/{categoria.pk}/update/'),
                                ('delete', f'/gastos/tipo/{categoria.pk}/delete/')]:
                res = self.request(user, method, url, {'tipo_gasto':'Cambio rechazado'})
                self.assertIn(res.status_code, (401,403))
        categoria.refresh_from_db()
        self.assertEqual(categoria.tipo_gasto, 'Compartida ficticia')
        self.assertEqual(Tipo_Gasto.objects.count(), 1)
        self.assertEqual(Gasto.objects.count(), 2)
        self.assertEqual(MovimientoCaja.objects.count(), 0)

    def test_root_no_borra_categoria_utilizada_y_modelo_tambien_la_protege(self):
        categoria = self.gastos_compartidos()
        res = self.request(self.root, 'delete', f'/gastos/tipo/{categoria.pk}/delete/')
        self.assertEqual(res.status_code, 409)
        with self.assertRaises(ProtectedError):
            categoria.delete()
        self.assertEqual(Gasto.objects.count(), 2)
        self.assertEqual(Tipo_Gasto.objects.count(), 1)

    def test_root_gestiona_categoria_sin_gastos(self):
        res = self.request(self.root, 'post', '/gastos/tipo/create/', {'tipo_gasto':'Nueva ficticia'})
        self.assertEqual(res.status_code, 200, res.data)
        pk = res.data['id']
        self.assertEqual(self.request(self.root,'put',f'/gastos/tipo/{pk}/update/',
                                     {'tipo_gasto':'Renombrada ficticia'}).status_code,200)
        self.assertEqual(self.request(self.root,'delete',f'/gastos/tipo/{pk}/delete/').status_code,200)

    def test_worker_sigue_leyendo_catalogo_y_registrando_gasto(self):
        categoria = Tipo_Gasto.objects.create(tipo_gasto='Operación normal')
        res = self.request(self.worker, 'get', '/gastos/tipo/')
        self.assertEqual(res.status_code, 200)
        res = self.request(self.worker, 'post', f'/gastos/create/t/{self.ruta.pk}/',
                           {'fecha':'2026-10-09','valor':'100','tipo_gasto':categoria.pk})
        self.assertEqual(res.status_code, 200, res.data)
        self.ruta.refresh_from_db()
        self.assertEqual(self.ruta.caja_inicial, Decimal('9900'))

    def datos_cliente(self, **extra):
        return {'identificacion':'c1','nombres':'Cliente','apellidos':'Prueba',
                'telefono_principal':'000','direccion':'Dirección ficticia', **extra}

    def test_clientes_no_cambian_ruta_ni_como_root(self):
        for user in (self.worker, self.admin, self.root):
            for ruta in (self.ruta_ajena, self.otra):
                res = self.request(user, 'put', f'/clientes/{self.cliente.pk}/update/',
                                   self.datos_cliente(tienda=ruta.pk))
                self.assertEqual(res.status_code, 400, res.data)
        self.cliente.refresh_from_db()
        self.venta.refresh_from_db()
        self.assertEqual(self.cliente.tienda_id, self.ruta.pk)
        self.assertEqual(self.venta.tienda_id, self.ruta.pk)

    def test_edicion_cliente_misma_ruta_u_omitida_funciona(self):
        for extra in ({}, {'tienda':self.ruta.pk}, {'tienda':str(self.ruta.pk)}):
            res = self.request(self.worker, 'put', f'/clientes/{self.cliente.pk}/update/',
                               self.datos_cliente(nombres='Edición válida', **extra))
            self.assertEqual(res.status_code, 200, res.data)

    def test_utilidad_no_puede_cambiar_ruta_y_conserva_caja(self):
        utilidad = Utilidad.objects.create(fecha=date(2026,10,9), valor=100,
                                           trabajador=self.admin_profile, tienda=self.ruta)
        res = self.request(self.admin, 'put', f'/utilidades/{utilidad.pk}/update/',
                           {'fecha':'2026-10-09','valor':'500','tienda':self.ruta_ajena.pk})
        self.assertEqual(res.status_code, 400, res.data)
        utilidad.refresh_from_db()
        self.ruta.refresh_from_db()
        self.assertEqual(utilidad.tienda_id,self.ruta.pk)
        self.assertEqual(utilidad.valor,100)
        self.assertEqual(self.ruta.caja_inicial,10000)
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_worker_no_opera_finanzas_administrativas_explicitas_ni_implicitas(self):
        for modulo, modelo in [('utilidades',Utilidad), ('aportes',Aporte)]:
            obj = modelo.objects.create(fecha=date(2026,10,9),valor=100,
                                        trabajador=self.admin_profile,tienda=self.ruta)
            urls = [('get',f'/{modulo}/'), ('get',f'/{modulo}/t/{self.ruta.pk}/'),
                    ('get',f'/{modulo}/{obj.pk}/'), ('post',f'/{modulo}/create/'),
                    ('post',f'/{modulo}/create/t/{self.ruta.pk}/'),
                    ('put',f'/{modulo}/{obj.pk}/update/'), ('delete',f'/{modulo}/{obj.pk}/delete/')]
            for method,url in urls:
                with self.subTest(method=method,url=url):
                    res=self.request(self.worker,method,url,{'fecha':'2026-10-09','valor':'200'})
                    self.assertEqual(res.status_code,403,res.data)
            obj.refresh_from_db()
            self.assertEqual(obj.valor,100)
        self.ruta.refresh_from_db()
        self.assertEqual(self.ruta.caja_inicial,10000)
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_admin_ajeno_no_opera_finanzas_ni_por_objeto_ni_por_ruta(self):
        for modulo, modelo in [('utilidades',Utilidad), ('aportes',Aporte)]:
            obj=modelo.objects.create(fecha=date(2026,10,9),valor=100,
                                      trabajador=self.admin_profile,tienda=self.ruta)
            for method,url in [('get',f'/{modulo}/t/{self.ruta.pk}/'),
                               ('post',f'/{modulo}/create/t/{self.ruta.pk}/'),
                               ('put',f'/{modulo}/{obj.pk}/update/'),
                               ('delete',f'/{modulo}/{obj.pk}/delete/')]:
                self.assertEqual(self.request(self.ajeno,method,url,
                    {'fecha':'2026-10-09','valor':'200'}).status_code,403)

    def test_admin_finanzas_crud_con_caja_y_autor_autenticado(self):
        for modulo, modelo, signo in [('utilidades',Utilidad,-1),('aportes',Aporte,1)]:
            res=self.request(self.admin,'post',f'/{modulo}/create/t/{self.ruta.pk}/',
                {'fecha':'2026-10-09','valor':'100','trabajador':self.perfil_ajeno.pk})
            self.assertEqual(res.status_code,200,res.data)
            obj=modelo.objects.get(pk=res.data['id'])
            self.assertEqual(obj.trabajador_id,self.admin_profile.pk)
            self.ruta.refresh_from_db()
            self.assertEqual(self.ruta.caja_inicial,10000+signo*100)
            res=self.request(self.admin,'put',f'/{modulo}/{obj.pk}/update/',
                {'fecha':'2026-10-09','valor':'250','tienda':self.ruta.pk})
            self.assertEqual(res.status_code,200,res.data)
            self.ruta.refresh_from_db()
            self.assertEqual(self.ruta.caja_inicial,10000+signo*250)
            res=self.request(self.admin,'delete',f'/{modulo}/{obj.pk}/delete/')
            self.assertEqual(res.status_code,200,res.data)
            self.ruta.refresh_from_db()
            self.assertEqual(self.ruta.caja_inicial,10000)
        self.assertEqual(MovimientoCaja.objects.count(),6)

    def test_cualquier_admin_vinculado_y_root_pueden_consultar_finanzas(self):
        for user in (self.admin,self.coadmin,self.root):
            for modulo in ('utilidades','aportes'):
                self.assertEqual(self.request(user,'get',f'/{modulo}/t/{self.ruta.pk}/').status_code,200)

    def test_worker_no_desactiva_ruta_por_put_ni_patch(self):
        for method,url,data in [('put',f'/tiendas/{self.ruta.pk}/update/',
                                 {'nombre':self.ruta.nombre,'estado':False}),
                                ('patch',f'/tiendas/{self.ruta.pk}/settings/',{'zona_horaria':'UTC'})]:
            res=self.request(self.worker,method,url,data)
            self.assertEqual(res.status_code,403,res.data)
        self.ruta.refresh_from_db()
        self.assertTrue(self.ruta.estado)
        self.assertEqual(self.ruta.zona_horaria,'America/Santiago')

    def test_admin_actualiza_su_ruta_sin_poder_alterar_caja(self):
        res=self.request(self.admin,'put',f'/tiendas/{self.ruta.pk}/update/',
                         {'nombre':'Nombre permitido','caja_inicial':1})
        self.assertEqual(res.status_code,200,res.data)
        self.ruta.refresh_from_db()
        self.assertEqual(self.ruta.nombre,'Nombre permitido')
        self.assertEqual(self.ruta.caja_inicial,10000)

    def test_worker_no_crea_rutas(self):
        res=self.request(self.worker,'post','/tiendas/create/',
                         {'nombre':'No permitida','administrador':self.worker.pk})
        self.assertEqual(res.status_code,403,res.data)
        self.assertFalse(Tienda.objects.filter(nombre='No permitida').exists())

    def test_creacion_admin_no_acepta_propietario_falsificado(self):
        Membresia.objects.create(nombre='Prueba',precio=0)
        with patch('Tiendas.telegram_bot.notificar_nueva_ruta'):
            res=self.request(self.admin,'post','/tiendas/create/',
                             {'nombre':'Nueva propia','administrador':self.ajeno.pk})
        self.assertEqual(res.status_code,200,res.data)
        self.assertEqual(Tienda.objects.get(nombre='Nueva propia').administrador_id,self.admin.pk)

    def test_serializadores_y_login_no_exponen_hash_ni_permisos_internos(self):
        for serializer in (UserLoginSerializer,UserSerializer):
            data=serializer(self.admin).data
            self.assertTrue({'id','username','is_staff','is_superuser'}.issubset(data))
            for campo in ('password','groups','user_permissions'):
                self.assertNotIn(campo,data)
        self.worker.set_password('Acceso-Ficticio-2026!')
        self.worker.save()
        with patch.object(Login, 'throttle_classes', []):
            res=self.request(None,'post','/login/',
                             {'username':self.worker.username,'password':'Acceso-Ficticio-2026!'})
        self.assertEqual(res.status_code,200,res.data)
        self.assertIn('token',res.data)
        self.assertNotIn('password',res.data['user'])
