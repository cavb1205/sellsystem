"""API del asistente: solo lectura, token propio y alcance del usuario de alertas."""
import json
from datetime import date
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from Clientes.models import Cliente
from Recaudos.models import Recaudo
from Tiendas.agente_api import solo_lectura
from Tiendas.models import AlertaOperativa, Tienda, Tienda_Administrador
from Tiendas.test_suscripciones import SuscripcionesTests
from Ventas.models import Venta

TOKEN = 'k' * 48

# Usa las rutas reales de Tiendas/urls.py bajo el mismo prefijo que producción.
urlpatterns = [path('tiendas/', include('Tiendas.urls'))]


@override_settings(ROOT_URLCONF=__name__, AGENTE_JARVIS_TOKEN=TOKEN, TELEGRAM_ALERTAS_USERNAME='admin-pruebas')
class AgenteApiTests(TestCase):
    def setUp(self):
        SuscripcionesTests.setUp(self)
        cache.clear()
        self.cliente_api = APIClient()
        # Ruta de otro administrador: nunca debe aparecer.
        self.ajeno = User.objects.create_user('otro-admin')
        self.ruta_ajena = Tienda.objects.create(id=903, nombre='Ruta Ajena', administrador=self.ajeno)
        Tienda_Administrador.objects.create(tienda=self.ruta_ajena, administrador=self.ajeno)
        self.cliente_ajeno = Cliente.objects.create(tienda=self.ruta_ajena, identificacion='c9',
            nombres='Cliente', apellidos='Ajeno', telefono_principal='999', direccion='Otra dirección')
        Venta.objects.create(tienda=self.ruta_ajena, cliente=self.cliente_ajeno, fecha_venta=date(2026, 10, 1),
            valor_venta=1000, interes=20, cuotas=20, saldo_actual=1200)

    def get(self, recurso, token=TOKEN, **params):
        if token is None:
            self.cliente_api.credentials()
        else:
            self.cliente_api.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        return self.cliente_api.get(f'/tiendas/agente/{recurso}/', params)

    # Autenticación y método
    def test_sin_token_o_token_invalido_rechaza(self):
        self.assertEqual(self.get('rutas', token=None).status_code, 401)
        self.assertEqual(self.get('rutas', token='otro' * 12).status_code, 401)

    @override_settings(AGENTE_JARVIS_TOKEN='corto')
    def test_token_configurado_corto_deja_api_cerrada(self):
        self.assertEqual(self.get('rutas', token='corto').status_code, 401)

    def test_solo_get(self):
        self.cliente_api.credentials(HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        for metodo in ('post', 'put', 'patch', 'delete'):
            respuesta = getattr(self.cliente_api, metodo)('/tiendas/agente/rutas/', {})
            self.assertEqual(respuesta.status_code, 405, metodo)

    # Alcance
    def test_rutas_solo_del_usuario_de_alertas(self):
        Tienda_Administrador.objects.create(tienda=Tienda.objects.create(
            id=904, nombre='Ruta Inactiva', administrador=self.admin, estado=False), administrador=self.admin)
        r = self.get('rutas')
        self.assertEqual(r.status_code, 200)
        nombres = [x['nombre'] for x in r.json()['rutas']]
        self.assertEqual(nombres, ['Ruta A', 'Ruta B'])
        ruta_a = r.json()['rutas'][0]
        self.assertEqual(ruta_a['creditos_activos'], 1)
        self.assertEqual(ruta_a['trabajadores'], ['worker-pruebas'])
        self.assertEqual(ruta_a['fecha_operativa'], '2026-10-10')

    def test_cliente_de_otra_ruta_no_aparece(self):
        r = self.get('cliente', buscar='Cliente')
        self.assertEqual([c['cliente'] for c in r.json()['creditos']], ['Cliente Prueba'])
        self.assertEqual(self.get('cliente', buscar='Ajeno').json()['total'], 0)
        self.assertEqual(self.get('cliente', buscar='ab').status_code, 400)

    # Jornada
    def test_jornada_sin_registro_y_no_pago(self):
        r = self.get('jornada', fecha='ayer', ruta='Ruta A').json()['rutas'][0]
        self.assertEqual(r['fecha'], '2026-10-09')
        self.assertEqual(r['sin_registro'], 1)
        self.assertEqual(r['clientes_sin_registro'][0]['cliente'], 'Cliente Prueba')
        self.assertEqual(r['no_pagaron'], 0)
        Recaudo.objects.create(tienda=self.ruta, venta=self.venta, fecha_recaudo=date(2026, 10, 9), valor_recaudo=0)
        r = self.get('jornada', fecha='2026-10-09', ruta=str(self.ruta.id)).json()['rutas'][0]
        self.assertEqual(r['no_pagaron'], 1)
        self.assertEqual(r['sin_registro'], 0)
        self.assertEqual(r['clientes_no_pagaron'][0]['pendiente'], r['clientes_no_pagaron'][0]['cuota'])
        self.assertTrue(r['cierre_caja']['faltante'])

    def test_jornada_fechas_invalidas(self):
        self.assertEqual(self.get('jornada', fecha='2026-13-40').status_code, 400)
        self.assertEqual(self.get('jornada', fecha='2026-12-31').status_code, 400)

    def test_cartera_y_alertas(self):
        AlertaOperativa.objects.create(tipo='RIESGO_CARTERA', severidad='critica', clave_dedupe='a1',
            titulo='Riesgo crítico', detalle='Detalle interno', tienda_id_ref=self.ruta.id, venta_id_ref=self.venta.id)
        AlertaOperativa.objects.create(tipo='RIESGO_CARTERA', severidad='critica', clave_dedupe='a2',
            titulo='Ajena', detalle='x', tienda_id_ref=self.ruta_ajena.id)
        alertas = self.get('alertas').json()
        self.assertEqual(alertas['total'], 1)
        self.assertEqual(alertas['alertas'][0]['cliente'], 'Cliente Prueba')
        self.assertNotIn('detalle', alertas['alertas'][0])
        cartera = self.get('cartera', ruta='Ruta A').json()['rutas'][0]
        self.assertEqual(cartera['creditos_activos'], 1)
        self.assertEqual(cartera['alertas_criticas'], 1)

    # Privacidad y solo lectura
    def test_no_expone_datos_personales(self):
        cuerpo = ''.join(json.dumps(self.get(r, **p).json()) for r, p in (
            ('rutas', {}), ('jornada', {'fecha': 'ayer'}), ('cartera', {}), ('alertas', {}),
            ('cliente', {'buscar': 'Cliente'})))
        for dato in ('Dirección de prueba', 'telefono', 'identificacion', 'direccion', 'Otra dirección'):
            self.assertNotIn(dato, cuerpo)

    def test_solo_lectura_revierte_escrituras(self):
        antes = Cliente.objects.count()
        solo_lectura(lambda: Cliente.objects.create(tienda=self.ruta, identificacion='tmp',
            nombres='Temporal', apellidos='X', telefono_principal='1', direccion='y'))
        self.assertEqual(Cliente.objects.count(), antes)

    def test_consultas_no_cambian_la_base(self):
        conteos = lambda: (Venta.objects.count(), Recaudo.objects.count(), AlertaOperativa.objects.count(),
                           list(Venta.objects.order_by('id').values_list('estado_venta', 'saldo_actual')))
        antes = conteos()
        for r, p in (('rutas', {}), ('jornada', {'fecha': 'ayer'}), ('cartera', {}), ('alertas', {}),
                     ('cliente', {'buscar': 'Cliente'})):
            self.assertEqual(self.get(r, **p).status_code, 200, r)
        self.assertEqual(conteos(), antes)
