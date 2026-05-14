"""Mantenimiento diario de membresías y solicitudes de pago.

Correr vía cron en el VPS:
    python manage.py mantenimiento_membresias

Hace tres cosas:
1. Recalcula estados de membresías (Activa→Pendiente Pago→Vencida, Pre-activada vencida→Pendiente Pago).
2. Marca como 'expirada' las solicitudes pre-activadas que llevan +3 días sin confirmar.
3. Borra comprobantes de pago con más de 6 meses (la copia en Telegram permanece).
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from Tiendas.models import SolicitudPago
from Tiendas.views import _actualizar_estados_membresias


class Command(BaseCommand):
    help = 'Mantenimiento diario de membresías, solicitudes y comprobantes'

    def handle(self, *args, **options):
        # 1. Estados de membresías
        _actualizar_estados_membresias()
        self.stdout.write('Estados de membresías recalculados.')

        # 2. Solicitudes pre-activadas sin confirmar (+3 días)
        limite = timezone.now() - datetime.timedelta(days=3)
        expiradas = SolicitudPago.objects.filter(
            estado='pendiente_confirmacion',
            creada__lt=limite,
        ).update(estado='expirada')
        self.stdout.write(f'{expiradas} solicitud(es) marcada(s) como expiradas.')

        # 3. Comprobantes con más de 6 meses
        corte = timezone.now() - datetime.timedelta(days=180)
        viejas = SolicitudPago.objects.filter(
            creada__lt=corte,
        ).exclude(comprobante='').exclude(comprobante__isnull=True)
        borrados = 0
        for solicitud in viejas:
            if solicitud.comprobante:
                solicitud.comprobante.delete(save=False)
                solicitud.save(update_fields=['comprobante'])
                borrados += 1
        self.stdout.write(f'{borrados} comprobante(s) antiguo(s) eliminado(s).')

        self.stdout.write(self.style.SUCCESS('Mantenimiento completado.'))
