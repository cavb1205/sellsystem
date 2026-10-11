from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from Tiendas.permission_suscripcion import IsAuthenticatedOperativo as IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

import datetime
import logging
import re
from itertools import chain

from django.conf import settings
from django.utils import timezone

from django.core.files.base import ContentFile

from django.db.models import Sum, Count, Max, Q, OuterRef, Subquery, F, DecimalField
from django.db import transaction
from decimal import Decimal, ROUND_HALF_UP

from Clientes.models import Cliente
from Ventas.models import Venta
from Tiendas.models import AlertaOperativa, Tienda, Cierre_Caja, MovimientoCaja, Tienda_Membresia, Membresia, Tienda_Administrador, SolicitudPago, CuentaDestino, PagoMembresia, _generar_codigo_solicitud
from Tiendas.serializers import TiendaSerializer, CajaSerializer, MovimientoCajaSerializer, TiendaMembresiaSerializer, TiendaMembresiaListaSerializer, TiendaCreateSerializer, TiendaAdminSerializer, SolicitudPagoSerializer, CuentaDestinoSerializer, MembresiaSerializer
from Tiendas.alertas_operativas import rutas_del_usuario, usuario_alertas_configurado
from Tiendas.fecha_operativa import fecha_operativa, validar_zona_horaria
from Tiendas import telegram_bot
from Tiendas import telegram_assistant
from Tiendas.suscripciones import snapshot, registrar_cambio, anular_pago, pendientes
from Tiendas.seguridad_root import anotar_historial_ruta, tiene_historial_protegido
from Tiendas.configuracion_root import (CAMPOS_CUENTA, snapshot_cuenta, cuenta_completa,
    motivo_configuracion, registrar_configuracion, historial_configuracion)
from Tiendas.permissions import (
    requiere_acceso_tienda,
    usuario_puede_acceder_tienda,
    usuario_es_administrador_tienda,
    respuesta_sin_permiso,
)

logger = logging.getLogger(__name__)


def _sum_values(values):
    """Replica el resultado de Sum(...): 0 si no hay valores, Decimal si sí."""
    values = [value for value in values if value is not None]
    return sum(values, Decimal('0')) if values else 0


def _metricas_dashboard_tienda(tienda_id):
    """Calcula los indicadores del dashboard con consultas agrupadas.

    TiendaSerializer conserva una ruta histórica que ejecuta una consulta por
    cada indicador. Este bloque se usa únicamente en los detalles del
    dashboard y mantiene los mismos nombres y reglas de cálculo.
    """
    from Aportes.models import Aporte
    from Gastos.models import Gasto
    from Utilidades.models import Utilidad
    from Recaudos.models import Recaudo

    tienda = Tienda.objects.get(pk=tienda_id)
    hoy = fecha_operativa(tienda)
    ventas = list(Venta.objects.filter(tienda_id=tienda_id).values(
        'fecha_venta', 'valor_venta', 'interes', 'estado_venta', 'saldo_actual',
    ))

    def ventas_en(predicate):
        return [venta for venta in ventas if predicate(venta['fecha_venta'])]

    def total_ventas(rows):
        return _sum_values(venta['valor_venta'] for venta in rows)

    def utilidad_estimada(rows):
        # Mantiene la aritmética float usada por los métodos históricos.
        return sum(
            float(venta['valor_venta']) * (float(venta['interes']) / 100)
            for venta in rows
        )

    ventas_dia = ventas_en(lambda fecha: fecha == hoy)
    ventas_mes = ventas_en(lambda fecha: fecha.year == hoy.year and fecha.month == hoy.month)
    ventas_ano = ventas_en(lambda fecha: fecha.year == hoy.year)

    pagadas = [venta for venta in ventas if venta['estado_venta'] == 'Pagado']
    ingresos_finalizadas = sum(
        int(venta['valor_venta'] + (Decimal(venta['interes']) / Decimal(100)) * venta['valor_venta'])
        - int(venta['valor_venta'])
        for venta in pagadas
    )

    def movimiento_metricas(model):
        valores = model.objects.filter(tienda_id=tienda_id).aggregate(
            total=Sum('valor'),
            dia=Sum('valor', filter=Q(fecha=hoy)),
            mes=Sum('valor', filter=Q(fecha__year=hoy.year, fecha__month=hoy.month)),
            ano=Sum('valor', filter=Q(fecha__year=hoy.year)),
        )
        return {key: (value or 0) for key, value in valores.items()}

    aportes = movimiento_metricas(Aporte)
    gastos = movimiento_metricas(Gasto)
    utilidades = movimiento_metricas(Utilidad)
    recaudos_dia = Recaudo.objects.filter(
        tienda_id=tienda_id,
        fecha_recaudo=hoy,
    ).aggregate(total=Sum('valor_recaudo'))['total'] or 0

    saldo_cobrable = _sum_values(
        venta['saldo_actual']
        for venta in ventas
        if venta['estado_venta'] not in ('Pagado', 'Perdida')
    )
    perdidas = _sum_values(
        venta['saldo_actual']
        for venta in ventas
        if venta['estado_venta'] == 'Perdida'
    )
    perdidas_ano = _sum_values(
        venta['saldo_actual']
        for venta in ventas_ano
        if venta['estado_venta'] == 'Perdida'
    )
    perdidas_mes = _sum_values(
        venta['saldo_actual']
        for venta in ventas_mes
        if venta['estado_venta'] == 'Perdida'
    )

    return {
        'cantidad_clientes': Cliente.objects.filter(tienda_id=tienda_id).count(),
        'cantidad_ventas': len(ventas),
        'inversion': aportes['total'],
        'gastos': gastos['total'],
        'utilidades': utilidades['total'],
        'perdidas': perdidas,
        'ingresos_ventas_finalizadas': ingresos_finalizadas,
        'dinero_x_cobrar': saldo_cobrable,
        'aportes_dia': aportes['dia'],
        'gastos_dia': gastos['dia'],
        'utilidades_dia': utilidades['dia'],
        'recaudos_dia': recaudos_dia,
        'ventas_netas_dia': total_ventas(ventas_dia),
        'utilidad_estimada_dia': utilidad_estimada(ventas_dia),
        'aportes_mes': aportes['mes'],
        'gastos_mes': gastos['mes'],
        'utilidades_mes': utilidades['mes'],
        'ventas_netas_mes': total_ventas(ventas_mes),
        'utilidad_estimada_mes': utilidad_estimada(ventas_mes),
        'perdidas_mes': perdidas_mes,
        'aportes_ano': aportes['ano'],
        'gastos_ano': gastos['ano'],
        'utilidades_ano': utilidades['ano'],
        'ventas_netas_ano': total_ventas(ventas_ano),
        'perdidas_ano': perdidas_ano,
        'utilidad_estimada_ano': utilidad_estimada(ventas_ano),
    }


def _actualizar_estados_membresias():
    """Recalcula estados usando la zona horaria de cada ruta.

    Es intencionalmente un recorrido pequeño: las rutas pueden estar en
    países distintos y por eso un único ``timezone.localdate()`` del servidor
    no representa el mismo día para todas.
    """
    # Una lectura agrupada; solo las candidatas se recalculan bajo su bloqueo.
    # La revisión bajo bloqueo sigue siendo la autoridad ante renovaciones concurrentes.
    for tm in Tienda_Membresia.objects.select_related('tienda').all():
        if tm.vencida_manualmente:
            continue
        hoy = fecha_operativa(tm.tienda)
        temporal_vencida = tm.estado == 'Pre-activada' and (not tm.pre_activada_hasta or tm.pre_activada_hasta < hoy)
        normal_vencida = (tm.estado == 'Activa' and hoy >= tm.fecha_vencimiento + datetime.timedelta(days=1)
            or tm.estado == 'Pendiente Pago' and hoy >= tm.fecha_vencimiento + datetime.timedelta(days=2))
        if temporal_vencida or normal_vencida:
            comprobar_estado_membresia(tm.tienda_id)


def extender_membresia(tienda_id, plan_nombre):
    """Extiende la membresía desde max(fecha_vencimiento, hoy). Retorna la Tienda_Membresia."""
    if plan_nombre not in ('Mensual', 'Anual'):
        raise ValueError(f'Plan de membresía no válido: {plan_nombre}')
    tm = Tienda_Membresia.objects.select_for_update().get(tienda_id=tienda_id)
    membresia = Membresia.objects.filter(nombre=plan_nombre).first()
    if not membresia:
        raise ValueError(f'Plan de membresía no configurado: {plan_nombre}')
    dias = 30 if plan_nombre == 'Mensual' else 365
    hoy = fecha_operativa(tm.tienda)
    base = hoy if tm.vencida_manualmente else max(tm.fecha_vencimiento, hoy)
    tm.membresia = membresia
    tm.fecha_activacion = hoy
    tm.fecha_vencimiento = base + datetime.timedelta(days=dias)
    tm.estado = 'Activa'
    tm.pre_activada_hasta = None
    tm.vencida_manualmente = False
    tm.vencimiento_manual_fecha = None
    tm.motivo = ''
    # Si la ruta estaba archivada por inactividad, al pagar vuelve a estar visible
    tm.archivada = False
    tm.fecha_archivado = None
    tm.save()
    return tm


def _confirmar_solicitud(solicitud, revisor=None, monto_confirmado=None,
        permitir_expirada=False, motivo_excepcion=''):
    """Confirma una solicitud y extiende la membresía. Guarda el vencimiento previo
    para poder revertir con exactitud. revisor puede ser None (confirmado vía Telegram)."""
    # La misma normalización se aplica en web, Telegram y llamadas internas.
    _normalizar_solicitud_pago(solicitud)
    # La aprobación, la activación y el ingreso deben ser una sola operación.
    # Antes se guardaba la solicitud como confirmada antes de extender la ruta;
    # si algo fallaba después, quedaban estados incompatibles.
    with transaction.atomic():
        solicitud_db = (
            SolicitudPago.objects
            .select_for_update()
            .select_related('tienda', 'membresia')
            .get(pk=solicitud.pk)
        )
        if solicitud_db.estado == 'confirmada':
            return Tienda_Membresia.objects.select_for_update().get(tienda_id=solicitud_db.tienda_id)
        tm = Tienda_Membresia.objects.select_for_update().select_related('tienda').filter(tienda_id=solicitud_db.tienda_id).first()
        if tm is None:
            raise ValueError('La ruta no tiene una membresía configurada.')
        # El plazo puede vencer mientras se espera el bloqueo de otra sesión.
        fuera_plazo = solicitud_db.estado == 'expirada' or _solicitud_fuera_plazo(solicitud_db, tm)
        if fuera_plazo and not (permitir_expirada is True and revisor and revisor.is_superuser
                and solicitud_db.comprobante and isinstance(motivo_excepcion, str)
                and 5 <= len(motivo_excepcion.strip()) <= 500):
            raise ValueError('Solicitud expirada. Para confirmar este comprobante usa la excepción con motivo en el panel root.')
        if solicitud_db.estado != 'pendiente_confirmacion' and not fuera_plazo:
            raise ValueError('La solicitud ya no está disponible para confirmación.')
        if solicitud_db.monto_acordado is None:
            if not revisor or not revisor.is_superuser or monto_confirmado is None:
                raise ValueError('Solicitud anterior sin monto verificable. Revisa el comprobante y confirma su importe desde el panel root.')
            solicitud_db.monto_acordado = _precio_entero(monto_confirmado)
        elif monto_confirmado is not None and _precio_entero(monto_confirmado) != solicitud_db.monto_acordado:
            raise ValueError('El importe no coincide con el precio acordado de la solicitud.')

        fecha_vencimiento_previa = tm.fecha_vencimiento
        anterior = snapshot(tm)
        tm = extender_membresia(solicitud_db.tienda_id, solicitud_db.membresia.nombre)

        solicitud_db.fecha_vencimiento_previa = fecha_vencimiento_previa
        solicitud_db.estado = 'confirmada'
        solicitud_db.motivo_rechazo = ''
        solicitud_db.revisada_por = revisor
        solicitud_db.procesada = timezone.now()
        solicitud_db.confirmada_fuera_plazo = fuera_plazo
        solicitud_db.motivo_revision = motivo_excepcion.strip() if fuera_plazo else ''
        solicitud_db.save(update_fields=[
            'fecha_vencimiento_previa', 'estado', 'motivo_rechazo',
            'revisada_por', 'procesada',
            'monto_acordado',
            'confirmada_fuera_plazo', 'motivo_revision',
        ])

        # Registrar el ingreso en el libro de pagos (precio congelado al momento del cobro).
        pago, _ = PagoMembresia.objects.get_or_create(
            solicitud=solicitud_db,
            defaults={
                'tienda_id': solicitud_db.tienda_id,
                'tienda_nombre': solicitud_db.tienda.nombre,
                'membresia': solicitud_db.membresia,
                'monto': solicitud_db.monto_acordado,
                'fecha': fecha_operativa(solicitud_db.tienda),
                'origen': 'panel' if revisor else 'telegram',
                'registrado_por': revisor,
            },
        )
        registrar_cambio(tm, anterior, 'confirmacion_fuera_plazo' if fuera_plazo else 'confirmacion_pago',
            revisor, solicitud_db.motivo_revision, pago=pago,
            solicitud_codigo=solicitud_db.codigo)
        logger.info(
            'Solicitud de membresía %s confirmada (%s): tienda=%s vencimiento=%s',
            solicitud_db.codigo,
            'panel' if revisor else 'telegram',
            solicitud_db.tienda_id,
            tm.fecha_vencimiento,
        )
    return tm


