"""Mantenimiento diario de membresías y solicitudes de pago.

Correr vía cron en el VPS a las 08:00 de America/Santiago:
    python manage.py mantenimiento_membresias

Hace lo siguiente:
1. Recalcula estados de membresías (Activa→Pendiente Pago→Vencida, Pre-activada vencida→Pendiente Pago).
2. Detecta transiciones de retención y notifica al admin por Telegram:
   - Rutas que se bloquearon por impago (pasaron a Vencida).
   - Trials de 7 días que expiraron sin que pagaran.
3. Marca como 'expirada' las solicitudes pre-activadas que llevan +3 días sin confirmar.
4. Borra comprobantes de pago con más de 6 meses (la copia en Telegram permanece).
5. Archiva rutas vencidas hace 90+ días (quita ruido; reversible, no borra datos).
6. Envía un resumen diario del negocio por Telegram.

Las notificaciones de Telegram nunca rompen el mantenimiento: si fallan, se loguean
y el comando continúa.
"""
import datetime

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from Tiendas import telegram_bot
from Tiendas.alertas_operativas import (
    revisar_cierres_ausentes,
    revisar_riesgo_cartera,
    resumen_operativo,
    rutas_del_usuario,
    usuario_alertas_configurado,
)
from Tiendas.models import (
    Membresia, PagoMembresia, SolicitudPago, Tienda, Tienda_Membresia,
)
from Tiendas.views import _actualizar_estados_membresias
from Trabajadores.models import Perfil

# Días vencida tras los cuales una ruta se archiva automáticamente
DIAS_PARA_ARCHIVAR = 90
# Ventana del aviso anticipado de vencimiento (días)
DIAS_POR_VENCER = 3


def _telefono_admin(user):
    if not user:
        return ''
    perfil = Perfil.objects.filter(trabajador=user).first()
    return perfil.telefono if perfil else ''


