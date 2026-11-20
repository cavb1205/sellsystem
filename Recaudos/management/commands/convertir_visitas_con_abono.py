"""Convierte en abonos las visitas sin abono que ya fueron editadas a un monto.

Antes de la corrección de ``put_recaudo``, editar una visita sin abono a un
monto real dejaba el recaudo vinculado a ``Visita_Blanco``: el dinero entraba a
la caja, pero el calendario, el score, las alertas y los reportes lo seguían
contando como falla.

Por defecto solo informa. Con ``--aplicar`` desvincula esos recaudos en una
transacción y escribe un respaldo JSON con el motivo original de cada falla.
No modifica caja, saldos de ventas ni importes; las fichas de visita se
conservan.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from Recaudos.models import Recaudo


class Command(BaseCommand):
    help = 'Desvincula de su falla las visitas sin abono editadas a un monto (solo informa sin --aplicar).'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Aplica el cambio (sin esto solo informa).')
        parser.add_argument('--respaldo', help='Ruta del JSON de respaldo (obligatoria con --aplicar).')
        parser.add_argument('--tienda', type=int, help='Limita a una ruta.')

    def _candidatos(self, tienda):
        qs = Recaudo.objects.filter(visita_blanco__isnull=False, valor_recaudo__gt=0)
        if tienda:
            qs = qs.filter(tienda_id=tienda)
        return qs.select_related('visita_blanco').order_by('tienda_id', 'fecha_recaudo', 'pk')

    def handle(self, *args, aplicar=False, respaldo=None, tienda=None, **opts):
        if aplicar and not respaldo:
            raise CommandError('--aplicar requiere --respaldo con la ruta del archivo JSON.')
        if respaldo and Path(respaldo).exists():
            raise CommandError(f'El respaldo {respaldo} ya existe; usa otra ruta.')

        with transaction.atomic():
            filas = list(self._candidatos(tienda).select_for_update(of=('self',)))
            registros = [{
                'recaudo_id': r.pk, 'tienda_id': r.tienda_id, 'venta_id': r.venta_id,
                'fecha_recaudo': r.fecha_recaudo.isoformat(), 'valor_recaudo': str(r.valor_recaudo),
                'visita_blanco_id': r.visita_blanco_id, 'tipo_falla': r.visita_blanco.tipo_falla,
                'comentario': r.visita_blanco.comentario or '',
            } for r in filas]
            por_ruta = {}
            for reg in registros:
                por_ruta[reg['tienda_id']] = por_ruta.get(reg['tienda_id'], 0) + 1
            self.stdout.write(f'Visitas sin abono con monto: {len(registros)} en {len(por_ruta)} rutas')
            for tid, n in sorted(por_ruta.items()):
                self.stdout.write(f'  ruta {tid}: {n}')
            if not aplicar:
                self.stdout.write('Modo informe: no se modificó nada. Usa --aplicar --respaldo <archivo>.')
                return
            Path(respaldo).write_text(json.dumps({
                'generado': timezone.now().isoformat(), 'cantidad': len(registros), 'registros': registros,
            }, ensure_ascii=False, indent=1))
            actualizados = Recaudo.objects.filter(pk__in=[r['recaudo_id'] for r in registros]).update(visita_blanco=None)
            if actualizados != len(registros):
                raise CommandError('La cantidad actualizada no coincide; se revierte la transacción.')
        self.stdout.write(self.style.SUCCESS(f'Convertidos {actualizados} recaudos. Respaldo: {respaldo}'))