def _revertir_solicitud(solicitud, motivo, revisor=None):
    """Revertir sin borrar el ingreso ni sobrescribir una corrección posterior."""
    with transaction.atomic():
        actual = SolicitudPago.objects.select_for_update().get(pk=solicitud.pk)
        if actual.estado not in ('pendiente_confirmacion', 'confirmada'):
            raise ValueError('La solicitud ya no está disponible para revisión.')
        tm = Tienda_Membresia.objects.select_for_update().select_related('tienda').filter(tienda=actual.tienda).first()
        era_confirmada = actual.estado == 'confirmada'
        pago = PagoMembresia.objects.select_for_update().filter(solicitud=actual, estado='vigente').first()
        cambio_pago = pago.cambiosuscripcion_set.order_by('-version').first() if pago else None
        if era_confirmada and tm and ((cambio_pago and cambio_pago.version != tm.version)
            or (not cambio_pago and tm.version > 0)):
            raise ValueError('La suscripción tuvo cambios posteriores. Revisa el detalle de la ruta antes de revertir este pago.')
        anterior = snapshot(tm) if tm else None
        actual.estado = 'rechazada'
        actual.motivo_rechazo = str(motivo)[:500]
        actual.motivo_revision = actual.motivo_rechazo
        actual.revisada_por = revisor
        actual.procesada = timezone.now()
        actual.save()
        if era_confirmada and pago:
            anular_pago(pago, actual.motivo_rechazo, revisor)
        if tm:
            # No eliminar un vencimiento manual aplicado posteriormente.
            if era_confirmada and not tm.vencida_manualmente and actual.fecha_vencimiento_previa:
                tm.fecha_vencimiento = actual.fecha_vencimiento_previa
            ultimo = tm.tienda.cambiosuscripcion_set.order_by('-version').first()
            preactivacion_propia = (ultimo and ultimo.accion == 'comprobante_recibido'
                and ultimo.solicitud_codigo == actual.codigo)
            if tm.estado == 'Pre-activada' and not tm.vencida_manualmente and (
                preactivacion_propia or (tm.version == 0 and not pendientes(tm).exists())):
                tm.estado = 'Pendiente Pago'
                tm.pre_activada_hasta = None
            # Una confirmación posterior a un vencimiento manual puede
            # revertirse exactamente gracias al snapshot de la confirmación.
            if era_confirmada and cambio_pago:
                previo = cambio_pago.anterior
                tm.membresia_id = previo['membresia_id']
                tm.fecha_activacion = datetime.date.fromisoformat(previo['fecha_activacion'])
                tm.estado = 'Pendiente Pago' if previo['estado'] == 'Pre-activada' else previo['estado']
                tm.fecha_vencimiento = datetime.date.fromisoformat(previo['fecha_vencimiento'])
                tm.vencida_manualmente = previo['vencida_manualmente']
                tm.vencimiento_manual_fecha = datetime.datetime.fromisoformat(previo['vencimiento_manual_fecha']) if previo['vencimiento_manual_fecha'] else None
                tm.motivo = previo['motivo']
                tm.pre_activada_hasta = None
                tm.archivada = previo['archivada']
                tm.fecha_archivado = datetime.date.fromisoformat(previo['fecha_archivado']) if previo['fecha_archivado'] else None
            if era_confirmada or snapshot(tm) != anterior:
                tm.save()
                registrar_cambio(tm, anterior, 'reversion_pago' if era_confirmada else 'rechazo_pago', revisor, actual.motivo_rechazo, pago,
                    solicitud_codigo=actual.codigo)
        solicitud.estado = actual.estado
        solicitud.procesada = actual.procesada
        solicitud.motivo_rechazo = actual.motivo_rechazo
    return tm


### VIEWS FOR TIENDA  ####

