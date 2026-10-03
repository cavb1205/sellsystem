from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from Clientes import test_telegram_vinculacion as fixtures
from Clientes.telegram_limpieza import (
    limpiar_datos_vencidos, limpiar_mensajes_vencidos, registrar_entrada,
)
from Clientes.telegram_models import TelegramEvento, TelegramMensaje
from Clientes.telegram_transporte import ResultadoBorrado, ResultadoEnvio


@override_settings(TELEGRAM_CLIENTES_LIMPIEZA_ENABLED=True)
class TelegramLimpiezaTests(TestCase):
    def setUp(self):
        fixtures.VinculacionTests.setUp(self)
        self.vinculo = fixtures.VinculacionTests.vincular(self, self.clientes[0], chat=77123)

    def crear_mensaje(self, chat_id, mensaje_id, horas, direccion='saliente'):
        registro = TelegramMensaje.objects.create(
            chat_id=chat_id, mensaje_id=mensaje_id, direccion=direccion,
        )
        TelegramMensaje.objects.filter(pk=registro.pk).update(
            registrado_en=timezone.now() - timedelta(hours=horas),
        )
        return registro

    @patch('Clientes.telegram_transporte.eliminar_mensajes')
    def test_limpia_ambas_direcciones_a_24h_conserva_recientes_y_vinculo(self, eliminar):
        eliminar.return_value = ResultadoBorrado('borrado')
        entrada = self.crear_mensaje(77123, 11, 25, 'entrante')
        salida = self.crear_mensaje(77123, 12, 25)
        reciente = self.crear_mensaje(77123, 13, 23)
        expirado = self.crear_mensaje(77123, 14, 49)

        resultado = limpiar_mensajes_vencidos()

        self.assertEqual(resultado, {
            'estado': 'procesado', 'borrados': 2, 'vencidos': 1, 'pendientes': 0,
        })
        self.assertEqual(eliminar.call_count, 1)
        self.assertEqual(eliminar.call_args.args[0], 77123)
        self.assertEqual(set(eliminar.call_args.args[1]), {11, 12})
        self.assertFalse(TelegramMensaje.objects.filter(pk__in=[entrada.pk, salida.pk]).exists())
        self.assertTrue(TelegramMensaje.objects.filter(pk=reciente.pk).exists())
        self.assertFalse(TelegramMensaje.objects.filter(pk=expirado.pk).exists())
        self.vinculo.refresh_from_db()
        self.assertTrue(self.vinculo.activo)
        self.assertEqual(self.vinculo.chat_id, 77123)

    @patch('Clientes.telegram_transporte.eliminar_mensajes')
    def test_error_temporal_se_reintenta_en_siguiente_ventana(self, eliminar):
        registro = self.crear_mensaje(77123, 21, 25)
        eliminar.return_value = ResultadoBorrado('pendiente', reintentar_en=7200)

        resultado = limpiar_mensajes_vencidos()

        self.assertEqual(resultado['pendientes'], 1)
        registro.refresh_from_db()
        self.assertEqual(registro.intentos, 1)
        self.assertGreater(registro.proximo_intento, timezone.now())
        segundo = limpiar_mensajes_vencidos()
        self.assertEqual(segundo['pendientes'], 0)
        self.assertEqual(eliminar.call_count, 1)

    def test_guarda_ids_de_entrada_idempotentes_sin_contenido(self):
        self.assertTrue(registrar_entrada(77123, 31))
        self.assertTrue(registrar_entrada(77123, 31))
        self.assertEqual(TelegramMensaje.objects.filter(chat_id=77123).count(), 1)
        self.assertEqual(TelegramMensaje.objects.get().direccion, 'entrante')
        self.assertFalse(hasattr(TelegramMensaje, 'texto'))
        self.assertFalse(registrar_entrada(-77123, 32))

    @override_settings(TELEGRAM_CLIENTES_LIMPIEZA_ENABLED=False)
    @patch('Clientes.telegram_transporte.eliminar_mensajes')
    def test_bandera_apagada_no_borra_mensajes(self, eliminar):
        self.crear_mensaje(77123, 41, 25)
        resultado = limpiar_mensajes_vencidos()
        self.assertEqual(resultado['estado'], 'desactivado')
        eliminar.assert_not_called()

    def test_salida_confirmada_asocia_message_id_al_registro(self):
        from Clientes.telegram_limpieza import finalizar_salida, preparar_salida

        registro = preparar_salida(77123)
        finalizar_salida(registro.pk, ResultadoEnvio('enviado', 'confirmado', 51))
        registro.refresh_from_db()
        self.assertEqual(registro.mensaje_id, 51)
        self.assertEqual(registro.direccion, 'saliente')

    def test_depura_snapshots_terminales_y_vence_pendientes_sin_reintentar(self):
        terminal = TelegramEvento.objects.create(
            vinculo=self.vinculo, version_vinculo=self.vinculo.version,
            clave='evento-terminal', tipo='abono', credito_id_ref=101,
            estado='enviado', datos={'saldo': '330', 'ruta': 'Interna'},
        )
        vencido = TelegramEvento.objects.create(
            vinculo=self.vinculo, version_vinculo=self.vinculo.version,
            clave='evento-vencido', tipo='abono', credito_id_ref=102,
            estado='pendiente', datos={'saldo': '360'},
        )
        reciente = TelegramEvento.objects.create(
            vinculo=self.vinculo, version_vinculo=self.vinculo.version,
            clave='evento-reciente', tipo='abono', credito_id_ref=103,
            estado='pendiente', datos={'saldo': '300'},
        )
        TelegramEvento.objects.filter(pk=vencido.pk).update(
            creado_en=timezone.now() - timedelta(days=8),
        )

        resultado = limpiar_datos_vencidos()

        self.assertEqual(resultado, {'estado': 'procesado', 'scrubbed': 1, 'expirados': 1})
        terminal.refresh_from_db()
        vencido.refresh_from_db()
        reciente.refresh_from_db()
        self.assertEqual(terminal.datos, {})
        self.assertEqual(vencido.estado, 'fallido')
        self.assertEqual(vencido.datos['entrega']['codigo'], 'expirado_por_retencion')
        self.assertNotIn('saldo', vencido.datos)
        self.assertIsNone(vencido.proximo_intento)
        self.assertEqual((reciente.estado, reciente.datos), ('pendiente', {'saldo': '300'}))

    @override_settings(TELEGRAM_CLIENTES_LIMPIEZA_ENABLED=False)
    def test_limpieza_eventos_respetan_bandera(self):
        evento = TelegramEvento.objects.create(
            vinculo=self.vinculo, version_vinculo=self.vinculo.version,
            clave='evento-preservado', tipo='abono', credito_id_ref=104,
            estado='enviado', datos={'saldo': '330'},
        )
        resultado = limpiar_datos_vencidos()
        evento.refresh_from_db()
        self.assertEqual(resultado['estado'], 'desactivado')
        self.assertEqual(evento.datos, {'saldo': '330'})

    def test_limpieza_acota_borrado_de_metadatos_expirados(self):
        for mensaje_id in (61, 62, 63):
            self.crear_mensaje(77123, mensaje_id, 49)

        resultado = limpiar_mensajes_vencidos(limite=2)

        self.assertEqual(resultado['vencidos'], 2)
        self.assertEqual(TelegramMensaje.objects.filter(chat_id=77123).count(), 1)
