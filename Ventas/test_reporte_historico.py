from datetime import date, datetime, timezone as utc
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction, connection
from django.test.runner import DiscoverRunner
from django.test import TestCase
from django.urls import path
from rest_framework.test import APIClient

from Clientes.models import Cliente
from Recaudos.models import Recaudo
from Tiendas.models import Tienda, MovimientoCaja, Tienda_Administrador
from Tiendas.views import reporte_utilidad_diario
from Ventas.models import Venta, DeclaracionPerdidaVenta
from Ventas.perdidas import declarar_perdida
from Ventas.serializers import VentaSerializer, VentaUpdateSerializer
from Ventas.views import perdida_venta, post_venta, renovar_venta

urlpatterns = [
    path('ventas/<int:pk>/perdida/', perdida_venta),
    path('ventas/create/t/<str:tienda_id>/', post_venta),
    path('ventas/<int:pk>/renovar/t/<str:tienda_id>/', renovar_venta),
    path('tiendas/reportes/utilidad/<str:date1>/<str:date2>/t/<str:tienda_id>/', reporte_utilidad_diario),
]


def completar_esquema_legacy_de_pruebas():
    # Este modelo existe en el código previo pero no en sus migraciones.
    # Solo completar la BD desechable: no migrar/reparar producción aquí.
    if Tienda_Administrador._meta.db_table not in connection.introspection.table_names():
        with connection.schema_editor() as editor:
            editor.create_model(Tienda_Administrador)


class ReportesTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        # Django 4.1 + SQLite actual falla al quitar FK históricos indexados.
        # Forzar reconstrucción SOLO en estas pruebas, nunca en PostgreSQL.
        if connection.vendor == 'sqlite':
            connection.features.can_alter_table_drop_column = False
        bases = super().setup_databases(**kwargs)
        completar_esquema_legacy_de_pruebas()
        return bases


