from datetime import date
from decimal import Decimal
from uuid import uuid4
from copy import copy
from django.db import transaction
from django.test import TestCase, override_settings

from Clientes import test_telegram_vinculacion as fixtures
from Clientes.telegram_eventos import registrar_evento
from Clientes.telegram_models import TelegramEvento
from Ventas.models import Venta
from Recaudos.models import Recaudo, Visita_Blanco


@override_settings(TELEGRAM_CLIENTES_ENABLED=True)
class EventosTests(TestCase):
    vincular = fixtures.VinculacionTests.vincular

    def setUp(self):
        fixtures.VinculacionTests.setUp(self)
        self.vincular(self.clientes[0])
        self.venta = Venta.objects.create(cliente=self.clientes[0],
            tienda=self.clientes[0].tienda, fecha_venta=date(2026, 9, 20),
            valor_venta=300, interes=20, cuotas=12, saldo_actual=360)

    def test_dedupe_conserva_snapshot(self):
        evento = registrar_evento(self.venta)
        self.venta.saldo_actual = Decimal('330')
        segundo = registrar_evento(self.venta)
        self.assertEqual(evento.pk, segundo.pk)
        self.assertEqual(segundo.datos['saldo'], '360')
        self.assertEqual(TelegramEvento.objects.count(), 1)

    def test_abono_y_falla(self):
        pago = Recaudo.objects.create(venta=self.venta, tienda=self.venta.tienda,
            fecha_recaudo=self.venta.fecha_venta, valor_recaudo=30)
        self.venta.saldo_actual = Decimal('330')
        evento = registrar_evento(self.venta, pago)
        self.assertEqual(evento.tipo, 'abono')
        self.assertEqual(Decimal(evento.datos['total_abonado']), 30)
        falla = Recaudo.objects.create(venta=self.venta, tienda=self.venta.tienda,
            fecha_recaudo=self.venta.fecha_venta, valor_recaudo=0,
            visita_blanco=Visita_Blanco.objects.create(tipo_falla='Otro Motivo', comentario='privado'))
        evento = registrar_evento(self.venta, falla)
        self.assertEqual(evento.tipo, 'falla')
        self.assertNotIn('privado', str(evento.datos))
        self.assertEqual(evento.datos['saldo'], '330')

    def test_rollback_elimina_evento(self):
        with self.assertRaises(ValueError):
            with transaction.atomic():
                registrar_evento(self.venta)
                raise ValueError('Operación revertida')
        self.assertEqual(TelegramEvento.objects.count(), 0)

    def test_renovacion_no_publica_abono(self):
        pago = Recaudo.objects.create(venta=self.venta, tienda=self.venta.tienda,
            fecha_recaudo=self.venta.fecha_venta, valor_recaudo=360, es_renovacion=True)
        self.assertIsNone(registrar_evento(self.venta, pago))

    def test_correcciones_sucesivas_tienen_identidad_propia(self):
        pago = Recaudo.objects.create(venta=self.venta, tienda=self.venta.tienda,
            fecha_recaudo=self.venta.fecha_venta, valor_recaudo=30)
        self.venta.saldo_actual = Decimal('330')
        operacion = uuid4()
        primero = registrar_evento(self.venta, pago, accion='correccion', operacion_id=operacion)
        repetido = registrar_evento(self.venta, pago, accion='correccion', operacion_id=operacion)
        self.assertEqual(primero.pk, repetido.pk)
        pago.valor_recaudo = Decimal('60')
        pago.save()
        self.venta.saldo_actual = Decimal('300')
        segundo = registrar_evento(self.venta, pago, accion='correccion', operacion_id=uuid4())
        self.assertNotEqual(primero.pk, segundo.pk)
        self.assertEqual(segundo.datos['saldo'], '300')
        primero.refresh_from_db()
        self.assertEqual(primero.datos['saldo'], '330')

    def test_reversa_conserva_referencia_del_pago_eliminado(self):
        pago = Recaudo.objects.create(venta=self.venta, tienda=self.venta.tienda,
            fecha_recaudo=self.venta.fecha_venta, valor_recaudo=30)
        referencia = copy(pago)
        pago.delete()
        evento = registrar_evento(self.venta, referencia, accion='reversa', operacion_id=uuid4())
        self.assertEqual(evento.registro_id_ref, referencia.pk)
        self.assertEqual(Decimal(evento.datos['total_abonado']), 0)
        self.assertEqual(evento.tipo, 'reversa')

    def test_renovacion_expresa_no_simula_liquidacion(self):
        pago = Recaudo.objects.create(venta=self.venta, tienda=self.venta.tienda,
            fecha_recaudo=self.venta.fecha_venta, valor_recaudo=360, es_renovacion=True)
        self.venta.saldo_actual = Decimal('0')
        evento = registrar_evento(self.venta, pago, accion='renovacion', operacion_id=uuid4())
        self.assertEqual(evento.tipo, 'renovacion')
        self.assertEqual(evento.datos['cierre'], 'renovacion')
        self.assertEqual(Decimal(evento.datos['progreso']), 0)

    def test_mutacion_requiere_id_operacion(self):
        pago = Recaudo.objects.create(venta=self.venta, tienda=self.venta.tienda,
            fecha_recaudo=self.venta.fecha_venta, valor_recaudo=30)
        with self.assertRaises(ValueError):
            registrar_evento(self.venta, pago, accion='correccion')

    @override_settings(TELEGRAM_CLIENTES_ENABLED=False)
    def test_deshabilitado_no_crea_eventos(self):
        self.assertIsNone(registrar_evento(self.venta))
        self.assertEqual(TelegramEvento.objects.count(), 0)
