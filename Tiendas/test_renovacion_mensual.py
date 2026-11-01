"""Renovación mensual real: API, datos congelados y recuperación; sin red externa."""
from datetime import date, datetime, timezone
from unittest.mock import patch
from django.test import TestCase, override_settings
from Tiendas.test_suscripciones import SuscripcionesTests
from Tiendas.models import CuentaDestino, SolicitudPago, PagoMembresia, CambioConfiguracionRoot
from Tiendas.views import _confirmar_solicitud
from Tiendas.serializers import TiendaMembresiaSerializer

DATOS = '00.000.000-0\nSERVICIOS DE PRUEBA\nBanco Ficticio\nCuenta Corriente\n000001'


@override_settings(ROOT_URLCONF='Tiendas.test_suscripciones')
class RenovacionMensualTests(TestCase):
    setUp = SuscripcionesTests.setUp

    def configurar(self, **extra):
        return self.api.put('/tiendas/cuenta-destino/',
            {'datos_transferencia': DATOS, 'version': 0, 'motivo': 'Cambio verificado', **extra}, format='json')

    def iniciar(self, **extra):
        self.api.force_authenticate(self.admin)
        return self.api.post('/tiendas/solicitar-pago/', {'plan': 'Mensual', 'tienda_id': self.ruta.pk, **extra}, format='json')

    def actual(self, ultima=True, ruta=None):
        return self.api.get(f'/tiendas/solicitud-pago/actual/t/{ruta or self.ruta.pk}/' + ('?incluir_ultima=1' if ultima else ''))

    def test_texto_libre_conserva_orden_saltos_y_auditoria(self):
        r = self.configurar(datos_transferencia=DATOS.replace('\n', '\r\n'))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['datos_transferencia'], DATOS)
        self.assertTrue(r.data['configurada'])
        self.assertEqual(r.data['version'], 1)
        self.assertTrue(all(r.data[key] == '' for key in ('banco', 'numero', 'titular', 'tipo')))
        log = CambioConfiguracionRoot.objects.get()
        self.assertEqual(log.nuevo['datos_transferencia'], DATOS)
        self.assertEqual(log.anterior['numero'], '000-PRUEBA')
        r = self.configurar(version=1)
        self.assertEqual(r.data['version'], 1)
        self.assertEqual(CambioConfiguracionRoot.objects.count(), 1)

    def test_texto_invalido_y_conflicto_no_sobrescriben(self):
        for valor in ('', '   ', None, 123, [], 'x' * 2001, 'texto\x00oculto'):
            self.assertEqual(self.configurar(datos_transferencia=valor).status_code, 400, repr(valor)[:40])
        self.assertEqual(CuentaDestino.get().version, 0)
        self.assertEqual(self.configurar().status_code, 200)
        self.assertEqual(self.configurar(datos_transferencia='Otra cuenta').status_code, 409)
        self.assertEqual(CuentaDestino.get().datos_transferencia, DATOS)

    def test_solo_root_puede_cambiar_datos(self):
        for user in (self.admin, self.worker):
            self.api.force_authenticate(user)
            self.assertEqual(self.configurar().status_code, 403)
        self.assertEqual(CuentaDestino.get().version, 0)

    def test_solicitud_congela_texto_precio_y_cuenta_antigua(self):
        anterior = self.iniciar()
        self.assertEqual(anterior.status_code, 201, anterior.data)
        self.api.force_authenticate(self.root)
        self.assertEqual(self.configurar().status_code, 200)
        self.mensual.precio = 20000; self.mensual.save()
        recuperada = self.actual().data['solicitud']
        self.assertEqual(recuperada['cuenta'], anterior.data['cuenta'])
        self.assertEqual(recuperada['monto'], '12000')
        nueva = self.iniciar(tienda_id=self.otra.pk)
        self.assertEqual(nueva.data['cuenta']['datos_transferencia'], DATOS)
        self.assertEqual(nueva.data['monto'], '20000')
        self.assertNotIn('historial', nueva.data['cuenta'])

    def test_anual_no_se_ofrece_ni_se_compra_por_nombre_o_id(self):
        self.assertNotIn('Anual', [p['nombre'] for p in self.api.get('/tiendas/planes/').data])
        for extra in ({'plan': 'Anual'}, {'membresia_id': self.anual.pk}):
            r = self.iniciar(**extra)
            self.assertEqual(r.status_code, 400, r.data)
            self.assertEqual(r.data['code'], 'plan_retirado')
        self.assertFalse(SolicitudPago.objects.exists())
        self.anual.refresh_from_db(); self.assertEqual(self.anual.precio, 120000)

    def test_anual_manual_no_modifica_ni_genera_ingreso(self):
        r = self.api.post(f'/tiendas/activate/year/{self.tm.pk}/', {'version': self.tm.version}, format='json')
        self.assertEqual(r.status_code, 400, r.data)
        self.tm.refresh_from_db(); self.assertEqual(self.tm.fecha_vencimiento, date(2026, 12, 1))
        self.assertEqual(PagoMembresia.objects.count(), 1)

    def test_solicitud_anual_historica_se_puede_confirmar_sin_alterar_contrato(self):
        s = SolicitudPago.objects.create(tienda=self.ruta, membresia=self.anual,
            codigo='AA-HIST', estado='pendiente_confirmacion', expira=datetime(2026, 10, 12, tzinfo=timezone.utc))
        _confirmar_solicitud(s, self.root)
        self.tm.refresh_from_db()
        self.assertEqual(self.tm.fecha_vencimiento, date(2027, 12, 1))
        self.assertEqual(PagoMembresia.objects.get(solicitud=s).monto, 120000)

    def test_doble_toque_retoma_misma_solicitud_sin_generar_otro_pago(self):
        primera = self.iniciar()
        segunda = self.iniciar()
        self.assertEqual((primera.status_code, segunda.status_code), (201, 200))
        self.assertEqual(primera.data['codigo'], segunda.data['codigo'])
        self.assertEqual(SolicitudPago.objects.count(), 1)
        self.assertEqual(PagoMembresia.objects.count(), 1)
        self.ruta.refresh_from_db(); self.venta.refresh_from_db()
        self.assertEqual(self.ruta.caja_inicial, 10000)
        self.assertEqual(self.venta.saldo_actual, 1100)

    def test_solicitudes_en_revision_no_invitan_a_otro_pago(self):
        primera = self.iniciar()
        s = SolicitudPago.objects.get(codigo=primera.data['codigo'])
        for estado in ('pendiente_confirmacion', 'procesando', 'pre_aprobada'):
            s.estado = estado; s.save()
            self.assertEqual(self.actual().data['solicitud']['estado'], estado)
            self.assertEqual(self.iniciar().data['codigo'], s.codigo)
        self.assertEqual(SolicitudPago.objects.count(), 1)

    def test_recupera_ultimo_resultado_sin_perder_estado_al_recargar(self):
        primera = self.iniciar(); s = SolicitudPago.objects.get(codigo=primera.data['codigo'])
        for estado in ('confirmada', 'rechazada', 'expirada', 'cancelada'):
            s.estado = estado; s.motivo_rechazo = 'Revisar referencia'; s.save()
            self.assertEqual(self.actual().data['solicitud']['estado'], estado)
            self.assertIsNone(self.actual(ultima=False).data['solicitud'])
        self.assertIsNone(self.actual(ruta=self.otra.pk).data['solicitud'])

    def test_prevision_conserva_dias_y_reanuda_desde_hoy_si_vencio(self):
        self.assertEqual(TiendaMembresiaSerializer(self.tm).data['renovacion_mensual'],
            {'dias': 30, 'desde': '2026-12-01', 'hasta': '2026-12-31'})
        self.tm.fecha_vencimiento = date(2026, 10, 1)
        self.assertEqual(TiendaMembresiaSerializer(self.tm).data['renovacion_mensual']['hasta'], '2026-11-09')
        self.tm.fecha_vencimiento = date(2027, 1, 1); self.tm.vencida_manualmente = True
        self.assertEqual(TiendaMembresiaSerializer(self.tm).data['renovacion_mensual']['hasta'], '2026-11-09')

    def test_prevision_y_confirmacion_real_coinciden(self):
        preview = TiendaMembresiaSerializer(self.tm).data['renovacion_mensual']['hasta']
        primera = self.iniciar(); s = SolicitudPago.objects.get(codigo=primera.data['codigo'])
        s.estado = 'pendiente_confirmacion'; s.save()
        _confirmar_solicitud(s, self.root)
        self.tm.refresh_from_db()
        self.assertEqual(str(self.tm.fecha_vencimiento), preview)
        self.assertEqual(self.actual().data['solicitud']['estado'], 'confirmada')

    def test_cuenta_inexistente_en_historico_no_usa_cuenta_nueva(self):
        s = SolicitudPago.objects.create(tienda=self.ruta, membresia=self.mensual,
            codigo='MM-ANT', estado='pendiente', expira=datetime(2026, 10, 12, tzinfo=timezone.utc))
        self.assertEqual(self.configurar().status_code, 200)
        data = self.actual().data['solicitud']
        self.assertIsNone(data['cuenta']); self.assertFalse(data['cuenta_historica_verificable'])

    def test_no_revela_solicitudes_a_usuarios_de_otra_ruta(self):
        self.iniciar()
        self.api.force_authenticate(self.worker)
        self.assertEqual(self.actual(ruta=self.otra.pk).status_code, 403)
        self.assertEqual(self.api.post('/tiendas/solicitar-pago/', {'plan': 'Mensual', 'tienda_id': self.otra.pk}, format='json').status_code, 403)

    def test_endpoint_liviano_entrega_prevision_sin_modificar_suscripcion(self):
        self.api.force_authenticate(self.admin)
        with patch('Tiendas.views.comprobar_estado_membresia', side_effect=AssertionError('No recalcular en consulta liviana')):
            r = self.api.get(f'/tiendas/detail/admin/{self.ruta.pk}/?vista=membresia')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['renovacion_mensual']['hasta'], '2026-12-31')
        self.assertEqual(r.data['version'], 0)

    def enviar_imagen(self, codigo):
        from io import BytesIO
        import tempfile
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        output = BytesIO(); Image.new('RGB', (10, 10), 'white').save(output, format='PNG')
        with tempfile.TemporaryDirectory() as directory, self.settings(MEDIA_ROOT=directory), patch('Tiendas.telegram_bot.notificar_solicitud', return_value=None):
            return self.api.post(f'/tiendas/solicitud-pago/{codigo}/comprobante/',
                {'comprobante': SimpleUploadedFile('ficticio.png', output.getvalue(), content_type='image/png')}, format='multipart')

    def test_comprobante_queda_en_revision_y_no_duplica_pago(self):
        primera = self.iniciar(); codigo = primera.data['codigo']
        r = self.enviar_imagen(codigo)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['mensaje'], 'Comprobante recibido. El pago está en revisión.')
        self.assertTrue(r.data['acceso_temporal'])
        self.assertEqual(self.enviar_imagen(codigo).status_code, 409)
        self.assertEqual(SolicitudPago.objects.count(), 1)
        self.assertEqual(PagoMembresia.objects.count(), 1)
        recuperada = self.actual().data['solicitud']
        self.assertTrue(recuperada['tiene_comprobante'])
        self.assertEqual(recuperada['estado'], 'pendiente_confirmacion')

    def test_comprobante_no_desbloquea_vencimiento_manual_ni_promete_acceso(self):
        self.tm.vencida_manualmente = True; self.tm.estado = 'Vencida'; self.tm.save()
        primera = self.iniciar()
        r = self.enviar_imagen(primera.data['codigo'])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(r.data['acceso_temporal'])
        self.assertNotIn('acceso ya está activo', r.data['mensaje'])
        self.tm.refresh_from_db(); self.assertTrue(self.tm.vencida_manualmente)
        self.assertFalse(self.actual().data['solicitud']['acceso_temporal'])