class Command(BaseCommand):
    help = 'Mantenimiento diario de membresías, solicitudes y comprobantes'

    def handle(self, *args, **options):
        # La fecha operativa debe seguir a Chile, no al reloj UTC del VPS.
        hoy = timezone.localdate()
        ayer = hoy - datetime.timedelta(days=1)

        # El bot operativo está asociado al usuario configurado para alertas.
        # Todo el contenido que llegue a este chat debe respetar este alcance;
        # nunca debe caer por defecto a todas las rutas de la plataforma.
        usuario_alertas = usuario_alertas_configurado()
        rutas_alertas = (
            rutas_del_usuario(usuario_alertas).filter(estado=True)
            if usuario_alertas else Tienda.objects.none()
        )
        ids_rutas_alertas = rutas_alertas.values_list('id', flat=True)

        # --- Snapshot ANTES de recalcular para detectar transiciones ---
        ids_vencidas_antes = set(
            Tienda_Membresia.objects.filter(estado='Vencida').values_list('id', flat=True)
        )
        trial = Membresia.objects.filter(nombre='Prueba').first()
        ids_trial_activo_antes = set(
            Tienda_Membresia.objects.filter(estado='Activa', membresia=trial)
            .values_list('id', flat=True)
        ) if trial else set()

        # 1. Estados de membresías
        _actualizar_estados_membresias()
        self.stdout.write('Estados de membresías recalculados.')

        # 2a. Rutas recién bloqueadas por impago (Activa/Pendiente → Vencida)
        recien_bloqueadas = (
            Tienda_Membresia.objects
            .filter(
                estado='Vencida',
                archivada=False,
                tienda_id__in=ids_rutas_alertas,
            )
            .exclude(id__in=ids_vencidas_antes)
            .select_related('tienda', 'tienda__administrador')
        )
        n_bloqueadas = 0
        for tm in recien_bloqueadas:
            admin = tm.tienda.administrador
            telegram_bot.notificar_bloqueo_impago(
                tm.tienda, admin, tm.fecha_vencimiento, _telefono_admin(admin)
            )
            n_bloqueadas += 1
        self.stdout.write(f'{n_bloqueadas} ruta(s) recién bloqueada(s) por impago.')

        # 2b. Trials que expiraron sin convertir (estaban Activa en trial y ya no, sin pago)
        n_trials = 0
        if ids_trial_activo_antes:
            trials_expirados = (
                Tienda_Membresia.objects
                .filter(
                    id__in=ids_trial_activo_antes,
                    tienda_id__in=ids_rutas_alertas,
                )
                .exclude(estado='Activa')
                .select_related('tienda', 'tienda__administrador')
            )
            for tm in trials_expirados:
                if PagoMembresia.objects.filter(tienda=tm.tienda).exists():
                    continue  # sí convirtió en algún momento
                admin = tm.tienda.administrador
                telegram_bot.notificar_trial_sin_convertir(
                    tm.tienda, admin, tm.fecha_vencimiento, _telefono_admin(admin)
                )
                n_trials += 1
        self.stdout.write(f'{n_trials} trial(es) expirado(s) sin convertir.')

        # 3. Solicitudes pre-activadas sin confirmar (+3 días)
        limite = timezone.now() - datetime.timedelta(days=3)
        expiradas = SolicitudPago.objects.filter(
            estado='pendiente_confirmacion',
            creada__lt=limite,
        ).update(estado='expirada')
        self.stdout.write(f'{expiradas} solicitud(es) marcada(s) como expiradas.')

        # 4. Comprobantes con más de 6 meses
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

        # 5. Archivar rutas vencidas hace 90+ días (reversible, no borra datos)
        limite_arch = hoy - datetime.timedelta(days=DIAS_PARA_ARCHIVAR)
        archivadas = Tienda_Membresia.objects.filter(
            archivada=False,
            estado='Vencida',
            fecha_vencimiento__lte=limite_arch,
        ).update(archivada=True, fecha_archivado=hoy)
        self.stdout.write(f'{archivadas} ruta(s) archivada(s) por inactividad ({DIAS_PARA_ARCHIVAR}+ días vencidas).')

        # 6. Resumen diario del negocio
        nuevos_usuarios = (
            User.objects.filter(date_joined__date=ayer, tienda__in=rutas_alertas)
            .distinct().count()
        )
        nuevas_rutas = rutas_alertas.filter(fecha_registro=ayer).count()
        membresias_alertas = Tienda_Membresia.objects.filter(
            tienda__in=rutas_alertas,
            archivada=False,
        )
        stats = {
            'nuevos_usuarios': nuevos_usuarios,
            'nuevas_rutas': nuevas_rutas,
            'rutas_activas': membresias_alertas.filter(estado='Activa').count(),
            'por_vencer': membresias_alertas.filter(
                estado='Activa',
                fecha_vencimiento__range=(hoy, hoy + datetime.timedelta(days=DIAS_POR_VENCER)),
            ).count(),
            'bloqueadas': n_bloqueadas,
            'trials_perdidos': n_trials,
            'ingreso_ayer': PagoMembresia.objects.filter(
                tienda__in=rutas_alertas,
                fecha=ayer,
            ).aggregate(
                t=Sum('monto'))['t'] or 0,
            'ingreso_semana': PagoMembresia.objects.filter(
                tienda__in=rutas_alertas,
                fecha__gte=hoy - datetime.timedelta(days=7)).aggregate(
                t=Sum('monto'))['t'] or 0,
        }
        if usuario_alertas:
            telegram_bot.notificar_resumen_diario(stats, fecha=ayer)
            self.stdout.write(
                f'Resumen de membresías de {usuario_alertas.username} '
                f'({rutas_alertas.count()} ruta(s)) enviado a Telegram.'
            )
        else:
            self.stdout.write(self.style.WARNING(
                'No se encontró el usuario configurado para alertas; '
                'se omitió el resumen de Telegram para evitar datos globales.'
            ))

        # Alertas de cartera y control operativo. Son idempotentes: cada
        # crédito avisa al cruzar los umbrales definidos para su frecuencia y
        # cada cierre ausente solo una vez por ruta y fecha.
        if usuario_alertas:
            # El proceso nocturno actualiza el panel, pero no envía una
            # notificación por cada crédito o falla. Telegram recibe un solo
            # resumen consolidado más abajo.
            nuevos_riesgos = revisar_riesgo_cartera(
                rutas=rutas_alertas,
                notificar=False,
            )
            cierres_ausentes = revisar_cierres_ausentes(
                ayer,
                rutas=rutas_alertas,
                notificar=False,
            )
            self.stdout.write(
                f'Usuario de alertas: {usuario_alertas.username} · '
                f'{rutas_alertas.count()} ruta(s).'
            )
            self.stdout.write(
                f'{nuevos_riesgos} alerta(s) nueva(s) de riesgo; '
                f'{cierres_ausentes} cierre(s) ausente(s).'
            )
            telegram_bot.notificar_resumen_operativo(
                resumen_operativo(ayer, rutas=rutas_alertas), ayer
            )
            self.stdout.write('Resumen operativo enviado a Telegram.')
        else:
            self.stdout.write(self.style.WARNING(
                'No se encontró el usuario configurado para alertas; '
                'se omitió el monitoreo operativo para evitar mezclar rutas.'
            ))

        self.stdout.write(self.style.SUCCESS('Mantenimiento completado.'))