@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def list_all_tiendas(request):
    '''obtenemos todas las tiendas'''

    user = request.user
    if user.is_superuser:
        _normalizar_pendientes_revision()
        # Recalcular estados antes de listar — rutas inactivas pasan a Pendiente/Vencida automáticamente
        _actualizar_estados_membresias()
        vista_lista = request.GET.get('vista') == 'lista'
        tiendas = Tienda_Membresia.objects.all().order_by('fecha_vencimiento')
        # Por defecto se ocultan las archivadas; ?archivadas=1 las incluye
        if request.GET.get('archivadas') not in ('1', 'true'):
            tiendas = tiendas.filter(archivada=False)
        if vista_lista:
            from Recaudos.models import Recaudo
            ultimo_recaudo = Recaudo.objects.filter(
                tienda_id=OuterRef('tienda_id'),
            ).order_by('-fecha_recaudo').values('fecha_recaudo')[:1]
            ultimo_cierre = Cierre_Caja.objects.filter(
                tienda_id=OuterRef('tienda_id'),
            ).order_by('-fecha_cierre').values('fecha_cierre')[:1]
            cantidad_clientes = Cliente.objects.filter(
                tienda_id=OuterRef('tienda_id'),
            ).values('tienda_id').annotate(total=Count('id')).values('total')[:1]
            cantidad_ventas = Venta.objects.filter(
                tienda_id=OuterRef('tienda_id'),
            ).values('tienda_id').annotate(total=Count('id')).values('total')[:1]
            tiendas = tiendas.select_related(
                'tienda', 'tienda__administrador', 'membresia',
            ).annotate(
                _ult_recaudo=Subquery(ultimo_recaudo),
                _ult_cierre=Subquery(ultimo_cierre),
                _cantidad_clientes=Subquery(cantidad_clientes),
                _cantidad_ventas=Subquery(cantidad_ventas),
            )
            tiendas = anotar_historial_ruta(tiendas)
        else:
            # Compatibilidad del endpoint histórico para consumidores externos.
            tiendas = tiendas.annotate(
                _ult_recaudo=Max('tienda__recaudo__fecha_recaudo'),
                _ult_cierre=Max('tienda__cierre_caja__fecha_cierre'),
            )
        if tiendas:
            serializer_class = (
                TiendaMembresiaListaSerializer
                if vista_lista else TiendaMembresiaSerializer
            )
            serializer = serializer_class(tiendas, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response([] if vista_lista else {'message': 'No se han creado tiendas'}, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No tiene permisos para acceder a esta vista'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def list_tiendas_admin(request):
    '''return list stores for a admin user'''

    user = request.user
    if user.is_staff:
        tiendas = Tienda.objects.filter(administrador=user)
        if tiendas:
            serializer = TiendaSerializer(tiendas, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontraron tiendas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def list_tiendas(request):
    '''obtenemos todas las tiendas'''

    user = request.user
    tiendas = Tienda.objects.filter(id=user.perfil.tienda.id)

    if tiendas:
        serializer = TiendaSerializer(tiendas, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado tiendas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def get_tienda(request):

    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if tienda:
        serialize = TiendaSerializer(tienda, many=False)
        return Response(serialize.data)
    else:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_tienda(request, pk):
    tienda = Tienda.objects.filter(id=pk).first()
    if tienda:
        if not usuario_puede_acceder_tienda(request.user, pk):
            return respuesta_sin_permiso()
        serialize = TiendaSerializer(tienda, data=request.data)
        if serialize.is_valid():
            serialize.save()
            return Response(serialize.data, status=status.HTTP_200_OK)
        return Response(serialize.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
def patch_tienda_settings(request, pk):
    tienda = Tienda.objects.filter(id=pk).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    if not usuario_puede_acceder_tienda(request.user, pk):
        return respuesta_sin_permiso()
    if not usuario_es_administrador_tienda(request.user, pk):
        return Response(
            {'error': 'Solo un administrador puede cambiar la configuración de la ruta.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    allowed = ['prefijo_telefono', 'telefono', 'cupo_minimo_nuevo', 'zona_horaria']
    for field in allowed:
        if field in request.data:
            if field == 'zona_horaria':
                zona = validar_zona_horaria(request.data[field])
                if not zona:
                    return Response(
                        {'zona_horaria': 'Selecciona una zona horaria válida.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                request.data[field] = zona
            setattr(tienda, field, request.data[field])
    tienda.save()
    return Response(
        {
            'prefijo_telefono': tienda.prefijo_telefono,
            'telefono': tienda.telefono,
            'cupo_minimo_nuevo': tienda.cupo_minimo_nuevo,
            'zona_horaria': tienda.zona_horaria,
        },
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
def post_tienda(request):
    '''creamos una tienda'''
    if request.method == 'POST':
        # Verificar antes de guardar para que el flag sea correcto
        es_primera_tienda = not Tienda.objects.filter(administrador=request.user).exists()

        serialize = TiendaCreateSerializer(data=request.data)
        if serialize.is_valid():
            tienda = serialize.save()
            hoy = fecha_operativa(tienda)
            Cierre_Caja.objects.create(tienda=tienda, valor=tienda.caja_inicial, fecha_cierre=(
                hoy - datetime.timedelta(days=1)))

            if es_primera_tienda:
                # Solo la primera ruta recibe trial de 7 días
                Tienda_Membresia.objects.create(
                    tienda=tienda,
                    membresia=Membresia.objects.get(nombre='Prueba'),
                    fecha_activacion=hoy,
                    fecha_vencimiento=hoy + datetime.timedelta(days=7),
                    estado='Activa'
                )
            else:
                # Rutas adicionales nacen en Pendiente Pago — sin trial
                Tienda_Membresia.objects.create(
                    tienda=tienda,
                    membresia=Membresia.objects.get(nombre='Prueba'),
                    fecha_activacion=hoy,
                    fecha_vencimiento=hoy - datetime.timedelta(days=1),
                    estado='Pendiente Pago'
                )

            Tienda_Administrador.objects.create(
                tienda=tienda, administrador=request.user)

            if not es_primera_tienda:
                # Aviso al admin: usuario existente creó una ruta adicional
                total_rutas = Tienda.objects.filter(administrador=request.user).count()
                telegram_bot.notificar_nueva_ruta(tienda, request.user, total_rutas)

            return Response(serialize.data, status=status.HTTP_200_OK)
        return Response(serialize.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_tienda(request, pk):
    return _eliminar_ruta_protegida(request, pk)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_tienda_root(request, pk):
    return _eliminar_ruta_protegida(request, pk)


def _eliminar_ruta_protegida(request, pk):
    if not request.user.is_superuser:
        return respuesta_sin_permiso()
    with transaction.atomic():
        tienda = Tienda.objects.select_for_update().filter(pk=pk).first()
        if tienda is None:
            return Response({'error': 'No se encontró la ruta'}, status=404)
        if tiene_historial_protegido(tienda):
            return Response({'error': 'La ruta tiene saldo o historial operativo, financiero o de suscripción. Archívala para conservar los datos.',
                'code': 'ruta_con_historial'}, status=409)
        nombre = tienda.nombre
        tienda.delete()
    return Response({'message': f'Ruta "{nombre}" eliminada'}, status=status.HTTP_200_OK)

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove_tienda_admin(request, pk):
    '''Remove the Tienda_Administrador link for the current user — does not delete the tienda itself'''
    rel = Tienda_Administrador.objects.filter(tienda_id=pk, administrador=request.user).first()
    if rel:
        rel.delete()
        return Response({'message': 'Tienda quitada de tu lista'}, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontró la relación'}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
def get_tiendas_admin(request):
    '''return list stores for a admin user'''

    user = request.user
    if user.is_staff:
        tiendas = Tienda_Administrador.objects.filter(administrador=user)
        serializer = TiendaAdminSerializer(tiendas, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    return Response([], status=status.HTTP_200_OK)


def _serializar_alerta_operativa(alerta, rutas, clientes):
    ruta = rutas.get(alerta.tienda_id_ref)
    cliente = clientes.get(alerta.cliente_id_ref)
    cliente_nombre = None
    if cliente:
        cliente_nombre = f'{cliente.nombres} {cliente.apellidos}'.strip()
    datos = alerta.datos or {}
    senales = [
        {
            'codigo': señal.get('codigo'),
            'severidad': señal.get('severidad'),
            'texto': señal.get('texto'),
        }
        for señal in datos.get('senales', [])
        if isinstance(señal, dict)
    ]
    return {
        'id': alerta.id,
        'tipo': alerta.tipo,
        'severidad': alerta.severidad,
        'estado': alerta.estado,
        'clave_dedupe': alerta.clave_dedupe,
        'titulo': alerta.titulo,
        'detalle': alerta.detalle,
        'senales': senales,
        'datos': datos,
        'tienda': {
            'id': alerta.tienda_id_ref,
            'nombre': ruta.nombre if ruta else 'Ruta no disponible',
        },
        'cliente': {
            'id': alerta.cliente_id_ref,
            'nombre': cliente_nombre,
        } if alerta.cliente_id_ref else None,
        'venta_id': alerta.venta_id_ref,
        'trabajador': (
            alerta.trabajador.get_full_name() or alerta.trabajador.username
            if alerta.trabajador else None
        ),
        'telegram_message_id': alerta.telegram_message_id,
        'ocurrencias': alerta.ocurrencias,
        'creada': alerta.creada.isoformat(),
        'actualizada': alerta.actualizada.isoformat(),
        'ultima_notificacion': alerta.ultima_notificacion.isoformat()
        if alerta.ultima_notificacion else None,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def listar_alertas_operativas(request):
    """Lista alertas solo de las rutas administradas por el usuario actual."""
    usuario_alertas = usuario_alertas_configurado()
    if not usuario_alertas or request.user.id != usuario_alertas.id:
        return Response({'error': 'No tiene acceso a este centro de alertas'}, status=status.HTTP_403_FORBIDDEN)
    rutas = rutas_del_usuario(request.user)
    ruta_ids = list(rutas.values_list('id', flat=True))
    base = AlertaOperativa.objects.filter(
        tienda_id_ref__in=ruta_ids,
    ).select_related('trabajador')

    estado = request.query_params.get('estado', 'activas')
    if estado in ('activas', 'abiertas'):
        base = base.filter(estado__in=('nueva', 'revisada'))
    elif estado not in ('todas', 'todos', ''):
        base = base.filter(estado=estado)
    tipo = request.query_params.get('tipo')
    if tipo:
        base = base.filter(tipo=tipo)
    severidad = request.query_params.get('severidad')
    if severidad:
        base = base.filter(severidad=severidad)
    tienda_id = request.query_params.get('tienda')
    if tienda_id and tienda_id.isdigit() and int(tienda_id) in ruta_ids:
        base = base.filter(tienda_id_ref=int(tienda_id))

    try:
        limite = min(max(int(request.query_params.get('limit', 100)), 1), 200)
    except (TypeError, ValueError):
        limite = 100

    alertas = list(base.order_by('-id')[:limite])
    ruta_map = {ruta.id: ruta for ruta in rutas}
    cliente_ids = {a.cliente_id_ref for a in alertas if a.cliente_id_ref}
    clientes = {
        cliente.id: cliente
        for cliente in Cliente.objects.filter(id__in=cliente_ids)
    }
    return Response({
        'alertas': [
            _serializar_alerta_operativa(a, ruta_map, clientes)
            for a in alertas
        ],
        'rutas': [
            {'id': ruta.id, 'nombre': ruta.nombre}
            for ruta in rutas.order_by('nombre')
        ],
        'resumen': {
            'total': base.count(),
            'nuevas': base.filter(estado='nueva').count(),
            'criticas': base.filter(severidad='critica', estado='nueva').count(),
            'altas': base.filter(severidad='alta', estado='nueva').count(),
            'medias': base.filter(severidad='media', estado='nueva').count(),
        },
    }, status=status.HTTP_200_OK)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def cambiar_estado_alerta_operativa(request, pk):
    """Marca una alerta como nueva, revisada o resuelta dentro del alcance."""
    usuario_alertas = usuario_alertas_configurado()
    if not usuario_alertas or request.user.id != usuario_alertas.id:
        return Response({'error': 'No tiene acceso a este centro de alertas'}, status=status.HTTP_403_FORBIDDEN)
    estado = request.data.get('estado')
    if estado not in ('nueva', 'revisada', 'resuelta'):
        return Response(
            {'error': 'Estado inválido. Usa nueva, revisada o resuelta.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    ruta_ids = rutas_del_usuario(request.user).values_list('id', flat=True)
    alerta = AlertaOperativa.objects.filter(
        pk=pk,
        tienda_id_ref__in=ruta_ids,
    ).first()
    if not alerta:
        return Response({'error': 'Alerta no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    alerta.estado = estado
    alerta.save(update_fields=['estado', 'actualizada'])
    return Response({
        'id': alerta.id,
        'estado': alerta.estado,
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def auditoria_inconsistencias_tienda(request, tienda_id=None):
    """Detecta inconsistencias financieras actuales sin generar alertas.

    Este informe es deliberadamente de lectura: no cambia estados de ventas,
    no crea registros en AlertaOperativa y no envía Telegram. Compara el
    saldo guardado con el saldo que resulta del total del crédito menos todos
    los recaudos positivos registrados, incluyendo los cierres por renovación.
    Así el administrador puede revisar errores de datos, recaudos cruzados o
    saldos que no coinciden sin llenar el centro de alertas de duplicados.
    """
    from django.db.models.functions import Coalesce

    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = getattr(getattr(request.user, 'perfil', None), 'tienda', None)

    if not tienda:
        return Response({'error': 'Ruta no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    dinero_field = DecimalField(max_digits=14, decimal_places=2)
    fuera_de_ruta = ~Q(recaudo__tienda_id=F('tienda_id'))
    ventas = Venta.objects.filter(tienda_id=tienda.id).select_related('cliente').annotate(
        _abonos_registrados=Coalesce(
            Sum(
                'recaudo__valor_recaudo',
                filter=Q(recaudo__valor_recaudo__gt=0),
            ),
            Decimal('0'),
            output_field=dinero_field,
        ),
        _recaudos_fuera_ruta=Count('recaudo', filter=fuera_de_ruta),
        _recaudos_fuera_ruta_monto=Coalesce(
            Sum(
                'recaudo__valor_recaudo',
                filter=Q(recaudo__valor_recaudo__gt=0) & fuera_de_ruta,
            ),
            Decimal('0'),
            output_field=dinero_field,
        ),
        _recaudos_sin_motivo=Count(
            'recaudo',
            filter=Q(
                recaudo__valor_recaudo=0,
                recaudo__visita_blanco__isnull=True,
            ),
        ),
    ).order_by('id')

    severidad_orden = {'media': 1, 'alta': 2, 'critica': 3}
    casos = []
    por_tipo = {}
    monto_total = Decimal('0')

    for venta in ventas:
        saldo = venta.saldo_actual
        total = venta.total_a_pagar()
        abonos = venta._abonos_registrados or Decimal('0')
        esperado = total - abonos if saldo is not None else None
        señales = []

        def agregar_señal(codigo, titulo, detalle, monto=Decimal('0'), severidad='alta'):
            monto_decimal = monto if isinstance(monto, Decimal) else Decimal(str(monto or 0))
            señales.append({
                'codigo': codigo,
                'titulo': titulo,
                'detalle': detalle,
                'monto': float(max(Decimal('0'), monto_decimal)),
                'severidad': severidad,
            })

        if saldo is None:
            agregar_señal(
                'saldo_nulo',
                'Saldo no definido',
                'La venta no tiene saldo actual guardado; no se puede confirmar cuánto queda por cobrar.',
                total,
                'critica',
            )
        else:
            diferencia = saldo - esperado
            if saldo < Decimal('-1'):
                agregar_señal(
                    'sobreabono',
                    'Saldo negativo',
                    f'El crédito registra un saldo de ${abs(saldo):,.0f} por debajo de cero.',
                    abs(saldo),
                    'critica',
                )
            if abs(diferencia) > Decimal('1'):
                agregar_señal(
                    'saldo_desacoplado',
                    'Saldo no coincide con sus recaudos',
                    f'Saldo guardado ${saldo:,.0f}; saldo esperado ${esperado:,.0f} según total y recaudos.',
                    abs(diferencia),
                    'critica',
                )
            if venta.estado_venta == 'Pagado' and saldo > Decimal('1'):
                agregar_señal(
                    'estado_pagado_con_saldo',
                    'Pagado con saldo pendiente',
                    f'La venta aparece pagada, pero todavía registra ${saldo:,.0f} por cobrar.',
                    saldo,
                    'critica',
                )
            elif venta.estado_venta not in ('Pagado', 'Perdida') and saldo <= Decimal('1'):
                agregar_señal(
                    'estado_no_pagado_sin_saldo',
                    'Sin saldo, pero no aparece pagado',
                    f'La venta aparece como {venta.estado_venta}, aunque el saldo actual es ${max(saldo, Decimal("0")):,.0f}.',
                    Decimal('0'),
                    'alta',
                )

        if venta._recaudos_fuera_ruta:
            agregar_señal(
                'recaudo_fuera_de_ruta',
                'Recaudo registrado en otra ruta',
                f'{venta._recaudos_fuera_ruta} recaudo(s) de esta venta fueron registrados fuera de {tienda.nombre}.',
                venta._recaudos_fuera_ruta_monto or Decimal('0'),
                'critica',
            )

        if venta._recaudos_sin_motivo:
            agregar_señal(
                'recaudo_cero_sin_visita',
                'Visita sin recaudo y sin motivo',
                f'{venta._recaudos_sin_motivo} registro(s) tienen valor cero sin una visita fallida asociada.',
                Decimal('0'),
                'media',
            )

        if not señales:
            continue

        cliente_nombre = f'{venta.cliente.nombres} {venta.cliente.apellidos}'.strip()
        severidad = max(
            (señal['severidad'] for señal in señales),
            key=lambda valor: severidad_orden[valor],
        )
        monto_caso = max((Decimal(str(señal['monto'])) for señal in señales), default=Decimal('0'))
        principal = next(
            señal for señal in señales
            if severidad_orden[señal['severidad']] == severidad_orden[severidad]
        )
        casos.append({
            'codigo': principal['codigo'],
            'severidad': severidad,
            'titulo': principal['titulo'],
            'detalle': ' '.join(señal['detalle'] for señal in señales),
            'señales': señales,
            'venta_id': venta.id,
            'cliente': {
                'id': venta.cliente_id,
                'nombre': cliente_nombre,
            },
            'estado_venta': venta.estado_venta,
            'saldo_actual': float(saldo) if saldo is not None else None,
            'saldo_esperado': float(esperado) if esperado is not None else None,
            'monto_a_revisar': float(monto_caso),
        })
        monto_total += monto_caso
        for señal in señales:
            por_tipo[señal['codigo']] = por_tipo.get(señal['codigo'], 0) + 1

    casos.sort(key=lambda caso: (-severidad_orden[caso['severidad']], -caso['monto_a_revisar'], caso['venta_id']))
    return Response({
        'ruta': {'id': tienda.id, 'nombre': tienda.nombre},
        'resumen': {
            'casos': len(casos),
            'monto_a_revisar': float(monto_total),
            'por_tipo': por_tipo,
        },
        'casos': casos[:100],
        'actualizado': timezone.now().isoformat(),
    }, status=status.HTTP_200_OK)

### END VIEWS FOR TIENDA  ####

#### CIERRES DE CAJA#######


@api_view(['GET'])
@requiere_acceso_tienda
def get_cierres_caja(request, tienda_id=None):
    """obtenemos la lista de los cierres de caja"""
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(
            id=request.user.perfil.tienda.id).first()
    cierres_caja = Cierre_Caja.objects.filter(tienda=tienda.id).order_by('-id')
    if cierres_caja:
        serializer = CajaSerializer(cierres_caja, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontraron registros'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def list_movimientos_caja(request, tienda_id=None):
    """Lista el historial auditable de cambios de caja de una ruta."""
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if not tienda:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_404_NOT_FOUND)

    movimientos = MovimientoCaja.objects.filter(tienda_id=tienda.id).select_related('usuario')
    tipo = request.query_params.get('tipo')
    if tipo:
        movimientos = movimientos.filter(tipo=tipo)
    accion = request.query_params.get('accion')
    if accion:
        movimientos = movimientos.filter(accion=accion)
    desde = request.query_params.get('desde')
    if desde:
        movimientos = movimientos.filter(creado_en__date__gte=desde)
    hasta = request.query_params.get('hasta')
    if hasta:
        movimientos = movimientos.filter(creado_en__date__lte=hasta)

    try:
        limite = min(max(int(request.query_params.get('limit', 100)), 1), 200)
    except (TypeError, ValueError):
        limite = 100

    total = movimientos.count()
    movimientos = movimientos.order_by('-creado_en', '-id')[:limite]
    return Response({
        'results': MovimientoCajaSerializer(movimientos, many=True).data,
        'count': total,
        'limit': limite,
        'saldo_actual': tienda.caja_inicial,
        'historial_desde': MovimientoCaja.objects.filter(
            tienda_id=tienda.id,
        ).order_by('creado_en', 'id').values_list('creado_en', flat=True).first(),
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def get_caja_anterior(request, fecha, tienda_id=None):
    """obtenemos el valor de la caja del dia anterior a la fecha que recibimos como parametro"""
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(
            id=request.user.perfil.tienda.id).first()
    try:
        fecha = datetime.datetime.strptime(fecha, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return Response(
            {'error': 'Fecha inválida. Use el formato YYYY-MM-DD.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    dia_anterior = fecha - datetime.timedelta(days=1)
    caja_anterior = Cierre_Caja.objects.filter(
        tienda=tienda.id, fecha_cierre=dia_anterior).order_by('-id').first()
    if caja_anterior:
        serializer = CajaSerializer(caja_anterior, many=False)
        return Response(serializer.data, status=status.HTTP_200_OK)

    return Response({'message': 'No se encontraron registros'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@requiere_acceso_tienda
def post_cierre_caja(request, fecha, tienda_id=None):
    """guardamos el cierre de caja con la fecha, valor y tienda"""

    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    try:
        fecha = datetime.datetime.strptime(fecha, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return Response(
            {'error': 'Fecha inválida. Use el formato YYYY-MM-DD.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    with transaction.atomic():
        tienda = Tienda.objects.select_for_update().get(pk=tienda.pk)
        if Cierre_Caja.objects.filter(tienda=tienda, fecha_cierre=fecha).exists():
            return Response(
                {'error': 'Ya existe un cierre de caja para esa fecha.'},
                status=status.HTTP_409_CONFLICT,
            )
        Cierre_Caja.objects.create(
            fecha_cierre=fecha, valor=tienda.caja_inicial, tienda=tienda)
    return Response({'message': 'Se creo el registro'}, status=status.HTTP_200_OK)


@api_view(['DELETE'])
def delete_cierre_caja(request, pk):
    '''eliminamos un registro de cierre de caja'''

    cierre_caja = Cierre_Caja.objects.filter(id=pk).first()
    if not cierre_caja:
        return Response({'message': 'Cierre de caja no encontrado'}, status=status.HTTP_404_NOT_FOUND)
    if not usuario_puede_acceder_tienda(request.user, cierre_caja.tienda_id):
        return respuesta_sin_permiso()
    if not request.user.is_staff and not request.user.is_superuser:
        return Response(
            {'error': 'Solo un administrador puede eliminar cierres de caja.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    cierre_caja.delete()
    return Response({'message': 'Cierre Caja Eliminado'}, status=status.HTTP_200_OK)



####### MEMBRESIAS  ##########

@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def get_tienda_membresia(request):
    '''get info of store and info the acount store'''

    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    tienda_membresia = (
        Tienda_Membresia.objects
        .select_related('tienda', 'membresia')
        .filter(tienda=tienda)
        .first()
        if tienda else None
    )
    if tienda_membresia:
        if request.query_params.get('vista') == 'membresia':
            # Snapshot liviano y estrictamente de lectura para sesiones
            # abiertas: no recalcula cartera ni cambia estados por un GET.
            tm = tienda_membresia
            return Response({'id': tm.pk, 'fecha_activacion': tm.fecha_activacion,
                'fecha_vencimiento': tm.fecha_vencimiento, 'estado': tm.estado,
                'pre_activada_hasta': tm.pre_activada_hasta, 'archivada': tm.archivada,
                'fecha_archivado': tm.fecha_archivado, 'version': tm.version,
                'vencida_manualmente': tm.vencida_manualmente,
                'vencimiento_manual_fecha': tm.vencimiento_manual_fecha, 'motivo': tm.motivo},
                headers={'Cache-Control': 'no-store'})
        comprobar_estado_membresia(tienda.id)
        tienda_membresia.refresh_from_db()
        tienda_membresia.tienda._dashboard_metricas = _metricas_dashboard_tienda(tienda.id)
        serialize = TiendaMembresiaSerializer(tienda_membresia, many=False)
        return Response(serialize.data)
    else:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def get_tienda_membresia_admin(request, pk):
    '''get info of store and info the acount store for a admin user'''

    tienda = Tienda.objects.filter(id=pk).first()
    if not tienda:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)
    if not usuario_puede_acceder_tienda(request.user, pk):
        return respuesta_sin_permiso()
    tienda_membresia = (
        Tienda_Membresia.objects
        .select_related('tienda', 'membresia')
        .filter(tienda=tienda)
        .first()
    )
    if tienda_membresia:
        if request.query_params.get('vista') == 'membresia':
            tm = tienda_membresia
            return Response({'id': tm.pk, 'fecha_activacion': tm.fecha_activacion,
                'fecha_vencimiento': tm.fecha_vencimiento, 'estado': tm.estado,
                'pre_activada_hasta': tm.pre_activada_hasta, 'archivada': tm.archivada,
                'fecha_archivado': tm.fecha_archivado, 'version': tm.version,
                'vencida_manualmente': tm.vencida_manualmente,
                'vencimiento_manual_fecha': tm.vencimiento_manual_fecha, 'motivo': tm.motivo},
                headers={'Cache-Control': 'no-store'})
        comprobar_estado_membresia(tienda.id)
        tienda_membresia.refresh_from_db()
        tienda_membresia.tienda._dashboard_metricas = _metricas_dashboard_tienda(tienda.id)
        serialize = TiendaMembresiaSerializer(tienda_membresia, many=False)
        return Response(serialize.data)
    else:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@requiere_acceso_tienda
def dashboard_movimientos(request, date1, date2, tienda_id=None):
    """Entrega en una sola respuesta los movimientos del dashboard.

    La tarjeta "Stream de Actividad" solo necesita un subconjunto pequeño de
    cada modelo. Se evita serializar relaciones y metadatos que no se
    muestran, manteniendo la misma ventana de fechas que usaban las cuatro
    solicitudes originales.
    """
    from Aportes.models import Aporte
    from Gastos.models import Gasto
    from Utilidades.models import Utilidad

    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()

    if not tienda:
        return Response(
            {'message': 'No se encontró la tienda'},
            status=status.HTTP_404_NOT_FOUND,
        )

    aportes = Aporte.objects.filter(
        tienda_id=tienda.id,
        fecha__range=[date1, date2],
    ).values('id', 'comentario', 'valor', 'fecha')

    gastos = Gasto.objects.filter(
        tienda_id=tienda.id,
        fecha__range=[date1, date2],
    ).select_related('tipo_gasto').values(
        'id', 'tipo_gasto__tipo_gasto', 'valor', 'fecha',
    )

    utilidades = Utilidad.objects.filter(
        tienda_id=tienda.id,
        fecha__range=[date1, date2],
    ).values('id', 'valor', 'fecha')

    ventas = Venta.objects.filter(
        tienda_id=tienda.id,
        fecha_venta__range=[date1, date2],
    ).select_related('cliente').values(
        'id',
        'cliente__nombres',
        'cliente__apellidos',
        'valor_venta',
        'fecha_venta',
    )

    return Response({
        'aportes': list(aportes),
        'gastos': [
            {
                'id': gasto['id'],
                'tipo_gasto': {'tipo_gasto': gasto['tipo_gasto__tipo_gasto']},
                'valor': gasto['valor'],
                'fecha': gasto['fecha'],
            }
            for gasto in gastos
        ],
        'utilidades': list(utilidades),
        'ventas': [
            {
                'id': venta['id'],
                'cliente': {
                    'nombres': venta['cliente__nombres'],
                    'apellidos': venta['cliente__apellidos'],
                },
                'valor_venta': venta['valor_venta'],
                'fecha_venta': venta['fecha_venta'],
            }
            for venta in ventas
        ],
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def cierre_resumen_movimientos(request, fecha, tienda_id=None):
    """Resume los movimientos de una fecha y de hoy para Cierre de Caja.

    La pantalla solo necesita totales y cantidades. Las consultas históricas
    serializaban cinco listados completos por fecha y repetían el proceso para
    hoy; aquí cada modelo se agrega en una sola consulta, sin cargar filas.
    """
    from Aportes.models import Aporte
    from Gastos.models import Gasto
    from Recaudos.models import Recaudo
    from Utilidades.models import Utilidad

    try:
        fecha_obj = datetime.datetime.strptime(fecha, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return Response(
            {'message': 'Fecha inválida. Use el formato YYYY-MM-DD.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if not tienda:
        return Response(
            {'message': 'No se encontró la tienda'},
            status=status.HTTP_404_NOT_FOUND,
        )

    hoy = fecha_operativa(tienda)

    def agregar(model, valor_field, fecha_field):
        fechas = {
            'fecha': fecha_obj,
            'hoy': hoy,
        }
        agregados = model.objects.filter(tienda_id=tienda.id).aggregate(
            fecha_total=Sum(
                valor_field,
                filter=Q(**{fecha_field: fechas['fecha']}),
            ),
            fecha_cantidad=Count(
                'id',
                filter=Q(**{fecha_field: fechas['fecha']}),
            ),
            hoy_total=Sum(
                valor_field,
                filter=Q(**{fecha_field: fechas['hoy']}),
            ),
            hoy_cantidad=Count(
                'id',
                filter=Q(**{fecha_field: fechas['hoy']}),
            ),
        )
        return {
            'total': agregados['fecha_total'] or 0,
            'cantidad': agregados['fecha_cantidad'] or 0,
            'hoy_total': agregados['hoy_total'] or 0,
            'hoy_cantidad': agregados['hoy_cantidad'] or 0,
        }

    aportes = agregar(Aporte, 'valor', 'fecha')
    gastos = agregar(Gasto, 'valor', 'fecha')
    utilidades = agregar(Utilidad, 'valor', 'fecha')
    recaudos = agregar(Recaudo, 'valor_recaudo', 'fecha_recaudo')
    ventas = agregar(Venta, 'valor_venta', 'fecha_venta')

    fecha_data = {
        'aportes': aportes['total'],
        'gastos': gastos['total'],
        'utilidades': utilidades['total'],
        'recaudos': recaudos['total'],
        'count_recaudos': recaudos['cantidad'],
        'ventas': ventas['total'],
        'count_ventas': ventas['cantidad'],
        'tiene_movimientos': any(
            item['cantidad'] > 0
            for item in (aportes, gastos, utilidades, recaudos, ventas)
        ),
    }
    hoy_data = {
        'tiene_movimientos': any(
            item['hoy_cantidad'] > 0
            for item in (aportes, gastos, utilidades, recaudos, ventas)
        ),
    }
    return Response({
        'fecha': fecha_data,
        'hoy': hoy_data,
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def comparativo_mensual(request, inicio_a, fin_a, inicio_b, fin_b, tienda_id=None):
    """Calcula dos períodos del comparativo mensual en una sola respuesta.

    El reporte solo requiere agregados de ventas, gastos y aportes. Se cargan
    filas mínimas de los dos períodos y se calculan los mismos indicadores
    que el frontend histórico, sin serializar cada venta ni sus relaciones.
    """
    from Aportes.models import Aporte
    from Gastos.models import Gasto

    try:
        periodos = [
            (
                'mes_a',
                datetime.datetime.strptime(inicio_a, '%Y-%m-%d').date(),
                datetime.datetime.strptime(fin_a, '%Y-%m-%d').date(),
            ),
            (
                'mes_b',
                datetime.datetime.strptime(inicio_b, '%Y-%m-%d').date(),
                datetime.datetime.strptime(fin_b, '%Y-%m-%d').date(),
            ),
        ]
    except (TypeError, ValueError):
        return Response(
            {'message': 'Fechas inválidas. Use el formato YYYY-MM-DD.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if any(inicio > fin for _, inicio, fin in periodos):
        return Response(
            {'message': 'El inicio de cada período no puede ser mayor que su fin.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if not tienda:
        return Response(
            {'message': 'No se encontró la tienda'},
            status=status.HTTP_404_NOT_FOUND,
        )

    ventas_q = Q(fecha_venta__range=[periodos[0][1], periodos[0][2]]) | Q(
        fecha_venta__range=[periodos[1][1], periodos[1][2]],
    )
    gastos_q = Q(fecha__range=[periodos[0][1], periodos[0][2]]) | Q(
        fecha__range=[periodos[1][1], periodos[1][2]],
    )
    aportes_q = Q(fecha__range=[periodos[0][1], periodos[0][2]]) | Q(
        fecha__range=[periodos[1][1], periodos[1][2]],
    )

    ventas = list(Venta.objects.filter(tienda_id=tienda.id).filter(ventas_q).values(
        'fecha_venta', 'valor_venta', 'interes', 'estado_venta', 'saldo_actual',
    ))
    gastos = list(Gasto.objects.filter(tienda_id=tienda.id).filter(gastos_q).values(
        'fecha', 'valor',
    ))
    aportes = list(Aporte.objects.filter(tienda_id=tienda.id).filter(aportes_q).values(
        'fecha', 'valor',
    ))

    def en_periodo(rows, field, inicio, fin):
        return [row for row in rows if inicio <= row[field] <= fin]

    def resumir(inicio, fin):
        ventas_periodo = en_periodo(ventas, 'fecha_venta', inicio, fin)
        gastos_periodo = en_periodo(gastos, 'fecha', inicio, fin)
        aportes_periodo = en_periodo(aportes, 'fecha', inicio, fin)

        cantidad_ventas = len(ventas_periodo)
        total_vendido = _sum_values(row['valor_venta'] for row in ventas_periodo)
        intereses = sum(
            (
                row['valor_venta'] * Decimal(row['interes']) / Decimal('100')
                for row in ventas_periodo
            ),
            Decimal('0'),
        )
        perdidas = _sum_values(
            row['saldo_actual']
            for row in ventas_periodo
            if row['estado_venta'] == 'Perdida'
        )
        total_gastos = _sum_values(row['valor'] for row in gastos_periodo)
        total_aportes = _sum_values(row['valor'] for row in aportes_periodo)
        utilidad = intereses - total_gastos - perdidas

        return {
            'cantidadVentas': cantidad_ventas,
            'totalVendido': total_vendido,
            'intereses': intereses,
            'totalGastos': total_gastos,
            'perdidas': perdidas,
            'totalAportes': total_aportes,
            'utilidad': utilidad,
            # Se conserva la respuesta histórica: el frontend recibía
            # g.categoria, campo inexistente en el serializer actual, y por
            # eso agrupaba todos los gastos bajo "Sin categoría".
            'categorias': {'Sin categoría': total_gastos} if gastos_periodo else {},
            'margen': float((utilidad / intereses) * Decimal('100')) if intereses > 0 else 0,
        }

    return Response({
        'mes_a': resumir(periodos[0][1], periodos[0][2]),
        'mes_b': resumir(periodos[1][1], periodos[1][2]),
    }, status=status.HTTP_200_OK)


def _desglosar_abono_capital_interes(capital, interes_total, acumulado, abono):
    """Aplica un abono primero al capital y luego al interés.

    Es una función pura: no toca ventas, saldos ni recaudos. Devuelve la
    porción de capital y de interés que corresponde exclusivamente al abono
    recibido, limitando ambas partes al monto originalmente pactado.
    """
    cero = Decimal('0')
    capital = max(Decimal(capital or 0), cero)
    interes_total = max(Decimal(interes_total or 0), cero)
    acumulado = max(Decimal(acumulado or 0), cero)
    abono = max(Decimal(abono or 0), cero)

    total_despues = acumulado + abono
    capital_antes = min(acumulado, capital)
    capital_despues = min(total_despues, capital)
    interes_antes = min(max(acumulado - capital, cero), interes_total)
    interes_despues = min(max(total_despues - capital, cero), interes_total)

    return (
        capital_despues - capital_antes,
        interes_despues - interes_antes,
    )


@api_view(['GET'])
@requiere_acceso_tienda
def reporte_utilidad_diario(request, date1, date2, tienda_id=None):
    """Devuelve el reporte diario de utilidad ya procesado en el backend.

    La pantalla solo necesita una fila por fecha. Se cargan campos mínimos de
    ventas y movimientos, se conserva el redondeo monetario histórico del
    frontend y se evita enviar listados completos para que el navegador los
    agrupe.

    ``utilidadEstimada`` conserva la fórmula histórica para comparación.
    ``utilidadCobrada`` reconoce únicamente el interés que efectivamente
    quedó cobrado después de recuperar el capital de cada venta, menos gastos.
    La conciliación separa los recaudos registrados en la ruta de los pagos
    aplicados a sus ventas y marca movimientos fuera de ruta, de otras rutas
    o ajustes negativos para revisión.
    Ninguno de estos cálculos modifica saldos ni recaudos.
    """
    from Aportes.models import Aporte
    from Gastos.models import Gasto
    from Recaudos.models import Recaudo
    from Utilidades.models import Utilidad

    try:
        inicio = datetime.datetime.strptime(date1, '%Y-%m-%d').date()
        fin = datetime.datetime.strptime(date2, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return Response(
            {'message': 'Fechas inválidas. Use el formato YYYY-MM-DD.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if inicio > fin:
        return Response(
            {'message': 'La fecha de inicio no puede ser mayor que la fecha de fin.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if not tienda:
        return Response(
            {'message': 'No se encontró la tienda'},
            status=status.HTTP_404_NOT_FOUND,
        )

    ventas = Venta.objects.filter(
        tienda_id=tienda.id,
        fecha_venta__range=[inicio, fin],
    ).annotate(
        _total_abonos=Sum(
            'recaudo__valor_recaudo',
            filter=Q(
                recaudo__valor_recaudo__gt=0,
                recaudo__es_renovacion=False,
            ),
        ),
    ).values(
        'fecha_venta', 'valor_venta', 'interes', 'estado_venta', 'saldo_actual',
        '_total_abonos',
    )
    gastos = Gasto.objects.filter(
        tienda_id=tienda.id,
        fecha__range=[inicio, fin],
    ).values('fecha', 'valor', 'tipo_gasto__tipo_gasto')
    recaudos = Recaudo.objects.filter(
        tienda_id=tienda.id,
        fecha_recaudo__range=[inicio, fin],
        valor_recaudo__gt=0,
        es_renovacion=False,
    ).values('fecha_recaudo', 'valor_recaudo')
    recaudos_para_desglose = list(
        Recaudo.objects.filter(
            venta__tienda_id=tienda.id,
            fecha_recaudo__range=[inicio, fin],
            valor_recaudo__gt=0,
            es_renovacion=False,
        ).values(
            'id',
            'venta_id',
            'fecha_recaudo',
            'valor_recaudo',
            'venta__valor_venta',
            'venta__interes',
        ).order_by('fecha_recaudo', 'id')
    )
    ventas_con_abonos = {recaudo['venta_id'] for recaudo in recaudos_para_desglose}
    acumulados_anteriores = {}
    if ventas_con_abonos:
        acumulados_anteriores = {
            fila['venta_id']: fila['total'] or Decimal('0')
            for fila in Recaudo.objects.filter(
                venta__tienda_id=tienda.id,
                venta_id__in=ventas_con_abonos,
                fecha_recaudo__lt=inicio,
                valor_recaudo__gt=0,
                es_renovacion=False,
        ).values('venta_id').annotate(total=Sum('valor_recaudo'))
        }
    recaudos_auditoria = Recaudo.objects.filter(
        Q(tienda_id=tienda.id) | Q(venta__tienda_id=tienda.id),
        fecha_recaudo__range=[inicio, fin],
        es_renovacion=False,
        valor_recaudo__isnull=False,
    ).values(
        'fecha_recaudo', 'valor_recaudo', 'tienda_id', 'venta_id',
        'venta__tienda_id',
    )
    aportes = Aporte.objects.filter(
        tienda_id=tienda.id,
        fecha__range=[inicio, fin],
    ).values('fecha', 'valor')
    utilidades = Utilidad.objects.filter(
        tienda_id=tienda.id,
        fecha__range=[inicio, fin],
    ).values('fecha', 'valor')

    def redondear_monto(value):
        if value is None:
            return 0
        return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    datos_por_fecha = {}

    def fila(fecha):
        clave = fecha.isoformat()
        if clave not in datos_por_fecha:
            datos_por_fecha[clave] = {
                'fecha': clave,
                'cantidadVentas': 0,
                'totalVendido': 0,
                'interesesGenerados': 0,
                'gastos': 0,
                'perdidas': 0,
                'perdidaCapital': 0,
                'interesNoCobrado': 0,
                'utilidad': 0,
                'utilidadEstimada': 0,
                'capitalRecuperado': 0,
                'interesesCobrados': 0,
                'utilidadCobrada': 0,
                'recaudos': 0,
                'recaudosAplicados': 0,
                'recaudosConciliados': 0,
                'recaudosFueraRuta': 0,
                'recaudosDeOtrasRutas': 0,
                'recaudosSinVenta': 0,
                'recaudosNegativos': 0,
                'recaudosPorRevisar': 0,
                'aportes': 0,
                'utilidadesRetiradas': 0,
                'categoriasGastos': {},
            }
        return datos_por_fecha[clave]

    for venta in ventas:
        datos = fila(venta['fecha_venta'])
        valor_venta = redondear_monto(venta['valor_venta'])
        total_a_pagar = redondear_monto(
            venta['valor_venta']
            + (Decimal(venta['interes']) / Decimal('100')) * venta['valor_venta']
        )
        datos['cantidadVentas'] += 1
        datos['totalVendido'] += valor_venta
        interes_total = total_a_pagar - valor_venta
        datos['interesesGenerados'] += interes_total
        if venta['estado_venta'] == 'Perdida':
            datos['perdidas'] += redondear_monto(venta['saldo_actual'])
            total_abonos = max(Decimal(venta['_total_abonos'] or 0), Decimal('0'))
            capital_recuperado = min(total_abonos, venta['valor_venta'])
            interes_cobrado = min(
                max(total_abonos - venta['valor_venta'], Decimal('0')),
                interes_total,
            )
            datos['perdidaCapital'] += redondear_monto(
                max(venta['valor_venta'] - capital_recuperado, Decimal('0'))
            )
            datos['interesNoCobrado'] += redondear_monto(
                max(interes_total - interes_cobrado, Decimal('0'))
            )

    for gasto in gastos:
        datos = fila(gasto['fecha'])
        valor_gasto = redondear_monto(gasto['valor'])
        datos['gastos'] += valor_gasto
        categoria = gasto['tipo_gasto__tipo_gasto'] or 'Sin categoría'
        datos['categoriasGastos'][categoria] = (
            datos['categoriasGastos'].get(categoria, 0) + valor_gasto
        )

    acumulados_por_venta = dict(acumulados_anteriores)
    for recaudo in recaudos_para_desglose:
        acumulado = acumulados_por_venta.get(recaudo['venta_id'], Decimal('0'))
        capital = recaudo['venta__valor_venta']
        interes_total = (
            Decimal(recaudo['venta__interes']) / Decimal('100')
        ) * capital
        capital_recuperado, intereses_cobrados = _desglosar_abono_capital_interes(
            capital,
            interes_total,
            acumulado,
            recaudo['valor_recaudo'],
        )
        datos = fila(recaudo['fecha_recaudo'])
        datos['capitalRecuperado'] += redondear_monto(capital_recuperado)
        datos['interesesCobrados'] += redondear_monto(intereses_cobrados)
        acumulados_por_venta[recaudo['venta_id']] = (
            acumulado + recaudo['valor_recaudo']
        )

    for recaudo in recaudos:
        datos = fila(recaudo['fecha_recaudo'])
        datos['recaudos'] += redondear_monto(recaudo['valor_recaudo'])

    for recaudo in recaudos_auditoria:
        datos = fila(recaudo['fecha_recaudo'])
        valor_recaudo = Decimal(recaudo['valor_recaudo'] or 0)
        registrado_en_ruta = recaudo['tienda_id'] == tienda.id
        pertenece_a_ruta = recaudo['venta__tienda_id'] == tienda.id

        if valor_recaudo < 0:
            # Los ajustes negativos no se mezclan con los recaudos efectivos;
            # se muestran como movimientos que requieren revisión.
            datos['recaudosNegativos'] += redondear_monto(valor_recaudo)
            continue
        if valor_recaudo <= 0:
            continue

        monto = redondear_monto(valor_recaudo)
        if pertenece_a_ruta:
            datos['recaudosAplicados'] += monto
        if registrado_en_ruta and pertenece_a_ruta:
            datos['recaudosConciliados'] += monto
        elif pertenece_a_ruta and not registrado_en_ruta:
            datos['recaudosFueraRuta'] += monto
        elif registrado_en_ruta and recaudo['venta__tienda_id'] is None:
            datos['recaudosSinVenta'] += monto
        elif registrado_en_ruta:
            datos['recaudosDeOtrasRutas'] += monto

    for aporte in aportes:
        datos = fila(aporte['fecha'])
        datos['aportes'] += redondear_monto(aporte['valor'])

    for utilidad_retirada in utilidades:
        datos = fila(utilidad_retirada['fecha'])
        datos['utilidadesRetiradas'] += redondear_monto(utilidad_retirada['valor'])

    for datos in datos_por_fecha.values():
        datos['recaudosPorRevisar'] = (
            datos['recaudosFueraRuta']
            + datos['recaudosDeOtrasRutas']
            + datos['recaudosSinVenta']
            + abs(datos['recaudosNegativos'])
        )
        datos['utilidad'] = (
            datos['interesesGenerados']
            - datos['gastos']
            - datos['perdidas']
        )
        # La comparación estimada usa la pérdida económica real: capital no
        # recuperado. El campo ``utilidad`` conserva la fórmula histórica.
        datos['utilidadEstimada'] = (
            datos['interesesGenerados']
            - datos['gastos']
            - datos['perdidaCapital']
        )
        datos['utilidadCobrada'] = (
            datos['interesesCobrados']
            - datos['gastos']
        )

    filas = sorted(datos_por_fecha.values(), key=lambda item: item['fecha'], reverse=True)
    if request.query_params.get('version') == '2':
        from Tiendas.reporte_historico import ampliar_reporte
        return Response(ampliar_reporte(tienda, inicio, fin, filas, fila))
    return Response(filas, status=status.HTTP_200_OK)

def _registrar_pago_manual(tienda_membresia, user):
    """Un ingreso por activación. La versión bloqueada evita reintentos."""
    return PagoMembresia.objects.create(
        tienda=tienda_membresia.tienda,
        membresia=tienda_membresia.membresia,
        fecha=fecha_operativa(tienda_membresia.tienda),
        origen='manual',
        tienda_nombre=tienda_membresia.tienda.nombre,
        monto=tienda_membresia.membresia.precio,
        registrado_por=user if getattr(user, 'is_authenticated', False) else None,
    )


def _activar_manual(request, pk, plan):
    if not request.user.is_superuser:
        return respuesta_sin_permiso()
    if not isinstance(request.data, dict):
        return Response({'error': 'Se espera un objeto JSON.'}, status=400)
    expected = request.data.get('version')
    if expected is not None and (type(expected) is not int or expected < 0):
        return Response({'error': 'Versión de suscripción inválida.'}, status=400)
    with transaction.atomic():
        tm = Tienda_Membresia.objects.select_for_update().select_related('tienda').filter(pk=pk).first()
        if tm is None:
            return Response({'error': 'No se encontró la suscripción.'}, status=404)
        if expected is not None and expected != tm.version:
            return Response({'error': 'La suscripción cambió. Recarga antes de confirmar.',
                'code': 'suscripcion_modificada'}, status=409)
        # Compatibilidad con pantallas antiguas sin versión: un reintento del
        # mismo día/plan no puede añadir meses sin otro ingreso identificado.
        if expected is None and tm.estado == 'Activa' and not tm.vencida_manualmente and PagoMembresia.objects.filter(
            tienda=tm.tienda, membresia__nombre=plan, fecha=fecha_operativa(tm.tienda),
            origen='manual', estado='vigente',
        ).exists():
            return Response({'message': 'Esta activación manual ya está registrada.', 'version': tm.version})
        anterior = snapshot(tm)
        try:
            tm = extender_membresia(tm.tienda_id, plan)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)
        pago = _registrar_pago_manual(tm, request.user)
        registrar_cambio(tm, anterior, 'activacion_manual', request.user, pago=pago)
    return Response({'message': f'Suscripción {plan} activa', 'version': tm.version,
        'fecha_vencimiento': tm.fecha_vencimiento})


@api_view(['POST'])
def activar_membresia_mensual(request, pk):
    '''get store and activate membershi for a mounth + 30 days'''
    return _activar_manual(request, pk, 'Mensual')
    
@api_view(['POST'])
def activar_membresia_ano(request, pk):
    '''get store and activate membershi for a year + 365 days'''
    return _activar_manual(request, pk, 'Anual')

####### SOLICITUDES DE PAGO (membresías automáticas vía WhatsApp) ##########

def _marcar_solicitud_expirada(solicitud):
    """Expira una solicitud y deja el botón de Telegram inutilizable."""
    with transaction.atomic():
        solicitud_db = SolicitudPago.objects.select_for_update().get(pk=solicitud.pk)
        if solicitud_db.estado not in ('pendiente', 'pendiente_confirmacion'):
            # Otra operación pudo confirmarla o rechazarla mientras se revisaba.
            # Nunca sobrescribir ese resultado con una expiración.
            solicitud.estado = solicitud_db.estado
            solicitud.procesada = solicitud_db.procesada
            return False

        solicitud_db.estado = 'expirada'
        solicitud_db.procesada = timezone.now()
        solicitud_db.save(update_fields=['estado', 'procesada'])

        tm = (
            Tienda_Membresia.objects
            .select_for_update()
            .filter(tienda_id=solicitud_db.tienda_id)
            .first()
        )
        ultimo = tm.tienda.cambiosuscripcion_set.order_by('-version').first() if tm else None
        preactivacion_propia = ultimo and ultimo.accion == 'comprobante_recibido' and ultimo.solicitud_codigo == solicitud_db.codigo
        if tm and tm.estado == 'Pre-activada' and (preactivacion_propia
                or (tm.version == 0 and not pendientes(tm).exclude(pk=solicitud_db.pk).exists())):
            anterior = snapshot(tm)
            tm.estado = 'Pendiente Pago'
            tm.pre_activada_hasta = None
            tm.save(update_fields=['estado', 'pre_activada_hasta'])
            registrar_cambio(tm, anterior, 'solicitud_expirada', solicitud_codigo=solicitud_db.codigo)

        solicitud.estado = solicitud_db.estado
        solicitud.procesada = solicitud_db.procesada

    # Si Telegram no responde, la expiración en base de datos se conserva y el
    # siguiente toque del botón volverá a mostrar el mensaje correcto.
    telegram_bot.marcar_expirada(solicitud)
    return True


def _solicitud_fuera_plazo(solicitud, tm=None):
    ahora = timezone.now()
    if solicitud.estado == 'pendiente':
        return ahora >= solicitud.expira
    if solicitud.estado != 'pendiente_confirmacion':
        return False
    return ahora >= solicitud.creada + datetime.timedelta(days=3) or bool(
        tm and tm.estado == 'Pre-activada' and tm.pre_activada_hasta
        and fecha_operativa(tm.tienda) > tm.pre_activada_hasta)


def _normalizar_solicitud_pago(solicitud):
    """Expira solicitudes desde cualquier endpoint, no solo desde el cron.

    Esto evita que un código vencido pueda volver a preactivar una ruta si el
    mantenimiento diario todavía no ha corrido.
    """
    tm = Tienda_Membresia.objects.select_related('tienda').filter(tienda_id=solicitud.tienda_id).first() if solicitud.estado == 'pendiente_confirmacion' else None
    if _solicitud_fuera_plazo(solicitud, tm):
        _marcar_solicitud_expirada(solicitud)
    return solicitud


def _normalizar_pendientes_revision():
    for solicitud in SolicitudPago.objects.filter(estado='pendiente_confirmacion').select_related('tienda__tienda_membresia'):
        tm = getattr(solicitud.tienda, 'tienda_membresia', None)
        if _solicitud_fuera_plazo(solicitud, tm):
            _marcar_solicitud_expirada(solicitud)


def _datos_solicitud_pago(solicitud):
    """Respuesta común para crear y recuperar una solicitud de pago."""
    numero_soporte = getattr(settings, 'WHATSAPP_SOPORTE_NUMERO', '')
    texto_wa = (
        f"Hola, necesito ayuda con el pago de mi plan, tienda *{solicitud.tienda.nombre}* "
        f"(código {solicitud.codigo})."
    )
    return {
        'codigo': solicitud.codigo,
        'expira': solicitud.expira,
        'monto': str(solicitud.monto_acordado) if solicitud.monto_acordado is not None else None,
        'requiere_revision_monto': solicitud.monto_acordado is None,
        'plan': solicitud.membresia.nombre,
        'estado': solicitud.estado,
        'motivo_rechazo': solicitud.motivo_rechazo,
        'wa_link': f"https://wa.me/{numero_soporte}?text={texto_wa.replace(' ', '%20')}" if numero_soporte else '',
        'cuenta': solicitud.cuenta_acordada or None,
        'cuenta_historica_verificable': bool(solicitud.cuenta_acordada),
    }

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def solicitar_pago(request):
    """Crea una SolicitudPago y devuelve el código + datos de cuenta para que el usuario pague."""
    membresia_id = request.data.get('membresia_id')
    plan_nombre = request.data.get('plan')

    if membresia_id:
        try:
            membresia = Membresia.objects.get(id=membresia_id)
        except Membresia.DoesNotExist:
            return Response({'error': 'Membresía no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    elif plan_nombre in ('Mensual', 'Anual'):
        membresia = Membresia.objects.filter(nombre=plan_nombre).first()
        if not membresia:
            return Response({'error': f'Plan {plan_nombre} no configurado'}, status=status.HTTP_404_NOT_FOUND)
    else:
        return Response({'error': 'membresia_id o plan requerido'}, status=status.HTTP_400_BAD_REQUEST)

    perfil = getattr(request.user, 'perfil', None)
    tienda_id = request.data.get('tienda_id') or getattr(perfil, 'tienda_id', None)
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'error': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    if not usuario_puede_acceder_tienda(request.user, tienda_id):
        return respuesta_sin_permiso()

    solicitud_existente = (
        SolicitudPago.objects
        .filter(tienda=tienda, estado__in=('pendiente', 'pendiente_confirmacion'))
        .select_related('tienda', 'membresia')
        .order_by('-creada')
        .first()
    )
    if solicitud_existente:
        _normalizar_solicitud_pago(solicitud_existente)
        if solicitud_existente.estado in ('pendiente', 'pendiente_confirmacion'):
            return Response(_datos_solicitud_pago(solicitud_existente), status=status.HTTP_200_OK)

    codigo = _generar_codigo_solicitud(membresia.nombre)
    expira = timezone.now() + datetime.timedelta(hours=24)

    with transaction.atomic():
        cuenta = CuentaDestino.get()
        cuenta = CuentaDestino.objects.select_for_update().get(pk=cuenta.pk)
        if not cuenta_completa(cuenta):
            return Response({'error': 'Los datos bancarios aún no están completos. Contacta con soporte antes de transferir.'}, status=409)
        membresia = Membresia.objects.select_for_update().get(pk=membresia.pk)
        solicitud = SolicitudPago.objects.create(
            tienda=tienda, membresia=membresia, monto_acordado=membresia.precio,
            codigo=codigo, expira=expira, solicitada_por=request.user,
            cuenta_acordada=snapshot_cuenta(cuenta),
        )

    return Response(_datos_solicitud_pago(solicitud), status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def solicitud_pago_actual(request, tienda_id):
    """Recupera la solicitud vigente de una ruta para no perderla al recargar."""
    if not usuario_puede_acceder_tienda(request.user, tienda_id):
        return respuesta_sin_permiso()

    solicitud = (
        SolicitudPago.objects
        .filter(tienda_id=tienda_id, estado__in=('pendiente', 'pendiente_confirmacion'))
        .select_related('tienda', 'membresia')
        .order_by('-creada')
        .first()
    )
    if not solicitud:
        return Response({'solicitud': None}, status=status.HTTP_200_OK)

    _normalizar_solicitud_pago(solicitud)
    if solicitud.estado not in ('pendiente', 'pendiente_confirmacion'):
        return Response({'solicitud': None}, status=status.HTTP_200_OK)
    return Response({'solicitud': _datos_solicitud_pago(solicitud)}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancelar_solicitud_pago(request, codigo):
    """Cancela una solicitud pendiente para permitir elegir otro plan."""
    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        return Response({'error': 'Solicitud no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    if not usuario_puede_acceder_tienda(request.user, solicitud.tienda_id):
        return respuesta_sin_permiso()

    _normalizar_solicitud_pago(solicitud)
    if solicitud.estado != 'pendiente':
        return Response({'error': 'Solo se puede cancelar una solicitud pendiente de comprobante.'}, status=status.HTTP_409_CONFLICT)

    solicitud.estado = 'cancelada'
    solicitud.procesada = timezone.now()
    solicitud.save(update_fields=['estado', 'procesada'])
    return Response({'estado': solicitud.estado}, status=status.HTTP_200_OK)


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def cuenta_destino(request):
    """Datos bancarios para pagos. GET y PUT solo para el superusuario (root)."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

    cuenta = CuentaDestino.get()
    if request.method == 'GET':
        return Response({**CuentaDestinoSerializer(cuenta).data,
            'configurada': bool(cuenta_completa(cuenta)), 'historial': historial_configuracion('cuenta')})

    data = request.data
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] < 0:
        return Response({'error': 'Recarga la cuenta para obtener una versión válida.'}, status=400)
    try:
        motivo = motivo_configuracion(data)
    except ValueError as exc:
        return Response({'error': str(exc)}, status=400)
    if any(not isinstance(data.get(key), str) or not 1 <= len(data[key].strip()) <= 100 for key in CAMPOS_CUENTA):
        return Response({'error': 'Completa banco, número, titular y tipo de cuenta (máximo 100 caracteres por campo).'}, status=400)
    with transaction.atomic():
        cuenta = CuentaDestino.objects.select_for_update().get(pk=cuenta.pk)
        if cuenta.version != data['version']:
            return Response({'error': 'La cuenta cambió en otra sesión. Recarga y revisa antes de guardar.',
                'code': 'cuenta_modificada'}, status=409)
        anterior = snapshot_cuenta(cuenta)
        for key in CAMPOS_CUENTA:
            setattr(cuenta, key, data[key].strip())
        if snapshot_cuenta(cuenta) != anterior:
            cuenta.version += 1
            cuenta.save()
            registrar_configuracion('cuenta', anterior, snapshot_cuenta(cuenta), request.user, motivo)
    return Response({**CuentaDestinoSerializer(cuenta).data, 'configurada': True,
        'historial': historial_configuracion('cuenta')})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def configuracion_historial(request):
    if not request.user.is_superuser:
        return respuesta_sin_permiso()
    tipo = request.GET.get('tipo')
    if tipo not in ('cuenta', 'planes'):
        return Response({'error': 'tipo debe ser cuenta o planes.'}, status=400)
    return Response(historial_configuracion(tipo))


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def planes(request):
    """Planes de membresía y sus precios. GET lista los planes; PUT actualiza
    precios. La lectura está disponible para usuarios autenticados y la
    actualización queda reservada al superusuario. El cambio de precio afecta
    cobros futuros — el ledger PagoMembresia ya congela los precios históricos."""
    if request.method == 'PUT' and not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        membresias = Membresia.objects.all().order_by('id')
        return Response(MembresiaSerializer(membresias, many=True).data, status=status.HTTP_200_OK)

    # PUT: [{"id": 1, "precio": 12000, "version": 0}, ...], todo o nada.
    actualizaciones = request.data if isinstance(request.data, list) else request.data.get('planes') if isinstance(request.data, dict) else None
    if not isinstance(actualizaciones, list) or not actualizaciones:
        return Response({'error': 'Se espera una lista de planes'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        motivo = motivo_configuracion(request.data if isinstance(request.data, dict) else {})
    except ValueError as exc:
        return Response({'error': str(exc)}, status=400)
    validados = {}
    for item in actualizaciones:
        if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] <= 0:
            return Response({'error': 'Plan inválido.'}, status=400)
        if item['id'] in validados:
            return Response({'error': 'No repitas un plan en la actualización.'}, status=400)
        version = item.get('version')
        if type(version) is not int or version < 0:
            return Response({'error': 'Recarga los planes para obtener su versión.'}, status=400)
        try:
            precio = _precio_entero(item.get('precio'))
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)
        validados[item['id']] = (precio, version)
    with transaction.atomic():
        registros = list(Membresia.objects.select_for_update().filter(pk__in=validados).order_by('pk'))
        if len(registros) != len(validados):
            return Response({'error': 'Uno de los planes no existe.'}, status=404)
        if any(plan.version != validados[plan.pk][1] for plan in registros):
            return Response({'error': 'Los precios cambiaron en otra sesión. Recarga y revisa antes de guardar.',
                'code': 'planes_modificados'}, status=409)
        anterior = {str(plan.pk): {'nombre': plan.nombre, 'precio': str(plan.precio), 'version': plan.version} for plan in registros}
        for plan in registros:
            precio = validados[plan.pk][0]
            if plan.precio != precio:
                plan.precio = precio
                plan.version += 1
                plan.save(update_fields=['precio', 'version'])
        nuevo = {str(plan.pk): {'nombre': plan.nombre, 'precio': str(plan.precio), 'version': plan.version} for plan in registros}
        if nuevo != anterior:
            registrar_configuracion('planes', anterior, nuevo, request.user, motivo)

    membresias = Membresia.objects.all().order_by('id')
    return Response(MembresiaSerializer(membresias, many=True).data, status=status.HTTP_200_OK)


def _precio_entero(value):
    if type(value) not in (str, int) or not re.fullmatch(r'[0-9]{1,10}', str(value)):
        raise ValueError('El importe debe ser un entero entre 0 y 9.999.999.999, sin decimales.')
    return int(value)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def adjuntar_comprobante(request, codigo):
    """El usuario sube el comprobante → pre-activa la membresía al instante y notifica al admin por Telegram."""
    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        return Response({'error': 'Solicitud no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    if not usuario_puede_acceder_tienda(request.user, solicitud.tienda_id):
        return respuesta_sin_permiso()

    _normalizar_solicitud_pago(solicitud)

    if solicitud.estado != 'pendiente':
        mensaje = 'El código de pago ya expiró.' if solicitud.estado == 'expirada' else 'Esta solicitud ya fue enviada o procesada.'
        return Response({'error': mensaje}, status=status.HTTP_409_CONFLICT)

    archivo = request.FILES.get('comprobante')
    if not archivo:
        return Response({'error': 'Debes adjuntar una imagen del comprobante.'}, status=status.HTTP_400_BAD_REQUEST)
    if not getattr(archivo, 'content_type', '').startswith('image/'):
        return Response({'error': 'El comprobante debe ser una imagen.'}, status=status.HTTP_400_BAD_REQUEST)
    if archivo.size > 5 * 1024 * 1024:
        return Response({'error': 'La imagen no debe superar 5 MB.'}, status=status.HTTP_400_BAD_REQUEST)

    comprobante_bytes = None
    try:
        comprobante_bytes, nombre = telegram_bot.comprimir_imagen(archivo)
    except Exception:
        logger.exception('Error procesando comprobante %s', solicitud.codigo)
        return Response({'error': 'No se pudo procesar la imagen.'}, status=status.HTTP_400_BAD_REQUEST)
    if not comprobante_bytes:
        return Response({'error': 'No se pudo procesar la imagen.'}, status=status.HTTP_400_BAD_REQUEST)

    referencia = (request.data.get('referencia') or '').strip()

    # Pre-activación: acceso inmediato por 3 días mientras el admin confirma
    tm = Tienda_Membresia.objects.filter(tienda=solicitud.tienda).first()
    if not tm:
        return Response({'error': 'La ruta no tiene una membresía configurada.'}, status=status.HTTP_409_CONFLICT)

    with transaction.atomic():
        solicitud = SolicitudPago.objects.select_for_update().get(pk=solicitud.pk)
        if solicitud.estado != 'pendiente':
            return Response({'error': 'Esta solicitud ya fue enviada o procesada.'}, status=409)
        tm = Tienda_Membresia.objects.select_for_update().select_related('tienda').get(pk=tm.pk)
        anterior = snapshot(tm)
        solicitud.comprobante.save(nombre, ContentFile(comprobante_bytes), save=False)
        solicitud.referencia_bancaria = referencia
        # Un comprobante no deshace por sí solo una corrección de root.
        # Solo una aprobación o activación explícita restablece el acceso.
        if not tm.vencida_manualmente:
            tm.estado = 'Pre-activada'
            tm.pre_activada_hasta = fecha_operativa(tm.tienda) + datetime.timedelta(days=3)
        # Si estaba archivada por inactividad, el pago la reactiva (deja de estar oculta)
        tm.archivada = False
        tm.fecha_archivado = None
        tm.save()
        solicitud.estado = 'pendiente_confirmacion'
        solicitud.save()
        registrar_cambio(tm, anterior, 'comprobante_recibido', request.user,
            solicitud_codigo=solicitud.codigo)

    # Notificar al admin por Telegram (no bloquea la respuesta si falla)
    message_id = telegram_bot.notificar_solicitud(solicitud, comprobante_bytes)
    if message_id:
        solicitud.telegram_message_id = message_id
        solicitud.save(update_fields=['telegram_message_id'])

    return Response({
        'estado': solicitud.estado,
        'mensaje': 'Tu acceso ya está activo. Confirmaremos tu pago en las próximas horas.',
        'pre_activada_hasta': str(tm.pre_activada_hasta) if tm else None,
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([AllowAny])
def telegram_webhook(request):
    """Recibe los toques de botón del admin desde Telegram (confirmar / rechazar).
    Telegram no envía JWT — valida con X-Telegram-Bot-Api-Secret-Token."""
    secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if secret != settings.TELEGRAM_WEBHOOK_SECRET:
        return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

    # Las consultas privadas se manejan antes de los callbacks de pagos y
    # aplican su propio chat_id autorizado. El flujo de pagos existente queda
    # restringido al TELEGRAM_ADMIN_CHAT_ID como siempre.
    message = request.data.get('message')
    if message:
        try:
            if telegram_assistant.procesar_mensaje(message):
                return Response({'ok': True})
        except Exception:
            # Una consulta del asistente nunca debe dejar updates atascados ni
            # afectar el webhook de pagos. El detalle queda en el log para
            # diagnóstico; Telegram recibe 200 y puede procesar lo siguiente.
            logger.exception('Error procesando mensaje del asistente Telegram')
            return Response({'ok': True})

    callback = request.data.get('callback_query')
    if not callback:
        return Response({'ok': True})  # ignorar updates que no sean botones

    try:
        if telegram_assistant.procesar_callback(callback):
            return Response({'ok': True})
    except Exception:
        # Mismo aislamiento para botones asst:*; los callbacks de pagos se
        # procesan abajo y permanecen fuera de esta protección.
        if callback.get('data', '').startswith('asst:'):
            logger.exception('Error procesando botón del asistente Telegram')
            return Response({'ok': True})
        raise

    try:
        if telegram_bot.procesar_callback_alerta(callback):
            return Response({'ok': True})
    except Exception:
        if callback.get('data', '').startswith('alerta:'):
            logger.exception('Error procesando botón de alerta Telegram')
            return Response({'ok': True})
        raise

    chat_id = str(callback.get('message', {}).get('chat', {}).get('id', ''))
    if chat_id != str(settings.TELEGRAM_ADMIN_CHAT_ID):
        return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

    callback_id = callback.get('id')
    data = callback.get('data', '')
    try:
        accion, codigo = data.split(':', 1)
    except ValueError:
        telegram_bot.responder_callback(callback_id, 'Acción inválida')
        return Response({'ok': True})

    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        telegram_bot.responder_callback(callback_id, 'Solicitud no encontrada')
        return Response({'ok': True})

    estado_antes = solicitud.estado
    _normalizar_solicitud_pago(solicitud)

    if solicitud.estado == 'expirada':
        # Las solicitudes expiradas anteriores a esta corrección pueden aún
        # conservar botones en Telegram; limpiarlos al primer toque.
        if estado_antes == 'expirada':
            telegram_bot.marcar_expirada(solicitud)
        telegram_bot.responder_callback(callback_id, '⚠️ Solicitud expirada: la ruta no se activó')
        return Response({'ok': True})

    if solicitud.estado in ('aprobada', 'confirmada', 'rechazada'):
        telegram_bot.responder_callback(callback_id, 'Esta solicitud ya fue procesada')
        return Response({'ok': True})

    admin_tg = callback.get('from', {})
    admin_nombre = (
        f"{admin_tg.get('first_name', '')} {admin_tg.get('last_name', '')}".strip()
        or admin_tg.get('username', 'admin')
    )

    if accion == 'confirmar':
        if solicitud.estado != 'pendiente_confirmacion':
            telegram_bot.responder_callback(callback_id, 'Falta comprobante o la solicitud ya expiró')
            return Response({'ok': True})
        try:
            _confirmar_solicitud(solicitud)
        except ValueError as exc:
            logger.warning('No se pudo confirmar %s vía Telegram: %s', codigo, exc)
            telegram_bot.responder_callback(callback_id, f'⚠️ {exc}')
            return Response({'ok': True})
        except Exception:
            # La transacción revierte cualquier cambio parcial. Se deja el
            # botón disponible para reintentar o usar el panel web.
            logger.exception('Error confirmando solicitud %s vía Telegram', codigo)
            telegram_bot.responder_callback(callback_id, '⚠️ No se pudo confirmar; revisa el panel y reintenta')
            return Response({'ok': True})
        solicitud.refresh_from_db()
        telegram_bot.marcar_procesada(solicitud, 'confirmar', admin_nombre)
        telegram_bot.responder_callback(callback_id, f'✅ {codigo} confirmado')
    elif accion == 'rechazar':
        if solicitud.estado != 'pendiente_confirmacion':
            telegram_bot.responder_callback(callback_id, 'La solicitud no está lista para revisión')
            return Response({'ok': True})
        try:
            _revertir_solicitud(solicitud, 'Rechazado en revisión')
        except ValueError as exc:
            telegram_bot.responder_callback(callback_id, str(exc))
            return Response({'ok': True})
        telegram_bot.marcar_procesada(solicitud, 'rechazar', admin_nombre)
        telegram_bot.responder_callback(callback_id, f'❌ {codigo} rechazado')
    else:
        telegram_bot.responder_callback(callback_id, 'Acción desconocida')

    return Response({'ok': True})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def consultar_solicitud(request, codigo):
    """Devuelve el estado actual de una SolicitudPago. La app la consulta para hacer polling."""
    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        return Response({'error': 'Solicitud no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    if not usuario_puede_acceder_tienda(request.user, solicitud.tienda_id):
        return respuesta_sin_permiso()

    _normalizar_solicitud_pago(solicitud)

    serializer = SolicitudPagoSerializer(solicitud)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def listar_solicitudes_revision(request):
    """Panel de conciliación (solo superusuarios): solicitudes esperando confirmación
    + confirmadas de los últimos 30 días para cruzar con el extracto bancario."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)

    try:
        dias = int(request.GET.get('dias', 30))
        if dias not in (30, 90, 365):
            raise ValueError()
    except (TypeError, ValueError):
        return Response({'error': 'dias debe ser 30, 90 o 365.'}, status=400)
    _normalizar_pendientes_revision()
    corte = timezone.now() - datetime.timedelta(days=dias)
    pendientes = SolicitudPago.objects.filter(
        estado='pendiente_confirmacion'
    ).select_related('tienda__administrador', 'tienda__tienda_membresia', 'membresia', 'solicitada_por').order_by('-creada')
    confirmadas = SolicitudPago.objects.filter(
        estado='confirmada', procesada__gte=corte
    ).select_related('tienda__administrador', 'tienda__tienda_membresia', 'membresia', 'solicitada_por', 'revisada_por').order_by('-procesada')
    vencidas = SolicitudPago.objects.filter(estado='expirada', procesada__gte=corte)
    vencidas = vencidas.exclude(comprobante='').exclude(comprobante__isnull=True).select_related('tienda__administrador', 'tienda__tienda_membresia', 'membresia', 'solicitada_por').order_by('-procesada')
    revisadas = SolicitudPago.objects.filter(estado='rechazada', procesada__gte=corte).select_related('tienda__administrador', 'tienda__tienda_membresia', 'membresia', 'solicitada_por', 'revisada_por').order_by('-procesada')

    return Response({
        'pendientes': SolicitudPagoSerializer(pendientes, many=True).data,
        'confirmadas': SolicitudPagoSerializer(confirmadas, many=True).data,
        'vencidas': SolicitudPagoSerializer(vencidas, many=True).data,
        'revisadas': SolicitudPagoSerializer(revisadas, many=True).data,
        'dias': dias,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ver_comprobante(request, codigo):
    """Sirve la imagen del comprobante. Solo superusuarios (la carpeta media no es pública)."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)
    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud or not solicitud.comprobante:
        return Response({'error': 'Sin comprobante'}, status=status.HTTP_404_NOT_FOUND)
    from django.http import FileResponse
    return FileResponse(solicitud.comprobante.open('rb'), content_type='image/jpeg')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def revisar_solicitud_admin(request, codigo):
    """Confirmación / rechazo / reversión manual por el superusuario (web fallback de Telegram)."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)

    if not isinstance(request.data, dict):
        return Response({'error': 'Se espera un objeto JSON.'}, status=400)
    resultado = request.data.get('resultado')
    if resultado not in ('confirmar', 'rechazar', 'revertir'):
        return Response({'error': 'resultado debe ser confirmar, rechazar o revertir'}, status=400)
    permitir_expirada = request.data.get('confirmar_expirada', False)
    if type(permitir_expirada) is not bool:
        return Response({'error': 'confirmar_expirada debe ser verdadero o falso.'}, status=400)
    motivo = request.data.get('motivo', '')
    if resultado != 'confirmar' or permitir_expirada:
        if not isinstance(motivo, str) or not 5 <= len(motivo.strip()) <= 500:
            return Response({'error': 'Escribe un motivo de entre 5 y 500 caracteres.'}, status=400)

    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        return Response({'error': 'Solicitud no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    _normalizar_solicitud_pago(solicitud)
    if solicitud.estado == 'rechazada':
        return Response({'estado': solicitud.estado, 'message': 'Ya procesada'}, status=status.HTTP_409_CONFLICT)

    admin_nombre = request.user.get_full_name() or request.user.username

    if resultado == 'confirmar':
        if solicitud.estado == 'confirmada':
            return Response({'estado': 'confirmada', 'message': 'Ya confirmada'}, status=status.HTTP_409_CONFLICT)
        if solicitud.estado != 'pendiente_confirmacion' and not (solicitud.estado == 'expirada' and permitir_expirada):
            return Response({'error': 'Solo se pueden confirmar solicitudes con comprobante enviado.'}, status=status.HTTP_409_CONFLICT)
        try:
            tm = _confirmar_solicitud(solicitud, revisor=request.user,
                monto_confirmado=request.data.get('monto_confirmado'),
                permitir_expirada=permitir_expirada, motivo_excepcion=motivo)
        except ValueError as exc:
            logger.warning('No se pudo confirmar %s desde el panel: %s', codigo, exc)
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        solicitud.refresh_from_db()
        telegram_bot.marcar_procesada(solicitud, 'confirmar', admin_nombre)
    else:
        if solicitud.estado not in ('pendiente_confirmacion', 'confirmada'):
            return Response({'error': 'La solicitud no está disponible para revisión.'}, status=status.HTTP_409_CONFLICT)
        if resultado == 'revertir' and solicitud.estado != 'confirmada':
            return Response({'error': 'Solo se puede revertir un pago confirmado.'}, status=409)
        era_confirmada = solicitud.estado == 'confirmada'
        try:
            tm = _revertir_solicitud(solicitud, motivo, revisor=request.user)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=409)
        telegram_bot.marcar_procesada(solicitud, 'rechazar', admin_nombre)

    response_data = {'estado': solicitud.estado}
    if tm:
        response_data['fecha_vencimiento'] = str(tm.fecha_vencimiento)
        response_data['estado_membresia'] = tm.estado
    response_data['accion'] = 'confirmar' if resultado == 'confirmar' else 'revertir' if era_confirmada else 'rechazar'
    response_data['message'] = {'confirmar': 'Pago confirmado.', 'revertir': 'Ingreso anulado y suscripción restaurada; no se realizó un reembolso.',
        'rechazar': 'Comprobante rechazado; no se registró un ingreso.'}[response_data['accion']]
    return Response(response_data, status=status.HTTP_200_OK)


@transaction.atomic
def comprobar_estado_membresia(tienda_id):
    '''verificamos el estado de la membresia'''
    suscripcion_tienda = (
        Tienda_Membresia.objects
        .select_for_update()
        .select_related('tienda')
        .filter(tienda_id=tienda_id)
        .first()
    )
    # Algunas rutas históricas aún no tienen fila de membresía. No deben
    # provocar un 500 al consultar recaudos o el dashboard.
    if not suscripcion_tienda:
        return None
    if suscripcion_tienda.vencida_manualmente:
        return None  # Login incluye este retorno en su respuesta JSON.
    hoy = fecha_operativa(suscripcion_tienda.tienda)

    # Pre-activada: acceso temporal mientras se revisa el comprobante
    if suscripcion_tienda.estado == 'Pre-activada':
        if suscripcion_tienda.pre_activada_hasta and suscripcion_tienda.pre_activada_hasta >= hoy:
            return  # Período temporal vigente, no degradar
        # Período temporal expirado sin aprobación → degradar
        suscripcion_tienda.estado = 'Pendiente Pago'
        suscripcion_tienda.pre_activada_hasta = None
        suscripcion_tienda.save(update_fields=['estado', 'pre_activada_hasta'])
        return

    pendiente_pago = suscripcion_tienda.fecha_vencimiento + datetime.timedelta(days=1)
    # El bloqueo es en V+2, igual que el mantenimiento diario y el frontend.
    vencida = suscripcion_tienda.fecha_vencimiento + datetime.timedelta(days=2)

    if suscripcion_tienda.estado == 'Activa' and hoy >= pendiente_pago:
        suscripcion_tienda.estado = 'Pendiente Pago'
        suscripcion_tienda.save(update_fields=['estado'])

    if suscripcion_tienda.estado == 'Pendiente Pago' and hoy >= vencida:
        suscripcion_tienda.estado = 'Vencida'
        suscripcion_tienda.save(update_fields=['estado'])


####### INFORME DE INGRESOS POR MEMBRESÍAS (solo root) ##########

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ingresos_membresias(request):
    """Informe de ingresos por renovaciones de membresía, agrupado por mes.
    ?year=2026 (default: año actual). Solo superusuarios."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)

    hoy = timezone.localdate()
    from Tiendas.root_metricas import comparar_ledger, periodos_anio
    try:
        year = int(request.GET.get('year', hoy.year))
        periodos = periodos_anio(year, hoy)
    except (TypeError, ValueError):
        return Response({'error': 'Selecciona un año válido, no posterior al actual.'}, status=400)

    pagos = PagoMembresia.objects.filter(
        fecha__year=year, fecha__lte=hoy,
    ).select_related('tienda__administrador', 'membresia', 'registrado_por', 'solicitud').order_by('-fecha', '-creado')

    por_mes = [
        {'mes': m, 'total': 0, 'cantidad': 0,
         'mensuales': 0, 'anuales': 0,
         'monto_mensuales': 0, 'monto_anuales': 0}
        for m in range(1, 13)
    ]
    detalle = []
    anulados = []
    total_anual = 0

    for p in pagos:
        admin = p.tienda.administrador if p.tienda else None
        fila = {'id': p.id, 'tienda': p.tienda.nombre if p.tienda else (p.tienda_nombre or 'Ruta eliminada'),
            'tienda_id': p.tienda_id, 'administrador': (admin.get_full_name() or admin.username) if admin else None,
            'administrador_usuario': admin.username if admin else None, 'administrador_id': admin.pk if admin else None,
            'plan': p.membresia.nombre, 'monto': float(p.monto), 'fecha': str(p.fecha), 'origen': p.origen,
            'codigo': p.solicitud.codigo if p.solicitud_id else None, 'estado': p.estado,
            'motivo_anulacion': p.anulado_motivo, 'anulado_en': p.anulado_en}
        if p.estado == 'anulado':
            anulados.append(fila)
            continue
        mes = por_mes[p.fecha.month - 1]
        monto = float(p.monto)
        mes['total'] += monto
        mes['cantidad'] += 1
        if p.membresia.nombre == 'Anual':
            mes['anuales'] += 1
            mes['monto_anuales'] += monto
        else:
            mes['mensuales'] += 1
            mes['monto_mensuales'] += monto
        total_anual += monto
        detalle.append(fila)

    total_anterior = PagoMembresia.objects.filter(estado='vigente',
        fecha__year=year - 1
    ).aggregate(t=Sum('monto'))['t'] or 0

    anios = sorted({d.year for d in PagoMembresia.objects.filter(fecha__lte=hoy).dates('fecha', 'year')} | {hoy.year}, reverse=True)

    return Response({
        'year': year,
        'anios_disponibles': anios,
        'total_anual': total_anual,
        'total_anio_anterior': float(total_anterior),
        'por_mes': por_mes,
        'pagos': detalle,
        'pagos_anulados': anulados,
        'fecha_corte': str(hoy),
        'comparacion_anual': comparar_ledger(periodos),
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_resumen(request):
    """Indicadores globales root, sin recalcular precios históricos."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)
    _normalizar_pendientes_revision()
    _actualizar_estados_membresias()
    from Tiendas.root_metricas import resumen_root
    response = Response(resumen_root(timezone.localdate()), status=200)
    response['Cache-Control'] = 'no-store'
    return response


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def archivar_ruta(request, pk):
    """Archiva o desarchiva una ruta (Tienda_Membresia). Solo root.
    Body: {"archivar": true|false, "version": 0}. Reversible, auditado y sin borrar datos."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)
    data = request.data
    if not isinstance(data, dict) or type(data.get('archivar')) is not bool:
        return Response({'error': 'archivar debe ser verdadero o falso.'}, status=400)
    version = data.get('version')
    if type(version) is not int or version < 0:
        return Response({'error': 'Recarga la ruta para obtener su versión.'}, status=400)
    with transaction.atomic():
        tm = Tienda_Membresia.objects.select_for_update().select_related('tienda').filter(pk=pk).first()
        if not tm:
            return Response({'error': 'Ruta no encontrada'}, status=404)
        if tm.version != version:
            return Response({'error': 'La suscripción cambió. Recarga antes de archivar o restaurar.',
                'code': 'suscripcion_modificada'}, status=409)
        if tm.archivada != data['archivar']:
            anterior = snapshot(tm)
            tm.archivada = data['archivar']
            tm.fecha_archivado = fecha_operativa(tm.tienda) if tm.archivada else None
            tm.save(update_fields=['archivada', 'fecha_archivado'])
            registrar_cambio(tm, anterior, 'archivado' if tm.archivada else 'restauracion', request.user)
    return Response({'archivada': tm.archivada, 'fecha_archivado': str(tm.fecha_archivado) if tm.fecha_archivado else None,
        'version': tm.version}, status=200)



@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_detalle_ruta(request, pk):
    """Drill-down de una ruta para el panel root: contacto del administrador,
    membresía, señales de actividad e historial de pagos del ledger.
    Solo superusuarios."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)

    tienda = Tienda.objects.select_related('administrador').filter(id=pk).first()
    if not tienda:
        return Response({'error': 'Ruta no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    admin_user = tienda.administrador
    perfil = getattr(admin_user, 'perfil', None)
    tm = Tienda_Membresia.objects.select_related('membresia').filter(tienda=tienda).first()

    # Señales de actividad (import local para no acoplar módulos a nivel de import)
    from Recaudos.models import Recaudo
    ultimo_recaudo = (Recaudo.objects.filter(tienda=tienda)
                      .order_by('-fecha_recaudo')
                      .values_list('fecha_recaudo', flat=True).first())
    ultimo_cierre = (Cierre_Caja.objects.filter(tienda=tienda)
                     .order_by('-fecha_cierre')
                     .values_list('fecha_cierre', flat=True).first())
    ventas_activas = tienda.venta_set.filter(
        estado_venta__in=['Vigente', 'Vencido', 'Atrasado']).count()

    # Historial de pagos (ledger PagoMembresia)
    pagos_qs = (PagoMembresia.objects.filter(tienda=tienda)
                .select_related('membresia', 'registrado_por', 'solicitud')
                .order_by('-fecha', '-creado'))
    pagos = [{
        'id': p.pk,
        'estado': p.estado,
        'anulado_motivo': p.anulado_motivo,
        'anulado_en': p.anulado_en,
        'fecha': p.fecha,
        'monto': float(p.monto),
        'plan': p.membresia.nombre,
        'origen': p.origen,
        'registrado_por': p.registrado_por.username if p.registrado_por else None,
        'codigo': p.solicitud.codigo if p.solicitud_id else None,
    } for p in pagos_qs]

    return Response({
        'tienda': {
            'id': tienda.id,
            'nombre': tienda.nombre,
            'telefono': tienda.telefono,
            'prefijo_telefono': tienda.prefijo_telefono,
            'fecha_registro': tienda.fecha_registro,
            'cantidad_clientes': tienda.cliente_set.count(),
            'cantidad_ventas': tienda.venta_set.count(),
        },
        'admin': {
            'username': admin_user.username,
            'nombre': (f'{admin_user.first_name} {admin_user.last_name}').strip() or admin_user.username,
            'email': admin_user.email,
            'telefono': (perfil.telefono if perfil and perfil.telefono else tienda.telefono) or '',
            'ultimo_login': admin_user.last_login,
        },
        'membresia': {
            'id': tm.pk if tm else None,
            'version': tm.version if tm else None,
            'vencida_manualmente': tm.vencida_manualmente if tm else False,
            'vencimiento_manual_fecha': tm.vencimiento_manual_fecha if tm else None,
            'motivo': tm.motivo if tm else '',
            'plan': tm.membresia.nombre if tm else None,
            'estado': tm.estado if tm else None,
            'fecha_activacion': tm.fecha_activacion if tm else None,
            'fecha_vencimiento': tm.fecha_vencimiento if tm else None,
            'archivada': tm.archivada if tm else False,
        },
        'actividad': {
            'ultimo_recaudo': ultimo_recaudo,
            'ultimo_cierre': ultimo_cierre,
            'ventas_activas': ventas_activas,
        },
        'pagos': pagos,
        'total_pagado': sum(p['monto'] for p in pagos if p['estado'] == 'vigente'),
        'solicitudes_pendientes': [{
            'codigo': s.codigo, 'estado': s.estado, 'plan': s.membresia.nombre, 'fecha': s.creada,
        } for s in pendientes(tm).select_related('membresia').order_by('-creada')] if tm else [],
        'historial_suscripcion': [{
            'accion': c.accion, 'motivo': c.motivo, 'usuario': c.usuario_nombre,
            'fecha': c.fecha, 'anterior': c.anterior, 'nuevo': c.nuevo, 'pago_id': c.pago_id,
        } for c in tienda.cambiosuscripcion_set.order_by('-version')[:30]],
    })
