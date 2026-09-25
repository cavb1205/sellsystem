"""Procesa avisos de clientes de forma acotada.

Uso controlado:
    python manage.py telegram_clientes --vinculo-id 12
    python manage.py telegram_clientes --todos --max-eventos 20
    python manage.py telegram_clientes --limpiar --max-mensajes 500

Con las banderas por defecto apagadas solo informa que no se procesó nada.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from Clientes.telegram_cursor import avanzar, obtener_lote
from Clientes.telegram_limpieza import limpiar_datos_vencidos, limpiar_mensajes_vencidos
from Clientes.telegram_worker import procesar_siguiente


class Command(BaseCommand):
    help = 'Procesa una cantidad limitada de avisos Telegram de clientes'

    def add_arguments(self, parser):
        grupo = parser.add_mutually_exclusive_group(required=True)
        grupo.add_argument('--vinculo-id', type=int)
        grupo.add_argument('--todos', action='store_true')
        grupo.add_argument('--limpiar', action='store_true')
        parser.add_argument('--max-eventos', type=int, default=20)
        parser.add_argument('--max-mensajes', type=int, default=500)

    def handle(self, *args, **options):
        if options['limpiar']:
            limite = options['max_mensajes']
            if limite < 1 or limite > 1000:
                raise CommandError('--max-mensajes debe estar entre 1 y 1000')
            if getattr(settings, 'TELEGRAM_CLIENTES_LIMPIEZA_ENABLED', False) is not True:
                self.stdout.write(self.style.WARNING(
                    'La limpieza de Telegram está desactivada; no se borraron mensajes.'
                ))
                return
            resultado = limpiar_mensajes_vencidos(limite=limite)
            datos = limpiar_datos_vencidos(limite=limite)
            self.stdout.write(self.style.SUCCESS(
                f"Limpieza: {resultado['borrados']} borrado(s), "
                f"{resultado['pendientes']} pendiente(s), "
                f"{resultado['vencidos']} registro(s) fuera de la ventana de 48 horas; "
                f"{datos['scrubbed']} evento(s) depurado(s), "
                f"{datos['expirados']} evento(s) vencido(s)."
            ))
            return

        limite = options['max_eventos']
        if limite < 1 or limite > 100:
            raise CommandError('--max-eventos debe estar entre 1 y 100')
        if not (getattr(settings, 'TELEGRAM_CLIENTES_ENABLED', False)
                and getattr(settings, 'TELEGRAM_CLIENTES_DELIVERY_ENABLED', False)):
            self.stdout.write(self.style.WARNING(
                'Telegram de clientes está desactivado; no se procesaron eventos.'
            ))
            return
        if options['vinculo_id']:
            vinculos = [options['vinculo_id']]
        else:
            vinculos = obtener_lote(limite)
        procesados = 0
        for vinculo_id in vinculos:
            resultado = procesar_siguiente(vinculo_id)
            if resultado is not None:
                procesados += 1
                self.stdout.write(
                    f'Evento {resultado.evento_id}: {resultado.estado} ({resultado.codigo})'
                )
            if not options['vinculo_id']:
                avanzar(vinculo_id)
        self.stdout.write(self.style.SUCCESS(f'{procesados} evento(s) procesado(s).'))
