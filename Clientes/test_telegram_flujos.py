from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from Clientes import test_telegram_eventos as fixtures
from Clientes.telegram_models import TelegramEvento
from Recaudos.models import Recaudo, Visita_Blanco
from Recaudos.views import post_recaudo, post_recaudo_no_pay, put_recaudo, delete_recaudo
from Tiendas.models import MovimientoCaja
from Ventas.models import Venta, AjusteVentaAdministrativo
from Ventas.views import post_venta, renovar_venta, corregir_venta_administrativamente
from Clientes.telegram_eventos import registrar_evento
from Ventas.views import put_venta, delete_venta
from Clientes.telegram_mensajes import mensaje_evento


@override_settings(TELEGRAM_CLIENTES_ENABLED=True)
class FlujosTests(TestCase):
    vincular = fixtures.EventosTests.vincular
    setUp = fixtures.EventosTests.setUp

    def ejecutar(self, vista, datos):
        request = APIRequestFactory().post('/', datos, format='json')
        force_authenticate(request, self.admin)
        with patch('Recaudos.views.revisar_riesgo_venta'), patch('Ventas.views.registrar_alerta_venta'), patch('requests.post', side_effect=AssertionError('No se permite red')):
            return vista(request, tienda_id=self.venta.tienda_id)

    def pago(self, monto=30):
        return {'venta': self.venta.pk, 'fecha_recaudo': '2026-09-20', 'valor_recaudo': monto}

    def test_saldo_historico_inconsistente_no_impide_pago(self):
        self.venta.saldo_actual = 300
        self.venta.save(update_fields=['saldo_actual'])
        respuesta = self.ejecutar(post_recaudo, self.pago())
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.venta.refresh_from_db()
        self.assertEqual(self.venta.saldo_actual, 270)
        self.assertEqual(MovimientoCaja.objects.get().delta, 30)
        evento = TelegramEvento.objects.get()
        self.assertEqual(evento.estado, 'fallido')
        self.assertEqual(evento.datos['codigo'], 'saldo_no_conciliado')
        self.assertNotIn('saldo', evento.datos)
        with self.assertRaises(ValueError):
            mensaje_evento(evento)
        self.assertEqual(registrar_evento(self.venta, Recaudo.objects.get()).pk, evento.pk)

    def test_incidencia_conciliacion_se_revierte_con_operacion(self):
        self.venta.saldo_actual = 300
        self.venta.save(update_fields=['saldo_actual'])
        request = APIRequestFactory().post('/', self.pago(), format='json')
        force_authenticate(request, self.admin)
        with patch('Recaudos.views.revisar_riesgo_venta', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                post_recaudo(request, tienda_id=self.venta.tienda_id)
        self.venta.refresh_from_db()
        self.assertEqual(self.venta.saldo_actual, 300)
        self.assertFalse(TelegramEvento.objects.exists())
        self.assertFalse(Recaudo.objects.exists())

    def test_error_inesperado_no_se_oculta_como_conciliacion(self):
        with patch('Clientes.telegram_eventos.resumen_credito', side_effect=RuntimeError('Error inesperado')):
            with self.assertRaises(RuntimeError):
                self.ejecutar(post_recaudo, self.pago())
        self.assertFalse(Recaudo.objects.exists())
        self.assertFalse(TelegramEvento.objects.exists())

    def editar_credito(self):
        request = APIRequestFactory().put('/', {'fecha_venta': '2026-09-20',
            'valor_venta': 600, 'interes': 20, 'cuotas': 24, 'plazo': 'Diario',
            'tienda': self.venta.tienda_id}, format='json')
        force_authenticate(request, self.admin)
        return put_venta(request, pk=self.venta.pk, tienda_id=self.venta.tienda_id)

    def eliminar_credito(self):
        request = APIRequestFactory().delete('/')
        force_authenticate(request, self.admin)
        return delete_venta(request, pk=self.venta.pk, tienda_id=self.venta.tienda_id)

    def test_edicion_credito_y_repeticion(self):
        respuesta = self.editar_credito()
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.venta.refresh_from_db()
        self.assertEqual(self.venta.saldo_actual, 720)
        evento = TelegramEvento.objects.get()
        self.assertEqual(evento.tipo, 'edicion_credito')
        self.assertEqual(Decimal(evento.datos['saldo']), 720)
        texto = mensaje_evento(evento)
        self.assertIn('Datos del crédito corregidos', texto)
        self.assertNotIn('Ruta:', texto)
        self.assertEqual(MovimientoCaja.objects.get().delta, -300)
        self.editar_credito()
        self.assertEqual(TelegramEvento.objects.count(), 1)

    def test_eliminacion_conserva_aviso_sin_simular_pago(self):
        referencia = self.venta.pk
        respuesta = self.eliminar_credito()
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertFalse(Venta.objects.filter(pk=referencia).exists())
        evento = TelegramEvento.objects.get()
        self.assertEqual(evento.credito_id_ref, referencia)
        self.assertEqual(evento.tipo, 'eliminacion_credito')
        texto = mensaje_evento(evento)
        self.assertIn('Saldo anterior a la eliminación: $360', texto)
        self.assertNotIn('Saldo pendiente:', texto)
        self.assertNotIn('liquidado por pago', texto)
        self.assertEqual(MovimientoCaja.objects.get().delta, 300)

    def test_con_pagos_no_edita_ni_elimina_ni_notifica(self):
        self.ejecutar(post_recaudo, self.pago())
        self.assertEqual(self.editar_credito().status_code, 409)
        self.assertEqual(self.eliminar_credito().status_code, 406)
        self.assertEqual(TelegramEvento.objects.count(), 1)

    def test_eliminacion_rollback_conserva_credito_sin_aviso(self):
        with patch('Ventas.views.registrar_movimiento_caja', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                self.eliminar_credito()
        self.assertTrue(Venta.objects.filter(pk=self.venta.pk).exists())
        self.assertFalse(TelegramEvento.objects.exists())

    def ajustar(self, usuario=None, **cambios):
        datos = {'fecha_venta': '2026-09-19', 'cuotas': 24, 'motivo': 'Comentario interno privado'}
        datos.update(cambios)
        request = APIRequestFactory().patch('/', datos, format='json')
        force_authenticate(request, usuario or self.admin)
        with patch('Tiendas.telegram_bot.notificar_correccion_venta'), patch('requests.post', side_effect=AssertionError('No red')):
            return corregir_venta_administrativamente(request, pk=self.venta.pk, tienda_id=self.venta.tienda_id)

    def test_ajuste_preserva_finanzas_y_no_expone_motivo(self):
        self.ejecutar(post_recaudo, self.pago())
        respuesta = self.ajustar()
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.venta.refresh_from_db()
        self.assertEqual(self.venta.saldo_actual, 330)
        self.assertEqual(self.venta.total_a_pagar(), 360)
        self.assertEqual(Recaudo.objects.get().valor_recaudo, 30)
        self.assertEqual(MovimientoCaja.objects.count(), 1)
        evento = TelegramEvento.objects.last()
        self.assertEqual(evento.tipo, 'ajuste_credito')
        self.assertEqual(evento.datos['cuotas_anteriores'], 12)
        self.assertEqual(evento.datos['cuotas'], 24)
        texto = mensaje_evento(evento)
        self.assertIn('$30 → $15', texto)
        self.assertNotIn('Comentario interno', str(evento.datos) + texto)
        self.assertEqual(registrar_evento(self.venta, ajuste=AjusteVentaAdministrativo.objects.get()).pk, evento.pk)
        self.ajustar()
        self.assertEqual(TelegramEvento.objects.count(), 2)

    def test_ajuste_rechazado_no_publica(self):
        self.ejecutar(post_recaudo, self.pago())
        respuesta = self.ajustar(cuotas=0)
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(TelegramEvento.objects.count(), 1)
        self.assertFalse(AjusteVentaAdministrativo.objects.exists())

    def test_ajuste_no_administrador_rechazado(self):
        self.ejecutar(post_recaudo, self.pago())
        self.admin.is_staff = False
        self.admin.save()
        respuesta = self.ajustar()
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(TelegramEvento.objects.count(), 1)

    def test_ajuste_rollback_revierte_evento_y_auditoria(self):
        self.ejecutar(post_recaudo, self.pago())
        def fallar(*args, **kwargs):
            registrar_evento(*args, **kwargs)
            raise RuntimeError('Rollback de prueba')
        with patch('Ventas.views.registrar_evento', side_effect=fallar):
            with self.assertRaises(RuntimeError):
                self.ajustar()
        self.venta.refresh_from_db()
        self.assertEqual(self.venta.cuotas, 12)
        self.assertFalse(AjusteVentaAdministrativo.objects.exists())
        self.assertEqual(TelegramEvento.objects.count(), 1)

    def mutar(self, vista, datos, pk):
        request = APIRequestFactory().put('/', datos, format='json') if vista == put_recaudo else APIRequestFactory().delete('/') if vista == delete_recaudo else APIRequestFactory().post('/', datos, format='json')
        force_authenticate(request, self.admin)
        kwargs = {} if vista == delete_recaudo else {'tienda_id': self.venta.tienda_id}
        with patch('Recaudos.views.revisar_riesgo_venta'), patch('requests.post', side_effect=AssertionError('No red')):
            return vista(request, pk=pk, **kwargs)

    def test_correccion_reintento_y_anulacion(self):
        self.ejecutar(post_recaudo, self.pago())
        pago = Recaudo.objects.get()
        respuesta = self.mutar(put_recaudo, self.pago(60), pago.pk)
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(Decimal(TelegramEvento.objects.last().datos['saldo']), 300)
        self.mutar(put_recaudo, self.pago(60), pago.pk)
        self.assertEqual(TelegramEvento.objects.count(), 2)
        respuesta = self.mutar(delete_recaudo, {}, pago.pk)
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        evento = TelegramEvento.objects.last()
        self.assertEqual(evento.tipo, 'reversa')
        self.assertEqual(evento.registro_id_ref, pago.pk)
        self.assertEqual(Decimal(evento.datos['saldo']), 360)
        self.assertEqual(list(MovimientoCaja.objects.order_by('id').values_list('delta', flat=True)), [30, 30, -60])

    def test_renovacion_no_es_abono_ni_dinero_nuevo(self):
        datos = {'fecha_venta': '2026-09-20', 'interes': 20, 'cuotas': 12}
        respuesta = self.mutar(renovar_venta, datos, self.venta.pk)
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        eventos = list(TelegramEvento.objects.all())
        self.assertEqual([e.tipo for e in eventos], ['renovacion', 'credito'])
        self.assertEqual(Decimal(eventos[0].datos['total_abonado']), 0)
        self.assertIn('no corresponde a un abono', mensaje_evento(eventos[0]))
        self.assertIn('no dinero nuevo', mensaje_evento(eventos[1]))
        self.assertFalse(MovimientoCaja.objects.exists())
        repetida = self.mutar(renovar_venta, datos, self.venta.pk)
        self.assertEqual(repetida.status_code, 409)
        self.assertEqual(TelegramEvento.objects.count(), 2)

    def test_correccion_rechazada_no_genera_aviso(self):
        self.ejecutar(post_recaudo, self.pago())
        respuesta = self.mutar(put_recaudo, self.pago(400), Recaudo.objects.get().pk)
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(TelegramEvento.objects.count(), 1)

    def test_credito_caja_y_snapshot(self):
        respuesta = self.ejecutar(post_venta, {'cliente': self.venta.cliente_id,
            'fecha_venta': '2026-09-20', 'valor_venta': 300, 'interes': 20,
            'cuotas': 12, 'plazo': 'Diario'})
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        evento = TelegramEvento.objects.get()
        self.assertEqual(evento.tipo, 'credito')
        self.assertEqual(Decimal(evento.datos['saldo']), 360)
        self.assertEqual(MovimientoCaja.objects.get().delta, -300)

    def test_pago_y_falla_preservan_saldo_y_caja(self):
        respuesta = self.ejecutar(post_recaudo, self.pago())
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        abono = TelegramEvento.objects.get()
        texto_abono = mensaje_evento(abono)
        self.assertIn('👤 Cliente: Camilo', texto_abono)
        self.assertIn('📍 Estado:', texto_abono)
        self.assertIn('📊 Avance de cuotas: 1 de 12', texto_abono)
        self.assertIn('📈 Progreso financiero: 8,33%', texto_abono)
        self.assertNotIn(f"Registro #{abono.datos['registro']}", texto_abono)
        datos = self.pago(0)
        datos['visita_blanco'] = {'tipo_falla': 'Otro Motivo', 'comentario': 'privado'}
        respuesta = self.ejecutar(post_recaudo_no_pay, datos)
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.venta.refresh_from_db()
        self.assertEqual(self.venta.saldo_actual, 330)
        self.assertEqual(MovimientoCaja.objects.get().delta, 30)
        self.assertEqual(list(TelegramEvento.objects.values_list('tipo', flat=True)), ['abono', 'falla'])
        self.assertEqual(Decimal(TelegramEvento.objects.last().datos['saldo']), 330)

    def test_pago_rechazado_no_deja_evento(self):
        respuesta = self.ejecutar(post_recaudo, self.pago(361))
        self.assertEqual(respuesta.status_code, 409)
        self.assertFalse(TelegramEvento.objects.exists())
        self.assertFalse(Recaudo.objects.exists())
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_falla_invalida_revierte_visita(self):
        datos = self.pago(0)
        datos.update(fecha_recaudo='incorrecta', visita_blanco={'tipo_falla': 'Otro Motivo'})
        respuesta = self.ejecutar(post_recaudo_no_pay, datos)
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Visita_Blanco.objects.exists())
        self.assertFalse(TelegramEvento.objects.exists())

    def test_error_posterior_revierte_pago_caja_y_evento(self):
        request = APIRequestFactory().post('/', self.pago(), format='json')
        force_authenticate(request, self.admin)
        with patch('Recaudos.views.revisar_riesgo_venta', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                post_recaudo(request, tienda_id=self.venta.tienda_id)
        self.venta.refresh_from_db()
        self.assertEqual(self.venta.saldo_actual, 360)
        self.assertFalse(Recaudo.objects.exists())
        self.assertFalse(MovimientoCaja.objects.exists())
        self.assertFalse(TelegramEvento.objects.exists())

    @override_settings(TELEGRAM_CLIENTES_ENABLED=False)
    def test_desactivado_conserva_operacion_sin_eventos(self):
        respuesta = self.ejecutar(post_recaudo, self.pago())
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.venta.refresh_from_db()
        self.assertEqual(self.venta.saldo_actual, 330)
        self.assertFalse(TelegramEvento.objects.exists())
