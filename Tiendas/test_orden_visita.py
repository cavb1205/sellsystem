"""Contrato del recorrido con base desechable; ninguna conexión productiva."""
from datetime import date, datetime, timezone
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.test import APIClient
from Clientes.models import Cliente
from Recaudos.models import Recaudo
from Tiendas.models import Tienda, Tienda_Administrador, OrdenVisita, CambioOrdenVisita, MovimientoCaja
from Tiendas.orden_visita import recorrido_ruta
from Trabajadores.models import Perfil, AsignacionTrabajador
from Ventas.models import Venta
from Ventas.views import list_ventas_a_liquidar

urlpatterns = [
    path('tiendas/recorrido/t/<int:tienda_id>/', recorrido_ruta),
    path('ventas/activas/liquidar/<str:date>/t/<str:tienda_id>/', list_ventas_a_liquidar),
]


@override_settings(ROOT_URLCONF=__name__)
class OrdenVisitaTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin-ruta', is_staff=True)
        self.ajeno = User.objects.create_user('admin-ajeno', is_staff=True)
        self.ruta = Tienda.objects.create(nombre='Ruta propia', administrador=self.admin, caja_inicial=10000)
        self.otra = Tienda.objects.create(nombre='Ruta ajena', administrador=self.ajeno)
        self.worker = User.objects.create_user('trabajador-ruta')
        self.perfil = Perfil.objects.create(trabajador=self.worker, tienda=self.ruta, identificacion='001', telefono='000')
        self.api = APIClient(); self.api.force_authenticate(self.admin)
        self.clientes = [self.cliente(n) for n in ('Ana', 'Pedro', 'Luisa')]
        self.ventas = [self.venta(c) for c in self.clientes]
        self.reloj = patch('Tiendas.fecha_operativa.timezone.now', return_value=datetime(2026, 10, 10, 15, tzinfo=timezone.utc))
        self.reloj.start(); self.addCleanup(self.reloj.stop)
        self.red = patch('requests.sessions.Session.request', side_effect=AssertionError('Red prohibida en pruebas'))
        self.red.start(); self.addCleanup(self.red.stop)

    def cliente(self, nombre, ruta=None):
        return Cliente.objects.create(tienda=ruta or self.ruta, identificacion=nombre,
            nombres=nombre, apellidos='Prueba', telefono_principal='000', direccion='Dirección de prueba')

    def venta(self, cliente, **extra):
        datos = dict(tienda=cliente.tienda, cliente=cliente,
            fecha_venta=date(2026,10,1), valor_venta=1000, interes=20, cuotas=20,
            saldo_actual=1200)
        datos.update(extra)
        return Venta.objects.create(**datos)

    def get(self, ruta=None):
        return self.api.get(f'/tiendas/recorrido/t/{(ruta or self.ruta).pk}/')

    def guardar(self, ids=None, version=0, ruta=None):
        return self.api.put(f'/tiendas/recorrido/t/{(ruta or self.ruta).pk}/',
            {'version':version, 'clientes':ids if ids is not None else [c.pk for c in reversed(self.clientes)]}, format='json')

    def liquidar(self, fecha='2026-10-10', previa=False):
        return self.api.get(f'/ventas/activas/liquidar/{fecha}/t/{self.ruta.pk}/', {'vista':'previa'} if previa else {})

    def test_get_no_crea_configuracion_ni_auditoria(self):
        res = self.get()
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['configurado'])
        self.assertEqual(res.data['version'],0)
        self.assertEqual([c['id'] for c in res.data['clientes']], [c.pk for c in self.clientes])
        self.assertEqual(res['Cache-Control'], 'no-store')
        self.assertFalse(OrdenVisita.objects.exists()); self.assertFalse(CambioOrdenVisita.objects.exists())
        self.assertEqual([v['id'] for v in self.liquidar().data], [v.pk for v in self.ventas])

    def test_admin_y_worker_comparten_orden_y_auditoria(self):
        self.api.force_authenticate(self.worker)
        res = self.guardar()
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['version'], 1)
        self.assertEqual(res.data['actualizado_por'], self.worker.username)
        self.api.force_authenticate(self.admin)
        self.assertEqual(self.get().data, res.data)
        cambio = CambioOrdenVisita.objects.get()
        self.assertEqual(cambio.usuario, self.worker)
        self.assertEqual(cambio.anterior, [])
        self.assertEqual(cambio.nuevo, [c.pk for c in reversed(self.clientes)])
        self.assertEqual([v['id'] for v in self.liquidar().data], [v.pk for v in reversed(self.ventas)])

    def test_permisos_ajenos_asignaciones_y_admin_vinculado(self):
        for user in [self.admin, self.worker]:
            self.api.force_authenticate(user)
            self.assertEqual(self.get(self.otra).status_code,403)
            self.assertEqual(self.guardar([],ruta=self.otra).status_code,403)
        AsignacionTrabajador.objects.create(perfil=self.perfil, tienda=self.ruta)
        adicional = Tienda.objects.create(nombre='Adicional', administrador=self.admin)
        asignacion = AsignacionTrabajador.objects.create(perfil=self.perfil, tienda=adicional)
        self.assertEqual(self.guardar([],ruta=adicional).status_code,200)
        asignacion.activa=False; asignacion.save()
        self.assertEqual(self.get(adicional).status_code,403)
        self.api.force_authenticate(self.ajeno)
        Tienda_Administrador.objects.create(tienda=self.ruta, administrador=self.ajeno)
        self.assertEqual(self.get().status_code,200)
        self.assertEqual(self.guardar().status_code,200)
        self.api.force_authenticate(None)
        self.assertIn(self.get().status_code, (401,403))

    def test_rechazar_lista_invalida_sin_escribir(self):
        ids = [c.pk for c in self.clientes]
        otro = self.cliente('Ajeno',self.otra)
        for lista,version,codigo in [(ids+[ids[0]],0,400),(ids,'0',400),(ids,True,400),
            ([True]+ids[1:],0,400),(['1']+ids[1:],0,400),(ids[:-1],0,409),
            (ids+[otro.pk],0,400),(ids+[999999],0,400),({},0,400)]:
            res = self.guardar(lista,version)
            self.assertEqual(res.status_code,codigo,res.data)
        self.assertFalse(OrdenVisita.objects.exists()); self.assertFalse(CambioOrdenVisita.objects.exists())

    def test_version_y_clientes_obsoletos_no_sobrescriben(self):
        self.assertEqual(self.guardar().status_code,200)
        self.assertEqual(self.guardar([c.pk for c in self.clientes]).status_code,409)
        nuevo = self.cliente('Carlos'); self.venta(nuevo)
        self.assertEqual(self.guardar(version=1).status_code,409)
        self.assertEqual(OrdenVisita.objects.get().version,1)
        self.assertEqual(CambioOrdenVisita.objects.count(),1)

    def test_reintento_sin_cambio_no_duplica_auditoria(self):
        self.guardar()
        self.assertEqual(self.guardar(version=1).data['version'],1)
        self.assertEqual(CambioOrdenVisita.objects.count(),1)

    def test_renovacion_inactivos_y_nuevos_conservan_posiciones(self):
        ana,pedro,luisa = self.clientes
        self.guardar([ana.pk,pedro.pk,luisa.pk])
        self.ventas[1].estado_venta='Pagado'; self.ventas[1].save()
        self.assertEqual(self.guardar([luisa.pk,ana.pk],1).status_code,200)
        self.venta(pedro,origen_renovacion=self.ventas[1])
        carlos = self.cliente('Carlos'); self.venta(carlos)
        self.assertEqual([c['id'] for c in self.get().data['clientes']], [luisa.pk,pedro.pk,ana.pk,carlos.pk])
        self.assertEqual(OrdenVisita.objects.get().clientes,[luisa.pk,pedro.pk,ana.pk])

    def test_creditos_mismo_cliente_adyacentes_sin_cambiar_datos(self):
        adicional = self.venta(self.clientes[0])
        antes = {v['id']:v for v in self.liquidar().data}
        caja = self.ruta.caja_inicial
        self.guardar()
        despues = self.liquidar().data
        self.assertEqual([v['id'] for v in despues], [self.ventas[2].pk,self.ventas[1].pk,self.ventas[0].pk,adicional.pk])
        self.assertEqual({v['id']:v for v in despues}, antes)
        self.ruta.refresh_from_db(); self.assertEqual(self.ruta.caja_inicial,caja)
        self.assertFalse(Recaudo.objects.exists()); self.assertFalse(MovimientoCaja.objects.exists())

    def test_calendarios_anticipos_y_vista_previa_se_respetan(self):
        pedro = self.ventas[1]
        pedro.plazo='Semanal'; pedro.cuotas=4; pedro.calendario_version=1
        pedro.fecha_venta=date(2026,10,8); pedro.save()
        mensual = self.venta(self.cliente('Mensual'),plazo='Mensual',cuotas=1,calendario_version=1)
        self.guardar([mensual.cliente_id,self.clientes[1].pk,self.clientes[2].pk,self.clientes[0].pk])
        self.assertEqual([v['id'] for v in self.liquidar().data], [self.ventas[2].pk,self.ventas[0].pk])
        Recaudo.objects.create(venta=pedro, tienda=self.ruta,fecha_recaudo=date(2026,10,9),valor_recaudo=100)
        datos = self.liquidar().data
        self.assertEqual([v['id'] for v in datos], [pedro.pk,self.ventas[2].pk,self.ventas[0].pk])
        self.assertFalse(datos[0]['calendario_pago']['permite_falla'])
        self.assertTrue(datos[0]['calendario_pago']['cobro_voluntario'])
        antes = list(Recaudo.objects.values())
        previa = self.liquidar('2026-10-15',True)
        self.assertEqual(previa.status_code,200)
        self.assertTrue(previa.data['vista_previa'])
        self.assertEqual([v['id'] for v in previa.data['creditos']], [pedro.pk,self.ventas[2].pk,self.ventas[0].pk])
        self.assertEqual(list(Recaudo.objects.values()),antes)

    def test_eliminado_y_perdida_no_rompen_recorrido(self):
        self.guardar()
        self.clientes[1].delete()
        self.ventas[0].estado_venta='Perdida'; self.ventas[0].save()
        self.assertEqual([c['id'] for c in self.get().data['clientes']], [self.clientes[2].pk])
        self.assertEqual([v['id'] for v in self.liquidar().data], [self.ventas[2].pk])