class ReporteHistoricoTests(TestCase):
    def setUp(self):
        # Fail closed on any unexpected external notification.
        self.red = patch('requests.sessions.Session.request', side_effect=AssertionError('No se permite red en pruebas'))
        self.red.start()
        self.addCleanup(self.red.stop)
        self.admin = User.objects.create_user('admin-reportes', is_staff=True)
        self.ajeno = User.objects.create_user('otro-admin', is_staff=True)
        self.ruta = Tienda.objects.create(nombre='Ruta prueba A', administrador=self.admin,
                                         caja_inicial=10000, zona_horaria='America/Santiago')
        self.otra = Tienda.objects.create(nombre='Ruta prueba B', administrador=self.ajeno)
        self.cliente = Cliente.objects.create(tienda=self.ruta, identificacion='test-1',
            nombres='Cliente', apellidos='Local', telefono_principal='000', direccion='Prueba')
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        self.ahora = datetime(2026, 10, 2, 1, 30, tzinfo=utc.utc)
        self.reloj = patch('Ventas.perdidas.timezone.now', return_value=self.ahora)
        self.reloj.start()
        self.addCleanup(self.reloj.stop)

    def venta(self, **extra):
        datos = dict(tienda=self.ruta, cliente=self.cliente, fecha_venta=date(2026, 8, 1),
                     valor_venta=1000, interes=20, cuotas=20, saldo_actual=1200)
        datos.update(extra)
        return Venta.objects.create(**datos)

    def abono(self, venta, valor, **extra):
        datos = dict(venta=venta, tienda=venta.tienda, fecha_recaudo=date(2026, 9, 1),
                     valor_recaudo=valor)
        datos.update(extra)
        return Recaudo.objects.create(**datos)

    def reporte(self, desde='2026-10-01', hasta='2026-10-31', version=True, ruta=None):
        url = f'/tiendas/reportes/utilidad/{desde}/{hasta}/t/{(ruta or self.ruta).pk}/'
        return self.api.get(url, {'version': '2'} if version else {})

    def declarar(self, venta):
        response = self.api.put(f'/ventas/{venta.pk}/perdida/', {'fecha_declaracion': '2000-01-01'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_fecha_operativa_y_foto_no_fecha_del_cliente(self):
        venta = self.venta(saldo_actual=850)
        self.abono(venta, 350)
        self.declarar(venta)
        evento = DeclaracionPerdidaVenta.objects.get()
        self.assertEqual(evento.fecha_declaracion, date(2026, 10, 1))
        self.assertEqual(evento.registrada_en, self.ahora)
        self.assertEqual(evento.capital_no_recuperado, 650)
        self.assertEqual(evento.interes_no_cobrado, 200)
        self.assertEqual(evento.usuario, self.admin)
        self.assertEqual(evento.zona_horaria, 'America/Santiago')
        self.assertTrue(evento.saldo_consistente)
        venta.refresh_from_db()
        self.cliente.refresh_from_db()
        self.assertEqual(venta.estado_venta, 'Perdida')
        self.assertEqual(self.cliente.estado_cliente, 'Bloqueado')
        self.ruta.refresh_from_db()
        self.assertEqual(self.ruta.caja_inicial, 10000)
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_reintento_no_duplica(self):
        venta = self.venta()
        primero = self.declarar(venta)
        segundo = self.declarar(venta)
        self.assertTrue(primero['declaracion_creada'])
        self.assertFalse(segundo['declaracion_creada'])
        self.assertEqual(primero['declaracion_id'], segundo['declaracion_id'])
        self.assertEqual(DeclaracionPerdidaVenta.objects.count(), 1)

    def test_historica_no_recibe_fecha_de_hoy(self):
        venta = self.venta(estado_venta='Perdida')
        response = self.declarar(venta)
        self.assertIsNone(response['fecha_declaracion'])
        self.assertIn('histórica', response['advertencia'])
        self.assertFalse(DeclaracionPerdidaVenta.objects.exists())
        data = self.reporte().data
        self.assertEqual(data['filas'], [])
        self.assertEqual(data['contexto']['perdidasSinFecha'], {'cantidad': 1, 'capitalActual': 1000})

    def test_historial_sobrevive_cambios_y_eliminacion(self):
        venta = self.venta()
        self.declarar(venta)
        original_id = venta.pk
        self.abono(venta, 200)
        venta.estado_venta = 'Vigente'
        venta.fecha_venta = date(2026, 9, 5)
        venta.saldo_actual = 1000
        venta.save()
        venta.delete()
        evento = DeclaracionPerdidaVenta.objects.get()
        self.assertIsNone(evento.venta)
        self.assertEqual(evento.venta_id_ref, original_id)
        self.assertEqual(evento.fecha_venta_original, date(2026, 8, 1))
        self.assertEqual(evento.capital_no_recuperado, 1000)
        with self.assertRaises(ValidationError):
            evento.save()
        with self.assertRaises(ValidationError):
            evento.delete()
        self.assertEqual(self.reporte().data['filas'][0]['capitalPerdidaDeclarado'], 1000)

    def test_rollback_no_deja_declaracion_ni_estado(self):
        venta = self.venta()
        with self.assertRaises(ValueError):
            with transaction.atomic():
                declarar_perdida(venta.pk, self.admin)
                raise ValueError('revertido')
        venta.refresh_from_db()
        self.cliente.refresh_from_db()
        self.assertEqual(venta.estado_venta, 'Vigente')
        self.assertEqual(self.cliente.estado_cliente, 'Activo')
        self.assertFalse(DeclaracionPerdidaVenta.objects.exists())

    def test_reactivacion_y_redeclaracion_son_brutas_no_netas(self):
        venta = self.venta()
        self.declarar(venta)
        self.abono(venta, 400)
        venta.estado_venta = 'Vigente'
        venta.saldo_actual = 800
        venta.save()
        self.declarar(venta)
        fila = self.reporte().data['filas'][0]
        self.assertEqual(fila['cantidadDeclaracionesPerdida'], 2)
        self.assertEqual(fila['capitalPerdidaDeclarado'], 1600)
        self.assertEqual(self.reporte().data['contexto']['criterioDeclaraciones'],
                         'brutas_sin_neteo_de_recuperaciones')

    def test_ajustes_negativos_y_renovacion_no_simulan_cobro(self):
        venta = self.venta(saldo_actual=950)
        self.abono(venta, 300)
        self.abono(venta, -50)
        self.abono(venta, 1200, es_renovacion=True)
        self.declarar(venta)
        evento = DeclaracionPerdidaVenta.objects.get()
        self.assertEqual(evento.abonos_reales_netos, 250)
        self.assertEqual(evento.capital_no_recuperado, 750)
        self.assertEqual(evento.interes_no_cobrado, 200)
        self.assertTrue(evento.saldo_consistente)

    def test_saldo_inconsistente_se_conserva_y_advierte(self):
        venta = self.venta(saldo_actual=800)
        self.declarar(venta)
        self.assertFalse(DeclaracionPerdidaVenta.objects.get().saldo_consistente)
        self.assertEqual(self.reporte().data['filas'][0]['declaracionesPorRevisar'], 1)

    def test_sin_saldo_o_pagado_no_se_declara(self):
        for extra in [{'saldo_actual': 0}, {'saldo_actual': None}, {'estado_venta': 'Pagado'}]:
            venta = self.venta(**extra)
            response = self.api.put(f'/ventas/{venta.pk}/perdida/')
            self.assertEqual(response.status_code, 400)
        self.assertFalse(DeclaracionPerdidaVenta.objects.exists())

    def test_permiso_declaracion_y_reporte_no_fuga(self):
        venta = self.venta()
        self.api.force_authenticate(self.ajeno)
        self.assertEqual(self.api.put(f'/ventas/{venta.pk}/perdida/').status_code, 403)
        self.assertEqual(self.reporte().status_code, 403)
        self.assertFalse(DeclaracionPerdidaVenta.objects.exists())
        self.api.force_authenticate(None)
        self.assertIn(self.reporte().status_code, (401, 403))

    def test_solo_periodo_de_declaracion_y_v1_compatible(self):
        venta = self.venta()
        self.declarar(venta)
        self.assertEqual(self.reporte('2026-09-01', '2026-09-30').data['filas'], [])
        v1 = self.reporte(version=False).data
        v2 = self.reporte().data
        self.assertEqual(v1, [])
        self.assertEqual(v2['filas'][0]['fecha'], '2026-10-01')
        self.assertEqual(v2['filas'][0]['perdidaCapital'], 0)
        self.assertEqual(v2['filas'][0]['utilidadCobrada'], 0)
        self.assertEqual(v2['contexto']['tiendaId'], self.ruta.pk)
        self.assertEqual(v2['contexto']['perdidasSinFecha']['cantidad'], 0)

    def test_clasificacion_evidencia_no_ausencia_de_vinculo(self):
        nuevo = self.venta(fecha_venta=date(2026, 9, 1), tipo_origen='Nuevo', valor_venta=100)
        anterior = self.venta()
        renovado = self.venta(fecha_venta=date(2026, 9, 1), origen_renovacion=anterior, valor_venta=200)
        self.venta(fecha_venta=date(2026, 9, 1), valor_venta=300)
        auditado = self.venta(fecha_venta=date(2026, 9, 1), valor_venta=400)
        MovimientoCaja.objects.create(tienda=self.ruta, tienda_nombre=self.ruta.nombre,
            tipo='VENTA', accion='CREACION', delta=-400, saldo_anterior=10000, saldo_posterior=9600,
            origen_tipo='ventas.venta', origen_id=auditado.pk)
        fila = self.reporte('2026-09-01', '2026-09-30').data['filas'][0]
        self.assertEqual(fila['capitalNuevos'], 500)
        self.assertEqual(fila['capitalRenovado'], 200)
        self.assertEqual(fila['capitalSinClasificar'], 300)
        self.assertEqual(fila['totalVendido'], 1000)
        self.assertEqual(fila['totalVendido'], sum(fila[k] for k in
                         ('capitalNuevos', 'capitalRenovado', 'capitalSinClasificar')))
        v1 = self.reporte('2026-09-01', '2026-09-30', version=False).data[0]
        self.assertNotIn('capitalNuevos', v1)
        for campo, valor in v1.items():
            self.assertEqual(fila[campo], valor, campo)

    def test_creacion_real_marca_nuevo_y_no_acepta_falsificar_origen(self):
        anterior = self.venta()
        with patch('Ventas.views.registrar_alerta_venta'), patch('Ventas.views.registrar_evento'):
            response = self.api.post(f'/ventas/create/t/{self.ruta.pk}/', {
                'cliente': self.cliente.pk, 'fecha_venta': '2026-09-01', 'valor_venta': '100',
                'interes': 20, 'cuotas': 20, 'plazo': 'Diario', 'tipo_origen': 'Renovacion',
                'origen_renovacion': anterior.pk,
            }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        nueva = Venta.objects.get(pk=response.data['id'])
        self.assertEqual(nueva.tipo_origen, 'Nuevo')
        self.assertIsNone(nueva.origen_renovacion)
        self.ruta.refresh_from_db()
        self.assertEqual(self.ruta.caja_inicial, 9900)
        self.assertEqual(MovimientoCaja.objects.get().delta, -100)
        serializer = VentaUpdateSerializer(nueva, data={
            'tipo_origen': 'Renovacion', 'origen_renovacion': anterior.pk, 'tienda': self.otra.pk,
        }, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        nueva.refresh_from_db()
        self.assertEqual(nueva.tipo_origen, 'Nuevo')
        self.assertIsNone(nueva.origen_renovacion)
        self.assertEqual(nueva.tienda_id, self.ruta.pk)

    def test_renovar_real_no_mueve_caja_y_preserva_tipo_al_borrar_origen(self):
        anterior = self.venta()
        with patch('Ventas.views.registrar_evento'):
            response = self.api.post(f'/ventas/{anterior.pk}/renovar/t/{self.ruta.pk}/', {
                'fecha_venta': '2026-09-01', 'interes': 20, 'cuotas': 20,
            }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        nueva = Venta.objects.get(pk=response.data['nueva_venta_id'])
        self.assertEqual(nueva.tipo_origen, 'Renovacion')
        self.assertEqual(Recaudo.objects.get().es_renovacion, True)
        self.ruta.refresh_from_db()
        self.assertEqual(self.ruta.caja_inicial, 10000)
        self.assertFalse(MovimientoCaja.objects.exists())
        anterior.delete()
        nueva.refresh_from_db()
        self.assertIsNone(nueva.origen_renovacion)
        fila = self.reporte('2026-09-01', '2026-09-30').data['filas'][0]
        self.assertEqual(fila['capitalRenovado'], 1200)
        self.assertEqual(fila['capitalNuevos'], 0)
        self.assertEqual(fila['recaudos'], 0)

    def test_admin_compartido_autorizado_sin_restringir_a_un_usuario(self):
        Tienda_Administrador.objects.create(tienda=self.ruta, administrador=self.ajeno)
        venta = self.venta()
        self.api.force_authenticate(self.ajeno)
        self.declarar(venta)
        self.assertEqual(DeclaracionPerdidaVenta.objects.get().usuario, self.ajeno)
        self.assertEqual(self.reporte().status_code, 200)
        self.assertEqual(self.reporte().data['filas'][0]['capitalPerdidaDeclarado'], 1000)

    def test_fecha_guardada_no_cambia_con_zona_actual_de_ruta(self):
        self.ruta.zona_horaria = 'UTC'
        self.ruta.save()
        venta = self.venta()
        self.declarar(venta)
        evento = DeclaracionPerdidaVenta.objects.get()
        self.assertEqual(evento.fecha_declaracion, date(2026, 10, 2))
        self.ruta.zona_horaria = 'America/Santiago'
        self.ruta.save()
        evento.refresh_from_db()
        self.assertEqual(evento.fecha_declaracion, date(2026, 10, 2))
        self.assertEqual(evento.zona_horaria, 'UTC')
        self.assertEqual(self.reporte().data['filas'][0]['fecha'], '2026-10-02')

    def test_otra_ruta_no_entra_en_totales_ni_contexto(self):
        self.venta(tienda=self.otra, estado_venta='Perdida')
        propia = self.venta(estado_venta='Perdida', saldo_actual=900)
        self.abono(propia, 300)
        contexto = self.reporte().data['contexto']
        self.assertEqual(contexto['perdidasSinFecha']['cantidad'], 1)
        self.assertEqual(contexto['perdidasSinFecha']['capitalActual'], 700)
