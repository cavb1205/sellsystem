"""Informe diario del asistente: detectores, alcance, privacidad y solo lectura."""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from Clientes.models import Cliente
from Gastos.models import Gasto, Tipo_Gasto
from Recaudos.models import Recaudo, Visita_Blanco
from Tiendas import test_suscripciones as base
from Tiendas.models import AlertaOperativa, MovimientoCaja, Tienda, Tienda_Administrador
from Trabajadores.models import Perfil
from Ventas.models import Venta

TOKEN = 'k' * 48
CHILE = ZoneInfo('America/Santiago')
DIA = date(2026, 10, 9)  # "ayer" según el reloj fijo de las pruebas (viernes)


@override_settings(ROOT_URLCONF='Tiendas.test_agente_api', AGENTE_JARVIS_TOKEN=TOKEN,
                   TELEGRAM_ALERTAS_USERNAME='admin-pruebas')
class InformeTests(TestCase):
    def setUp(self):
        base.SuscripcionesTests.setUp(self)
        cache.clear()
        self.api_agente = APIClient()
        self.api_agente.credentials(HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        self.perfil = Perfil.objects.get(trabajador=self.worker)
        ajeno = base.User.objects.create_user('otro-admin')
        self.ruta_ajena = Tienda.objects.create(id=903, nombre='Ruta Ajena', administrador=ajeno)
        Tienda_Administrador.objects.create(tienda=self.ruta_ajena, administrador=ajeno)
        self.n = 0

    # Utilidades -------------------------------------------------------------
    def informe(self, **params):
        respuesta = self.api_agente.get('/tiendas/agente/informe/', params)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()

    def ruta_a(self, **params):
        return next(r for r in self.informe(**params)['rutas'] if r['nombre'] == 'Ruta A')

    def codigos(self, ruta):
        return {h['codigo']: h for h in ruta['hallazgos']}

    def nueva_venta(self, valor=1000, fecha=date(2026, 7, 1), cliente=None, **extra):
        self.n += 1
        cliente = cliente or Cliente.objects.create(
            tienda=self.ruta, identificacion=f'x{self.n}', nombres=f'Cliente{self.n}', apellidos='Prueba',
            telefono_principal='000', direccion='Dirección de prueba')
        return Venta.objects.create(tienda=self.ruta, cliente=cliente, fecha_venta=fecha, valor_venta=valor,
                                    interes=20, cuotas=20, saldo_actual=valor * 1.2, **extra)

    def nuevo_recaudo(self, venta, fecha, valor=100, gps=True):
        return Recaudo.objects.create(
            tienda=self.ruta, venta=venta, fecha_recaudo=fecha, valor_recaudo=valor,
            visita_blanco=None if valor else Visita_Blanco.objects.create(tipo_falla='Casa o Local Cerrado'),
            latitud=-22.45 if gps and valor else None, longitud=-68.92 if gps and valor else None)

    def movimiento(self, cuando, usuario, accion='CREACION', tipo='RECAUDO', delta=100, origen_id=1):
        m = MovimientoCaja.objects.create(
            tienda=self.ruta, tienda_nombre=self.ruta.nombre, tipo=tipo, accion=accion, delta=delta,
            saldo_anterior=0, saldo_posterior=delta, usuario=usuario, origen_tipo='recaudos.recaudo',
            origen_id=origen_id)
        MovimientoCaja.objects.filter(pk=m.pk).update(creado_en=cuando)
        return m

    def viernes_previos(self):
        return [DIA - timedelta(weeks=k) for k in range(1, 9)]

    # Acceso -----------------------------------------------------------------
    def test_requiere_token_y_solo_get(self):
        self.assertEqual(APIClient().get('/tiendas/agente/informe/').status_code, 401)
        self.assertEqual(self.api_agente.post('/tiendas/agente/informe/', {}).status_code, 405)

    def test_alcance_fecha_por_defecto_y_semaforo(self):
        datos = self.informe()
        self.assertEqual({r['nombre'] for r in datos['rutas']}, {'Ruta A', 'Ruta B'})
        self.assertTrue(all(r['fecha'] == DIA.isoformat() for r in datos['rutas']))
        ruta_b = next(r for r in datos['rutas'] if r['nombre'] == 'Ruta B')
        self.assertEqual((ruta_b['semaforo'], ruta_b['hallazgos']), ('verde', []))
        self.assertEqual(sum(datos['resumen'].values()), 2)
        self.assertEqual(self.api_agente.get('/tiendas/agente/informe/', {'fecha': '2026-13-01'}).status_code, 400)

    # Jornada ----------------------------------------------------------------
    def test_fallas_muy_por_encima_de_lo_normal(self):
        v = self.nueva_venta()
        for dia in self.viernes_previos():
            for _ in range(9):
                self.nuevo_recaudo(v, dia)
            self.nuevo_recaudo(v, dia, valor=0)
        for _ in range(3):
            self.nuevo_recaudo(v, DIA)
        for _ in range(7):
            self.nuevo_recaudo(v, DIA, valor=0)
        h = self.codigos(self.ruta_a())
        self.assertEqual(h['FALLAS_ALTAS']['severidad'], 'alta')
        self.assertEqual((h['FALLAS_ALTAS']['datos']['tasa_fallas'], h['FALLAS_ALTAS']['datos']['tasa_normal']), (70, 10))
        self.assertIn('RECAUDO_BAJO', h)

    def test_dia_sin_actividad_cuando_normalmente_se_trabaja(self):
        v = self.nueva_venta()
        for dia in self.viernes_previos():
            self.nuevo_recaudo(v, dia)
        ruta = self.ruta_a()
        self.assertEqual(self.codigos(ruta)['SIN_ACTIVIDAD']['severidad'], 'alta')
        self.assertEqual(ruta['semaforo'], 'rojo')

    def test_domingo_sin_trabajo_no_es_anomalia(self):
        v = self.nueva_venta()
        for k in range(1, 9):  # se trabaja de lunes a sábado, nunca el viernes de prueba
            self.nuevo_recaudo(v, DIA - timedelta(days=7 * k - 1))
        self.assertNotIn('SIN_ACTIVIDAD', self.codigos(self.ruta_a()))

    def test_abonos_sin_gps_solo_si_no_es_lo_habitual(self):
        v = self.nueva_venta()
        for dia in self.viernes_previos():
            for _ in range(5):
                self.nuevo_recaudo(v, dia)
        for _ in range(6):
            self.nuevo_recaudo(v, DIA, gps=False)
        self.assertEqual(self.codigos(self.ruta_a())['ABONOS_SIN_GPS']['severidad'], 'media')
        Recaudo.objects.update(latitud=None, longitud=None)
        h = self.codigos(self.ruta_a())
        self.assertNotIn('ABONOS_SIN_GPS', h)
        self.assertEqual(h['RUTA_SIN_GPS']['severidad'], 'info')

    # Bitácora de caja -------------------------------------------------------
    def test_abono_eliminado_por_trabajador_dias_despues(self):
        self.movimiento(datetime(2026, 10, 5, 20, tzinfo=CHILE), self.worker, origen_id=77)
        self.movimiento(datetime(2026, 10, 9, 22, tzinfo=CHILE), self.worker, accion='REVERSA', delta=-100, origen_id=77)
        self.movimiento(datetime(2026, 10, 10, 1, tzinfo=CHILE), self.worker, accion='REVERSA', delta=-50, origen_id=78)
        ruta = self.ruta_a()
        correcciones = [h for h in ruta['hallazgos'] if h['codigo'] == 'CORRECCION_CAJA']
        self.assertEqual(len(correcciones), 1, 'solo cuenta lo ocurrido en la jornada local')
        h = correcciones[0]
        self.assertEqual((h['severidad'], h['titulo']), ('alta', 'Abono eliminado'))
        self.assertEqual((h['datos']['dias_despues'], h['datos']['monto_original'], h['datos']['hora']), (4, 100, '22:00'))
        self.assertEqual(h['datos']['hecho_por'], 'worker-pruebas')

    def test_sin_creacion_en_el_ledger_no_inventa_original(self):
        self.movimiento(datetime(2026, 10, 3, 20, tzinfo=CHILE), self.worker, accion='CORRECCION', origen_id=55)
        self.movimiento(datetime(2026, 10, 9, 20, tzinfo=CHILE), self.worker, accion='REVERSA', delta=-100, origen_id=55)
        h = self.codigos(self.ruta_a())['CORRECCION_CAJA']
        self.assertNotIn('monto_original', h['datos'])

    def test_correcciones_del_administrador_son_contexto(self):
        self.nuevo_recaudo(self.venta, DIA)
        for k in range(10):
            self.movimiento(datetime(2026, 10, 1 + k % 8, 10, tzinfo=CHILE), self.admin, origen_id=100 + k)
        self.movimiento(datetime(2026, 10, 9, 10, tzinfo=CHILE), self.admin, accion='REVERSA', delta=-100, origen_id=100)
        ruta = self.ruta_a()
        self.assertTrue(ruta['registra_administrador'])
        self.assertEqual(self.codigos(ruta)['CORRECCION_CAJA']['severidad'], 'info')
        self.assertEqual(ruta['semaforo'], 'verde')

    # Gastos -----------------------------------------------------------------
    def test_gasto_muy_alto_y_tipo_nuevo(self):
        comun, raro = Tipo_Gasto.objects.create(tipo_gasto='Gasolina'), Tipo_Gasto.objects.create(tipo_gasto='Reparación')
        for k in range(10):
            Gasto.objects.create(fecha=DIA - timedelta(days=3 + k * 5), tipo_gasto=comun, valor=100,
                                 trabajador=self.perfil, tienda=self.ruta)
        Gasto.objects.create(fecha=DIA, tipo_gasto=comun, valor=1000, trabajador=self.perfil, tienda=self.ruta)
        Gasto.objects.create(fecha=DIA, tipo_gasto=raro, valor=90, trabajador=self.perfil, tienda=self.ruta)
        ruta = self.ruta_a()
        h = self.codigos(ruta)
        self.assertEqual((h['GASTO_ALTO']['severidad'], h['GASTO_ALTO']['datos']['valor']), ('alta', 1000))
        self.assertEqual(h['GASTO_TIPO_NUEVO']['datos']['tipo'], 'Reparación')
        self.assertIn('GASTO_SIN_ACTIVIDAD', h)
        self.assertEqual(ruta['gastos']['del_dia'], 1090)

    def test_gastos_semanales_altos_solo_el_dia_que_cruzan(self):
        tipo = Tipo_Gasto.objects.create(tipo_gasto='Gasolina')
        for k in range(8, 64):
            Gasto.objects.create(fecha=DIA - timedelta(days=k), tipo_gasto=tipo, valor=100,
                                 trabajador=self.perfil, tienda=self.ruta)
        for k in (3, 2, 1, 0):  # normal: 700 por semana; umbral 1.050
            Gasto.objects.create(fecha=DIA - timedelta(days=k), tipo_gasto=tipo, valor=400,
                                 trabajador=self.perfil, tienda=self.ruta)
        # La semana móvil vale 900 el día -2, 1.200 el día -1 (cruza) y 1.600 el día 0 (ya avisado).
        self.assertIn('GASTOS_SEMANA_ALTOS', self.codigos(self.ruta_a(fecha=(DIA - timedelta(days=1)).isoformat())))
        self.assertNotIn('GASTOS_SEMANA_ALTOS', self.codigos(self.ruta_a()))

    def test_senales_de_venta_muestran_su_texto(self):
        v = self.nueva_venta(fecha=DIA)
        AlertaOperativa.objects.create(
            tipo='VENTA_CON_ALERTAS', severidad='critica', clave_dedupe='v9', titulo=f'Venta #{v.id} con señales operativas',
            detalle='x', tienda_id_ref=self.ruta.id, venta_id_ref=v.id, datos={'senales': [
                {'codigo': 'EXCESO_CUPO', 'severidad': 'alta', 'texto': 'Excede el cupo disponible en $500'},
                {'codigo': 'ACTIVO_OTRA_RUTA', 'severidad': 'critica', 'texto': 'Posible doble financiación por misma identificación'}]})
        h = self.codigos(self.ruta_a())
        self.assertEqual(h['CREDITO_CON_SENALES']['severidad'], 'alta')
        self.assertEqual(h['CREDITO_CON_SENALES']['datos']['senales'], ['Posible doble financiación por misma identificación'])
        self.assertEqual(h['CREDITO_SOBRE_CUPO']['severidad'], 'alta')

    def test_correccion_sin_efecto_en_caja_es_contexto(self):
        self.movimiento(datetime(2026, 10, 9, 13, tzinfo=CHILE), self.worker, accion='CORRECCION', tipo='GASTO', delta=0)
        self.assertEqual(self.codigos(self.ruta_a())['CORRECCION_CAJA']['severidad'], 'info')

    def test_senales_de_venta_heredan_gravedad_reducida(self):
        v = self.nueva_venta(fecha=DIA)
        AlertaOperativa.objects.create(tipo='VENTA_CON_ALERTAS', severidad='media', clave_dedupe='v1',
                                       titulo='Cliente con crédito en otra ruta', detalle='x',
                                       tienda_id_ref=self.ruta.id, venta_id_ref=v.id)
        self.assertEqual(self.codigos(self.ruta_a())['CREDITO_CON_SENALES']['severidad'], 'info')
        AlertaOperativa.objects.create(tipo='VENTA_CON_ALERTAS', severidad='critica', clave_dedupe='v2',
                                       titulo='Documento repetido', detalle='x',
                                       tienda_id_ref=self.ruta.id, venta_id_ref=v.id)
        self.assertEqual(self.codigos(self.ruta_a())['CREDITO_CON_SENALES']['severidad'], 'alta')

    # Créditos ---------------------------------------------------------------
    def test_credito_alto_para_cliente_nuevo(self):
        for k in range(8):
            self.nueva_venta(fecha=DIA - timedelta(days=5 + k))
        self.nueva_venta(valor=5000, fecha=DIA, creado_por=self.worker)
        ruta = self.ruta_a()
        h = self.codigos(ruta)['CREDITO_ALTO']
        self.assertEqual((h['severidad'], h['datos']['cliente_nuevo'], h['datos']['registrado_por']),
                         ('alta', True, 'worker-pruebas'))
        self.assertEqual(len(ruta['creditos_nuevos']), 1)

    def test_renovacion_sobre_credito_con_mora(self):
        original = self.nueva_venta(fecha=date(2026, 8, 1))
        self.nuevo_recaudo(original, DIA - timedelta(days=20))
        self.nueva_venta(fecha=DIA, cliente=original.cliente, origen_renovacion=original)
        h = self.codigos(self.ruta_a())['RENOVACION_CON_MORA']
        self.assertEqual((h['severidad'], h['datos']['dias_sin_abono_al_renovar']), ('alta', 19))

    def test_renovacion_mensual_usa_los_tramos_de_la_app(self):
        original = self.nueva_venta(fecha=date(2026, 6, 1), plazo='Mensual')
        self.nuevo_recaudo(original, DIA - timedelta(days=31))
        self.nueva_venta(fecha=DIA, cliente=original.cliente, origen_renovacion=original, plazo='Mensual')
        self.assertNotIn('RENOVACION_CON_MORA', self.codigos(self.ruta_a()), 'pagar una vez al mes es normal')
        Recaudo.objects.filter(venta=original).update(fecha_recaudo=DIA - timedelta(days=50))
        self.assertEqual(self.codigos(self.ruta_a())['RENOVACION_CON_MORA']['severidad'], 'media')

    def test_credito_sobre_el_cupo_de_la_calificacion(self):
        v = self.nueva_venta(valor=300, fecha=DIA, creado_por=self.worker)
        AlertaOperativa.objects.create(
            tipo='VENTA_CON_ALERTAS', severidad='alta', clave_dedupe='c1', titulo='x', detalle='x',
            tienda_id_ref=self.ruta.id, venta_id_ref=v.id, datos={
                'score_previo': {'score': 55, 'nivel': 'Regular', 'cupo_disponible': 250,
                                 'justificacion': {'razon': 'Basado en 2 crédito(s) pagado(s).', 'bloqueado': False}},
                'senales': [{'codigo': 'EXCESO_CUPO', 'severidad': 'alta', 'texto': 'Excede el cupo disponible en $50',
                             'datos': {'cupo_disponible': 250, 'exceso': 50}}]})
        h = self.codigos(self.ruta_a())
        self.assertNotIn('CREDITO_CON_SENALES', h, 'el exceso de cupo es su propia alerta')
        d = h['CREDITO_SOBRE_CUPO']
        self.assertEqual((d['severidad'], d['datos']['cupo_disponible'], d['datos']['exceso'], d['datos']['calificacion'],
                          d['datos']['nivel'], d['datos']['valor'], d['datos']['registrado_por']),
                         ('alta', 250, 50, 55, 'Regular', 300, 'worker-pruebas'))

    # Clientes ---------------------------------------------------------------
    def test_clientes_sin_abonar_por_tramos(self):
        self.nuevo_recaudo(self.venta, DIA)  # el crédito del fixture queda al día
        siete = self.nueva_venta(fecha=date(2026, 9, 1))
        self.nuevo_recaudo(siete, DIA - timedelta(days=7))
        self.nuevo_recaudo(siete, DIA - timedelta(days=3), valor=0)
        self.nuevo_recaudo(siete, DIA - timedelta(days=1), valor=0)
        cinco = self.nueva_venta(fecha=date(2026, 9, 1))
        self.nuevo_recaudo(cinco, DIA - timedelta(days=5))
        mensual = self.nueva_venta(fecha=date(2026, 8, 1), plazo='Mensual')
        self.nuevo_recaudo(mensual, DIA - timedelta(days=20))
        ruta = self.ruta_a()
        lista = {f['venta_id']: f for f in ruta['clientes']['sin_abonar']}
        self.assertEqual(set(lista), {siete.id, cinco.id}, 'un mensual con 20 días está dentro de lo normal')
        self.assertEqual((lista[siete.id]['dias_sin_abonar'], lista[siete.id]['tramo'], lista[siete.id]['cruzo_hoy'],
                          lista[siete.id]['visitas_sin_pago_desde_ultimo_abono']), (7, 2, True, 2))
        self.assertEqual((lista[cinco.id]['tramo'], lista[cinco.id]['cruzo_hoy'],
                          lista[cinco.id]['visitas_sin_pago_desde_ultimo_abono']), (1, False, 0))
        self.assertEqual(ruta['clientes']['sin_abonar'][0]['venta_id'], siete.id)
        tres = self.nueva_venta(fecha=date(2026, 9, 1))
        self.nuevo_recaudo(tres, DIA - timedelta(days=3))
        otra = self.ruta_a()
        self.assertEqual(otra['clientes']['llegaron_al_primer_tramo'], 1)
        self.assertEqual(self.codigos(otra)['SIN_ABONAR']['datos']['cantidad'], 1, 'el de 3 días no entra al hallazgo')
        self.assertEqual((ruta['clientes']['total_sin_abonar'], ruta['clientes']['sin_abonar_sin_visitas']), (2, 1))
        self.assertEqual(ruta['clientes']['llegaron_al_primer_tramo'], 0)
        h = self.codigos(ruta)['SIN_ABONAR']
        self.assertEqual((h['severidad'], h['datos']['cantidad'], h['datos']['clientes'][0]['venta_id']), ('alta', 1, siete.id))


    def test_buen_pagador_que_deja_de_pagar(self):
        v = self.nueva_venta(fecha=date(2026, 8, 20))
        for k in range(8, 21):
            self.nuevo_recaudo(v, DIA - timedelta(days=k))
        for k in range(0, 3):
            self.nuevo_recaudo(v, DIA - timedelta(days=k), valor=0)
        ruta = self.ruta_a()
        self.assertEqual(self.codigos(ruta)['DEJO_DE_PAGAR']['severidad'], 'media')
        cliente = ruta['clientes']['dejaron_de_pagar_de_golpe'][0]
        self.assertEqual((cliente['venta_id'], cliente['visitas_sin_pago_ultima_semana']), (v.id, 3))

    def test_clientes_que_entraron_en_riesgo(self):
        alerta = AlertaOperativa.objects.create(
            tipo='RIESGO_CARTERA', severidad='alta', clave_dedupe='r1', titulo='x', detalle='x',
            tienda_id_ref=self.ruta.id, venta_id_ref=self.venta.id, datos={'dias_sin_abono': 7})
        AlertaOperativa.objects.filter(pk=alerta.pk).update(creada=datetime(2026, 10, 9, 21, tzinfo=CHILE))
        clientes = self.ruta_a()['clientes']
        self.assertEqual(clientes['entraron_en_riesgo'], [], 'ya figura en sin_abonar: no se repite')
        self.assertEqual(clientes['sin_abonar'][0]['venta_id'], self.venta.id)
        self.nuevo_recaudo(self.venta, DIA)
        fila = self.ruta_a()['clientes']['entraron_en_riesgo'][0]
        self.assertEqual((fila['cliente'], fila['motivo'], fila['dias_sin_abono']), ('Cliente Prueba', 'mora', 7))

    # Privacidad, solo lectura y costo ----------------------------------------
    def test_no_expone_datos_personales_ni_escribe(self):
        v = self.nueva_venta(valor=5000, fecha=DIA)
        self.nuevo_recaudo(v, DIA)
        antes = (Venta.objects.count(), Recaudo.objects.count(), MovimientoCaja.objects.count(),
                 AlertaOperativa.objects.count())
        cuerpo = json.dumps(self.informe())
        for dato in ('Dirección de prueba', 'telefono', 'identificacion', 'direccion', 'Ruta Ajena'):
            self.assertNotIn(dato, cuerpo)
        self.assertEqual(antes, (Venta.objects.count(), Recaudo.objects.count(), MovimientoCaja.objects.count(),
                                 AlertaOperativa.objects.count()))

    def test_consultas_acotadas_por_ruta(self):
        for k in range(15):
            v = self.nueva_venta(fecha=DIA - timedelta(days=k))
            self.nuevo_recaudo(v, DIA)
            self.nuevo_recaudo(v, DIA - timedelta(days=7))
        with CaptureQueriesContext(connection) as consultas:
            self.informe()
        self.assertLess(len(consultas), 80, f'{len(consultas)} consultas para 2 rutas')
