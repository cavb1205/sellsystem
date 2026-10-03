from unittest.mock import patch

from django.test import TestCase, override_settings

from Clientes import test_telegram_eventos as fixtures
from Clientes.telegram_models import TelegramEvento
from Clientes.telegram_transporte import ResultadoEnvio
from Clientes.telegram_worker import procesar_siguiente
from Clientes.telegram_vinculacion import revocar_vinculo


@override_settings(TELEGRAM_CLIENTES_ENABLED=True,
                   TELEGRAM_CLIENTES_DELIVERY_ENABLED=True)
class WorkerTests(TestCase):
    vincular = fixtures.EventosTests.vincular

    def setUp(self):
        fixtures.EventosTests.setUp(self)
        self.vinculo = self.clientes[0].telegram_vinculo
        from Clientes.telegram_eventos import registrar_evento
        self.evento = registrar_evento(self.venta)

    @patch('Clientes.telegram_worker.enviar_texto')
    def test_procesa_formatea_y_persiste_confirmacion(self, enviar):
        enviar.return_value = ResultadoEnvio('enviado', 'confirmado', mensaje_id=77)
        resultado = procesar_siguiente(self.vinculo.pk)
        self.assertEqual((resultado.estado, resultado.codigo), ('enviado', 'confirmado'))
        enviar.assert_called_once()
        self.assertEqual(enviar.call_args.args[0], 12345)
        self.assertIn('Crédito #', enviar.call_args.args[1])
        self.evento.refresh_from_db()
        self.assertEqual((self.evento.estado, self.evento.mensaje_id), ('enviado', 77))
        self.assertNotIn('saldo', self.evento.datos)
        self.assertEqual(self.evento.datos['entrega']['codigo'], 'confirmado')

    @patch('Clientes.telegram_worker.enviar_texto')
    def test_interrupcion_queda_incierta_y_no_reenvia(self, enviar):
        enviar.return_value = ResultadoEnvio('incierto', 'conexion_interrumpida')
        resultado = procesar_siguiente(self.vinculo.pk)
        self.assertEqual(resultado.estado, 'incierto')
        self.assertIsNone(procesar_siguiente(self.vinculo.pk))
        enviar.assert_called_once()
        self.evento.refresh_from_db()
        self.assertEqual(self.evento.estado, 'incierto')

    @patch('Clientes.telegram_worker.enviar_texto')
    def test_rechazo_se_persiste_y_bloquea_siguiente(self, enviar):
        enviar.return_value = ResultadoEnvio('fallido', 'rechazado_403')
        self.assertEqual(procesar_siguiente(self.vinculo.pk).estado, 'fallido')
        self.assertIsNone(procesar_siguiente(self.vinculo.pk))
        self.evento.refresh_from_db()
        self.assertEqual(self.evento.datos['entrega']['codigo'], 'rechazado_403')
        self.assertNotIn('saldo', self.evento.datos)

    @patch('Clientes.telegram_worker.enviar_texto')
    def test_revocacion_entre_reserva_y_envio_no_llama_telegram(self, enviar):
        original = procesar_siguiente
        def reservar_y_revocar(vinculo_id):
            from Clientes.telegram_cola import reservar_siguiente
            evento = reservar_siguiente(vinculo_id)
            revocar_vinculo(self.venta.cliente_id, self.admin)
            return evento
        with patch('Clientes.telegram_worker.reservar_siguiente', side_effect=reservar_y_revocar):
            resultado = original(self.vinculo.pk)
        self.assertEqual(resultado.estado, 'cancelado')
        enviar.assert_not_called()
        self.evento.refresh_from_db()
        self.assertEqual(self.evento.estado, 'cancelado')

    @patch('Clientes.telegram_worker.enviar_texto')
    def test_mensaje_incompleto_no_sale_a_telegram(self, enviar):
        self.evento.datos = {'ruta': 'R1'}
        self.evento.save(update_fields=['datos'])
        resultado = procesar_siguiente(self.vinculo.pk)
        self.assertEqual(resultado.estado, 'fallido')
        enviar.assert_not_called()
        self.evento.refresh_from_db()
        self.assertEqual(self.evento.estado, 'fallido')
