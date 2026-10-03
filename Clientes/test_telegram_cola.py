from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from Clientes import test_telegram_eventos as fixtures
from Clientes.telegram_cola import reservar_siguiente, finalizar_reserva
from Clientes.telegram_transporte import ResultadoEnvio
from Clientes.telegram_vinculacion import revocar_vinculo
from Clientes.telegram_eventos import registrar_evento
from Clientes.telegram_models import TelegramEvento
from Tiendas.models import Tienda_Administrador


@override_settings(TELEGRAM_CLIENTES_ENABLED=True, TELEGRAM_CLIENTES_DELIVERY_ENABLED=True)
class ColaTests(TestCase):
    vincular = fixtures.EventosTests.vincular

    def setUp(self):
        fixtures.EventosTests.setUp(self)
        self.primero = registrar_evento(self.venta)
        self.segundo = TelegramEvento.objects.create(
            vinculo=self.primero.vinculo, version_vinculo=self.primero.version_vinculo,
            clave='segundo', tipo='credito', credito_id_ref=self.venta.pk,
            datos=self.primero.datos)

    def test_reserva_una_vez_y_respeta_orden(self):
        reservado = reservar_siguiente(self.primero.vinculo_id)
        self.assertEqual(reservado.pk, self.primero.pk)
        self.assertEqual(reservado.intentos, 1)
        self.assertIsNone(reservar_siguiente(self.primero.vinculo_id))
        self.segundo.refresh_from_db()
        self.assertEqual(self.segundo.estado, 'pendiente')

    def finalizar(self, evento, resultado):
        return finalizar_reserva(evento.pk, evento.intentos, evento.version_vinculo, resultado)

    def test_confirmacion_libera_siguiente_y_es_idempotente(self):
        evento = reservar_siguiente(self.primero.vinculo_id)
        resultado = ResultadoEnvio('enviado', 'confirmado', mensaje_id=42)
        self.assertTrue(self.finalizar(evento, resultado))
        self.assertFalse(self.finalizar(evento, resultado))
        evento.refresh_from_db()
        self.assertEqual(evento.mensaje_id, 42)
        self.assertEqual(evento.datos['entrega']['codigo'], 'confirmado')
        self.assertNotIn('saldo', evento.datos)
        self.assertEqual(reservar_siguiente(evento.vinculo_id).pk, self.segundo.pk)

    def test_limite_programa_espera_y_agota_tres_intentos(self):
        for numero in range(1, 4):
            evento = reservar_siguiente(self.primero.vinculo_id)
            self.assertEqual(evento.intentos, numero)
            self.finalizar(evento, ResultadoEnvio('pendiente', 'limite_telegram', reintentar_en=30))
            evento.refresh_from_db()
            if numero < 3:
                self.assertIn('saldo', evento.datos)
            self.assertIsNone(reservar_siguiente(evento.vinculo_id))
            TelegramEvento.objects.filter(pk=evento.pk).update(proximo_intento=timezone.now()-timedelta(seconds=1))
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'fallido')
        self.assertEqual(evento.datos['entrega']['codigo'], 'intentos_agotados')

    def test_respuesta_de_intento_anterior_no_sobrescribe(self):
        evento = reservar_siguiente(self.primero.vinculo_id)
        self.assertFalse(finalizar_reserva(evento.pk, 99, evento.version_vinculo,
            ResultadoEnvio('enviado', 'confirmado', mensaje_id=42)))
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'procesando')

    def test_revocacion_no_se_revierte_por_respuesta_tardia(self):
        evento = reservar_siguiente(self.primero.vinculo_id)
        revocar_vinculo(self.venta.cliente_id, self.admin)
        self.assertFalse(self.finalizar(evento, ResultadoEnvio('enviado', 'confirmado', mensaje_id=42)))
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'cancelado')

    def test_incierto_bloquea_siguientes(self):
        evento = reservar_siguiente(self.primero.vinculo_id)
        self.finalizar(evento, ResultadoEnvio('incierto', 'conexion_interrumpida'))
        self.assertIsNone(reservar_siguiente(evento.vinculo_id))
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'incierto')

    def test_apagado_antes_del_transporte_no_consume_intento(self):
        evento = reservar_siguiente(self.primero.vinculo_id)
        self.finalizar(evento, ResultadoEnvio('omitido', 'envios_desactivados'))
        evento.refresh_from_db()
        self.assertEqual((evento.estado, evento.intentos), ('pendiente', 0))

    def test_proceso_interrumpido_es_incierto_no_reenvio(self):
        reservar_siguiente(self.primero.vinculo_id)
        TelegramEvento.objects.filter(pk=self.primero.pk).update(
            proximo_intento=timezone.now()-timedelta(seconds=1))
        self.assertIsNone(reservar_siguiente(self.primero.vinculo_id))
        self.primero.refresh_from_db()
        self.assertEqual(self.primero.estado, 'incierto')
        self.assertEqual(self.primero.intentos, 1)

    def test_ruta_fuera_de_alcance_cancela(self):
        Tienda_Administrador.objects.filter(tienda=self.venta.tienda).delete()
        self.assertIsNone(reservar_siguiente(self.primero.vinculo_id))
        self.assertEqual(TelegramEvento.objects.filter(estado='cancelado').count(), 2)

    def test_incidencia_bloquea_siguientes(self):
        TelegramEvento.objects.filter(pk=self.primero.pk).update(estado='fallido')
        self.assertIsNone(reservar_siguiente(self.primero.vinculo_id))

    @override_settings(TELEGRAM_CLIENTES_DELIVERY_ENABLED=False)
    def test_envios_apagados_no_reservan(self):
        self.assertIsNone(reservar_siguiente(self.primero.vinculo_id))
        self.primero.refresh_from_db()
        self.assertEqual(self.primero.estado, 'pendiente')
