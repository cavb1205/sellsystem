"""Una visita sin abono editada a un monto real pasa a ser un abono."""
import json
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from Recaudos.models import Recaudo, Visita_Blanco
from Tiendas.models import Cierre_Caja, MovimientoCaja
from Tiendas import test_cierres as cierres_tests

HOY = datetime(2026, 11, 2, 14, tzinfo=timezone.utc)


@override_settings(ROOT_URLCONF='Tiendas.test_suscripciones')
class VisitaConvertidaEnAbonoTests(TestCase):
    setUp = cierres_tests.CierresAnterioresTests.setUp
    movimiento = cierres_tests.CierresAnterioresTests.movimiento
    cerrar = cierres_tests.CierresAnterioresTests.cerrar
    resumen = cierres_tests.CierresAnterioresTests.resumen
    _visita_sin_abono = cierres_tests.CierresAnterioresTests._visita_sin_abono
    _editar_abono = cierres_tests.CierresAnterioresTests._editar_abono

    def _visita(self, fecha='2026-10-31', comentario=''):
        visita = self._visita_sin_abono(fecha, datetime(2026, 10, 31, 15, tzinfo=timezone.utc))
        if comentario:
            Visita_Blanco.objects.filter(pk=visita.visita_blanco_id).update(comentario=comentario)
            visita.refresh_from_db()
        return visita

    def test_editar_la_visita_a_un_monto_la_convierte_en_abono_y_conserva_el_motivo(self):
        visita = self._visita(comentario='Volvió en la tarde')
        ficha = visita.visita_blanco_id
        self._editar_abono(visita, 600, HOY)
        visita.refresh_from_db()
        self.assertIsNone(visita.visita_blanco_id)
        self.assertEqual(visita.valor_recaudo, 600)
        self.assertTrue(Visita_Blanco.objects.filter(pk=ficha).exists())
        asiento = MovimientoCaja.objects.get(origen_id=visita.pk, accion='CORRECCION')
        self.assertEqual(asiento.delta, 600)
        self.assertEqual(asiento.detalle,
                         'Visita sin abono (Casa o Local Cerrado) convertida en abono: Volvió en la tarde')
        self.assertFalse(Recaudo.objects.filter(venta=self.venta, visita_blanco__isnull=False,
                                                fecha_recaudo=date(2026, 10, 31)).exists())
        # Cierre: el abono cuenta en su jornada y el anterior se reconstruye.
        self.assertEqual(Decimal(self.resumen('2026-10-31')['saldo_a_registrar']), 10600)
        self.assertEqual(Decimal(self.resumen('2026-10-30')['saldo_a_registrar']), 10000)

    def test_cambiar_solo_el_comentario_o_dejar_cero_mantiene_la_falla(self):
        visita = self._visita()
        self._editar_abono(visita, 0, HOY)
        visita.refresh_from_db()
        self.assertIsNotNone(visita.visita_blanco_id)
        self.assertFalse(MovimientoCaja.objects.filter(origen_id=visita.pk, tipo='RECAUDO').exists())

    def test_el_score_del_cliente_cuenta_el_abono_y_no_una_falla(self):
        def detalle():
            r = self.api.get(f'/clientes/{self.cliente.pk}/score/t/{self.ruta.pk}/')
            self.assertEqual(r.status_code, 200, r.data)
            return r.data['detalle'] if 'detalle' in r.data else r.data
        visita = self._visita()
        antes = detalle()
        self._editar_abono(visita, 600, HOY)
        despues = detalle()
        self.assertEqual(despues['pagos'], antes['pagos'] + 1)
        self.assertEqual(despues['no_pagos'], antes['no_pagos'] - 1)
        self.assertEqual(despues['racha_fallas'], 0)

    def test_comando_informa_sin_cambiar_y_aplica_con_respaldo(self):
        visita = self._visita(comentario='Pagó a la hermana')
        # Estado heredado: monto real con la falla aún vinculada.
        Recaudo.objects.filter(pk=visita.pk).update(valor_recaudo=600)
        ficha = visita.visita_blanco_id
        out = StringIO()
        call_command('convertir_visitas_con_abono', stdout=out)
        self.assertIn('Visitas sin abono con monto: 1 en 1 rutas', out.getvalue())
        self.assertIn('Modo informe', out.getvalue())
        self.assertEqual(Recaudo.objects.get(pk=visita.pk).visita_blanco_id, ficha)

        with self.assertRaises(CommandError):
            call_command('convertir_visitas_con_abono', aplicar=True, stdout=StringIO())

        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / 'respaldo.json'
            caja_antes = list(MovimientoCaja.objects.values_list('pk', 'delta'))
            call_command('convertir_visitas_con_abono', aplicar=True, respaldo=str(archivo), stdout=StringIO())
            datos = json.loads(archivo.read_text())
            self.assertEqual(datos['cantidad'], 1)
            self.assertEqual(datos['registros'][0]['recaudo_id'], visita.pk)
            self.assertEqual(datos['registros'][0]['visita_blanco_id'], ficha)
            self.assertEqual(datos['registros'][0]['comentario'], 'Pagó a la hermana')
            with self.assertRaises(CommandError):  # nunca sobrescribe un respaldo
                call_command('convertir_visitas_con_abono', aplicar=True, respaldo=str(archivo), stdout=StringIO())
        recaudo = Recaudo.objects.get(pk=visita.pk)
        self.assertIsNone(recaudo.visita_blanco_id)
        self.assertEqual(recaudo.valor_recaudo, 600)
        self.assertTrue(Visita_Blanco.objects.filter(pk=ficha).exists())
        self.assertEqual(list(MovimientoCaja.objects.values_list('pk', 'delta')), caja_antes)
        self.assertFalse(Cierre_Caja.objects.exists())
