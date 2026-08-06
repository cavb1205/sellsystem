from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status

import datetime
import logging
from itertools import chain

from django.conf import settings
from django.utils import timezone

from django.core.files.base import ContentFile

from django.db.models import Sum, Count, Max, Q, OuterRef, Subquery, F, DecimalField
from django.db import transaction
from decimal import Decimal, ROUND_HALF_UP

from Clientes.models import Cliente
from Ventas.models import Venta
from Tiendas.models import AlertaOperativa, Tienda, Cierre_Caja, Tienda_Membresia, Membresia, Tienda_Administrador, SolicitudPago, CuentaDestino, PagoMembresia, _generar_codigo_solicitud
from Tiendas.serializers import TiendaSerializer, CajaSerializer, TiendaMembresiaSerializer, TiendaMembresiaListaSerializer, TiendaCreateSerializer, TiendaAdminSerializer, SolicitudPagoSerializer, CuentaDestinoSerializer, MembresiaSerializer
from Tiendas.alertas_operativas import rutas_del_usuario, usuario_alertas_configurado
from Tiendas import telegram_bot
from Tiendas import telegram_assistant
from Tiendas.permissions import requiere_acceso_tienda, usuario_puede_acceder_tienda, respuesta_sin_permiso

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

    hoy = datetime.date.today()
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
        'aportes_ano': aportes['ano'],
        'gastos_ano': gastos['ano'],
        'utilidades_ano': utilidades['ano'],
        'ventas_netas_ano': total_ventas(ventas_ano),
        'perdidas_ano': perdidas_ano,
        'utilidad_estimada_ano': utilidad_estimada(ventas_ano),
    }


def _actualizar_estados_membresias():
    """Recalcula en bulk los estados de todas las membresías según la fecha actual.
    Activa → Pendiente Pago cuando fecha_vencimiento ya pasó (día +1, único día de gracia).
    Pendiente Pago → Vencida cuando han pasado 2+ días desde el vencimiento (bloqueo en V+2).
    Pre-activada → Pendiente Pago cuando pre_activada_hasta ya pasó."""
    hoy = datetime.date.today()

    Tienda_Membresia.objects.filter(
        estado='Activa',
        fecha_vencimiento__lte=hoy - datetime.timedelta(days=1)
    ).update(estado='Pendiente Pago')

    Tienda_Membresia.objects.filter(
        estado='Pendiente Pago',
        fecha_vencimiento__lte=hoy - datetime.timedelta(days=2)
    ).update(estado='Vencida')

    Tienda_Membresia.objects.filter(
        estado='Pre-activada',
        pre_activada_hasta__lt=hoy
    ).update(estado='Pendiente Pago', pre_activada_hasta=None)


def extender_membresia(tienda_id, plan_nombre):
    """Extiende la membresía desde max(fecha_vencimiento, hoy). Retorna la Tienda_Membresia."""
    tm = Tienda_Membresia.objects.get(tienda_id=tienda_id)
    membresia = Membresia.objects.get(nombre=plan_nombre)
    dias = 30 if plan_nombre == 'Mensual' else 365
    base = max(tm.fecha_vencimiento, datetime.date.today())
    tm.membresia = membresia
    tm.fecha_activacion = datetime.date.today()
    tm.fecha_vencimiento = base + datetime.timedelta(days=dias)
    tm.estado = 'Activa'
    tm.pre_activada_hasta = None
    # Si la ruta estaba archivada por inactividad, al pagar vuelve a estar visible
    tm.archivada = False
    tm.fecha_archivado = None
    tm.save()
    return tm


def _confirmar_solicitud(solicitud, revisor=None):
    """Confirma una solicitud y extiende la membresía. Guarda el vencimiento previo
    para poder revertir con exactitud. revisor puede ser None (confirmado vía Telegram)."""
    tm = Tienda_Membresia.objects.filter(tienda=solicitud.tienda).first()
    solicitud.fecha_vencimiento_previa = tm.fecha_vencimiento if tm else None
    solicitud.estado = 'confirmada'
    solicitud.motivo_rechazo = ''
    solicitud.revisada_por = revisor
    solicitud.procesada = timezone.now()
    solicitud.save()
    tm = extender_membresia(solicitud.tienda_id, solicitud.membresia.nombre)
    # Registrar el ingreso en el libro de pagos (precio congelado al momento del cobro)
    if not PagoMembresia.objects.filter(solicitud=solicitud).exists():
        PagoMembresia.objects.create(
            tienda_id=solicitud.tienda_id,
            tienda_nombre=solicitud.tienda.nombre,
            membresia=solicitud.membresia,
            monto=solicitud.membresia.precio,
            fecha=datetime.date.today(),
            origen='panel' if revisor else 'telegram',
            solicitud=solicitud,
            registrado_por=revisor,
        )
    return tm


def _revertir_solicitud(solicitud, motivo, revisor=None):
    """Rechaza o revierte una solicitud. Si estaba confirmada, restaura el vencimiento previo."""
    tm = Tienda_Membresia.objects.filter(tienda=solicitud.tienda).first()
    era_confirmada = solicitud.estado == 'confirmada'
    solicitud.estado = 'rechazada'
    solicitud.motivo_rechazo = motivo
    solicitud.revisada_por = revisor
    solicitud.procesada = timezone.now()
    solicitud.save()
    if era_confirmada:
        # El ingreso queda sin efecto — sacarlo del libro de pagos
        PagoMembresia.objects.filter(solicitud=solicitud).delete()
    if tm:
        if era_confirmada and solicitud.fecha_vencimiento_previa:
            tm.fecha_vencimiento = solicitud.fecha_vencimiento_previa
        if tm.estado in ('Pre-activada', 'Activa'):
            tm.estado = 'Pendiente Pago'
            tm.pre_activada_hasta = None
            tm.save()
        else:
            tm.save()
    return tm


### VIEWS FOR TIENDA  ####

@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def list_all_tiendas(request):
    '''obtenemos todas las tiendas'''

    user = request.user
    if user.username == 'root':
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
        return Response({'message': 'No se han creado tiendas'}, status=status.HTTP_200_OK)
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
    allowed = ['prefijo_telefono', 'telefono', 'cupo_minimo_nuevo']
    for field in allowed:
        if field in request.data:
            setattr(tienda, field, request.data[field])
    tienda.save()
    return Response({'prefijo_telefono': tienda.prefijo_telefono}, status=status.HTTP_200_OK)


@api_view(['POST'])
def post_tienda(request):
    '''creamos una tienda'''
    if request.method == 'POST':
        # Verificar antes de guardar para que el flag sea correcto
        es_primera_tienda = not Tienda.objects.filter(administrador=request.user).exists()

        serialize = TiendaCreateSerializer(data=request.data)
        if serialize.is_valid():
            tienda = serialize.save()
            Cierre_Caja.objects.create(tienda=tienda, valor=tienda.caja_inicial, fecha_cierre=(
                datetime.date.today() - datetime.timedelta(days=1)))

            if es_primera_tienda:
                # Solo la primera ruta recibe trial de 7 días
                Tienda_Membresia.objects.create(
                    tienda=tienda,
                    membresia=Membresia.objects.get(nombre='Prueba'),
                    fecha_activacion=datetime.date.today(),
                    fecha_vencimiento=datetime.date.today() + datetime.timedelta(days=7),
                    estado='Activa'
                )
            else:
                # Rutas adicionales nacen en Pendiente Pago — sin trial
                Tienda_Membresia.objects.create(
                    tienda=tienda,
                    membresia=Membresia.objects.get(nombre='Prueba'),
                    fecha_activacion=datetime.date.today(),
                    fecha_vencimiento=datetime.date.today() - datetime.timedelta(days=1),
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
def delete_tienda(request, pk):
    if not request.user.is_superuser:
        return respuesta_sin_permiso()
    tienda = Tienda.objects.filter(id=pk).first()
    if tienda:
        tienda.delete()
        return Response({'message': 'Tienda eliminada correctamente'}, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_tienda_root(request, pk):
    '''Elimina una tienda — solo root, solo si está vacía (0 clientes y 0 ventas).
    Las rutas con datos NO se pueden eliminar (se archivan): preservamos la
    integridad de los datos financieros de clientes.'''
    if request.user.username != 'root':
        return Response({'error': 'Solo el usuario root puede eliminar rutas'},
                        status=status.HTTP_403_FORBIDDEN)
    tienda = Tienda.objects.filter(id=pk).first()
    if not tienda:
        return Response({'error': 'No se encontró la ruta'}, status=status.HTTP_404_NOT_FOUND)
    clientes = tienda.cliente_set.count()
    ventas = tienda.venta_set.count()
    if clientes > 0 or ventas > 0:
        return Response({
            'error': f'La ruta tiene {clientes} clientes y {ventas} ventas. No se puede eliminar para proteger los datos.'
        }, status=status.HTTP_409_CONFLICT)
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
def get_caja_anterior(request, fecha, tienda_id=None):
    """obtenemos el valor de la caja del dia anterior a la fecha que recibimos como parametro"""
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(
            id=request.user.perfil.tienda.id).first()
    fecha = datetime.datetime.strptime(fecha, '%Y-%m-%d')
    dia_anterior = fecha - (datetime.timedelta(days=1))
    caja_anterior = Cierre_Caja.objects.filter(
        tienda=tienda.id, fecha_cierre=dia_anterior).first()
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
    fecha = datetime.datetime.strptime(fecha, '%Y-%m-%d')
    cierre_caja = Cierre_Caja.objects.create(
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

    # Se conserva la semántica histórica: el widget consultaba utilidades
    # únicamente para la fecha final, no para todo el rango.
    utilidades = Utilidad.objects.filter(
        tienda_id=tienda.id,
        fecha=date2,
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

    hoy = datetime.date.today()

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


@api_view(['GET'])
@requiere_acceso_tienda
def reporte_utilidad_diario(request, date1, date2, tienda_id=None):
    """Devuelve el reporte diario de utilidad ya procesado en el backend.

    La pantalla solo necesita una fila por fecha. Se cargan campos mínimos de
    ventas y gastos, se conserva el redondeo monetario histórico del frontend
    y se evita enviar listados completos para que el navegador los agrupe.
    """
    from Gastos.models import Gasto

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
    ).values(
        'fecha_venta', 'valor_venta', 'interes', 'estado_venta', 'saldo_actual',
    )
    gastos = Gasto.objects.filter(
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
                'utilidad': 0,
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
        datos['interesesGenerados'] += total_a_pagar - valor_venta
        if venta['estado_venta'] == 'Perdida':
            datos['perdidas'] += redondear_monto(venta['saldo_actual'])

    for gasto in gastos:
        datos = fila(gasto['fecha'])
        datos['gastos'] += redondear_monto(gasto['valor'])

    for datos in datos_por_fecha.values():
        datos['utilidad'] = (
            datos['interesesGenerados']
            - datos['gastos']
            - datos['perdidas']
        )

    return Response(
        sorted(datos_por_fecha.values(), key=lambda item: item['fecha'], reverse=True),
        status=status.HTTP_200_OK,
    )

def _registrar_pago_manual(tienda_membresia, user):
    """Registra en el libro de pagos una activación manual hecha por el root.
    Idempotente por día: si ya hay un pago manual de esa tienda y plan hoy,
    no duplica (evita doble-conteo al tocar el botón dos veces)."""
    PagoMembresia.objects.get_or_create(
        tienda=tienda_membresia.tienda,
        membresia=tienda_membresia.membresia,
        fecha=datetime.date.today(),
        origen='manual',
        defaults={
            'tienda_nombre': tienda_membresia.tienda.nombre,
            'monto': tienda_membresia.membresia.precio,
            'registrado_por': user if getattr(user, 'is_authenticated', False) else None,
        },
    )


@api_view(['POST'])
def activar_membresia_mensual(request, pk):
    '''get store and activate membershi for a mounth + 30 days'''
    if not request.user.is_superuser:
        return respuesta_sin_permiso()
    tienda = Tienda_Membresia.objects.filter(id=pk).first()
    if tienda:
        with transaction.atomic():
            tienda.estado = 'Activa'
            tienda.membresia = Membresia.objects.get(nombre='Mensual')
            tienda.fecha_activacion = datetime.date.today()
            tienda.fecha_vencimiento = tienda.fecha_activacion + datetime.timedelta(days=30)
            tienda.archivada = False
            tienda.fecha_archivado = None
            tienda.save()
            _registrar_pago_manual(tienda, request.user)
        return Response({'message':'Suscripción Mensual Activa'}, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)
    
@api_view(['POST'])
def activar_membresia_ano(request, pk):
    '''get store and activate membershi for a year + 365 days'''
    if not request.user.is_superuser:
        return respuesta_sin_permiso()
    tienda = Tienda_Membresia.objects.filter(id=pk).first()
    if tienda:
        with transaction.atomic():
            tienda.estado = 'Activa'
            tienda.membresia = Membresia.objects.get(nombre='Anual')
            tienda.fecha_activacion = datetime.date.today()
            tienda.fecha_vencimiento = tienda.fecha_activacion + datetime.timedelta(days=365)
            tienda.archivada = False
            tienda.fecha_archivado = None
            tienda.save()
            _registrar_pago_manual(tienda, request.user)
        return Response({'message':'Suscripción Anual Activa'}, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)

####### SOLICITUDES DE PAGO (membresías automáticas vía WhatsApp) ##########

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

    tienda_id = request.data.get('tienda_id') or request.user.perfil.tienda.id
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'error': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    if not usuario_puede_acceder_tienda(request.user, tienda_id):
        return respuesta_sin_permiso()

    codigo = _generar_codigo_solicitud(membresia.nombre)
    expira = timezone.now() + datetime.timedelta(hours=24)

    solicitud = SolicitudPago.objects.create(
        tienda=tienda,
        membresia=membresia,
        codigo=codigo,
        expira=expira,
        solicitada_por=request.user,
    )

    numero_soporte = getattr(settings, 'WHATSAPP_SOPORTE_NUMERO', '')
    texto_wa = f"Hola, necesito ayuda con el pago de mi plan, tienda *{tienda.nombre}* (código {codigo})."
    wa_link = f"https://wa.me/{numero_soporte}?text={texto_wa.replace(' ', '%20')}" if numero_soporte else ''

    return Response({
        'codigo': codigo,
        'expira': expira,
        'monto': str(membresia.precio),
        'plan': membresia.nombre,
        'wa_link': wa_link,
        'cuenta': CuentaDestinoSerializer(CuentaDestino.get()).data,
    }, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def cuenta_destino(request):
    """Datos bancarios para pagos. GET y PUT solo para el superusuario (root)."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

    cuenta = CuentaDestino.get()
    if request.method == 'GET':
        return Response(CuentaDestinoSerializer(cuenta).data, status=status.HTTP_200_OK)

    serializer = CuentaDestinoSerializer(cuenta, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def planes(request):
    """Planes de membresía y sus precios. GET lista los planes; PUT actualiza
    precios. Solo para el superusuario (root). El cambio de precio afecta
    cobros futuros — el ledger PagoMembresia ya congela los precios históricos."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        membresias = Membresia.objects.all().order_by('id')
        return Response(MembresiaSerializer(membresias, many=True).data, status=status.HTTP_200_OK)

    # PUT: espera [{"id": 1, "precio": 12000}, ...] — actualiza solo el precio
    actualizaciones = request.data if isinstance(request.data, list) else request.data.get('planes', [])
    if not isinstance(actualizaciones, list):
        return Response({'error': 'Se espera una lista de planes'}, status=status.HTTP_400_BAD_REQUEST)

    for item in actualizaciones:
        plan = Membresia.objects.filter(id=item.get('id')).first()
        if not plan:
            continue
        precio = item.get('precio')
        try:
            precio = int(precio)
        except (TypeError, ValueError):
            return Response({'error': f'Precio inválido para el plan {item.get("id")}'}, status=status.HTTP_400_BAD_REQUEST)
        if precio < 0:
            return Response({'error': 'El precio no puede ser negativo'}, status=status.HTTP_400_BAD_REQUEST)
        plan.precio = precio
        plan.save(update_fields=['precio'])

    membresias = Membresia.objects.all().order_by('id')
    return Response(MembresiaSerializer(membresias, many=True).data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def adjuntar_comprobante(request, codigo):
    """El usuario sube el comprobante → pre-activa la membresía al instante y notifica al admin por Telegram."""
    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        return Response({'error': 'Solicitud no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    if solicitud.estado in ('aprobada', 'confirmada', 'rechazada'):
        return Response({'error': 'Esta solicitud ya fue procesada'}, status=status.HTTP_409_CONFLICT)

    archivo = request.FILES.get('comprobante')
    comprobante_bytes = None
    if archivo:
        comprobante_bytes, nombre = telegram_bot.comprimir_imagen(archivo)
        if comprobante_bytes:
            solicitud.comprobante.save(nombre, ContentFile(comprobante_bytes), save=False)

    referencia = (request.data.get('referencia') or '').strip()
    if referencia:
        solicitud.referencia_bancaria = referencia

    # Pre-activación: acceso inmediato por 3 días mientras el admin confirma
    tm = Tienda_Membresia.objects.filter(tienda=solicitud.tienda).first()
    if tm:
        tm.estado = 'Pre-activada'
        tm.pre_activada_hasta = datetime.date.today() + datetime.timedelta(days=3)
        # Si estaba archivada por inactividad, el pago la reactiva (deja de estar oculta)
        tm.archivada = False
        tm.fecha_archivado = None
        tm.save()

    solicitud.estado = 'pendiente_confirmacion'
    solicitud.save()

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

    if solicitud.estado in ('aprobada', 'confirmada', 'rechazada'):
        telegram_bot.responder_callback(callback_id, 'Esta solicitud ya fue procesada')
        return Response({'ok': True})

    admin_tg = callback.get('from', {})
    admin_nombre = (
        f"{admin_tg.get('first_name', '')} {admin_tg.get('last_name', '')}".strip()
        or admin_tg.get('username', 'admin')
    )

    if accion == 'confirmar':
        _confirmar_solicitud(solicitud)
        solicitud.refresh_from_db()
        telegram_bot.marcar_procesada(solicitud, 'confirmar', admin_nombre)
        telegram_bot.responder_callback(callback_id, f'✅ {codigo} confirmado')
    elif accion == 'rechazar':
        _revertir_solicitud(solicitud, 'Rechazado en revisión')
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

    # Marcar como expirada si ya venció y sigue pendiente
    if solicitud.estado == 'pendiente' and timezone.now() >= solicitud.expira:
        solicitud.estado = 'expirada'
        solicitud.save(update_fields=['estado'])

    serializer = SolicitudPagoSerializer(solicitud)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def listar_solicitudes_revision(request):
    """Panel de conciliación (solo superusuarios): solicitudes esperando confirmación
    + confirmadas de los últimos 30 días para cruzar con el extracto bancario."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)

    corte = timezone.now() - datetime.timedelta(days=30)
    pendientes = SolicitudPago.objects.filter(
        estado='pendiente_confirmacion'
    ).select_related('tienda', 'membresia', 'solicitada_por').order_by('-creada')
    confirmadas = SolicitudPago.objects.filter(
        estado='confirmada', procesada__gte=corte
    ).select_related('tienda', 'membresia', 'solicitada_por', 'revisada_por').order_by('-procesada')

    return Response({
        'pendientes': SolicitudPagoSerializer(pendientes, many=True).data,
        'confirmadas': SolicitudPagoSerializer(confirmadas, many=True).data,
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

    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        return Response({'error': 'Solicitud no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    if solicitud.estado in ('rechazada', 'expirada'):
        return Response({'estado': solicitud.estado, 'message': 'Ya procesada'}, status=status.HTTP_409_CONFLICT)

    resultado = request.data.get('resultado')
    if resultado not in ('confirmar', 'rechazar'):
        return Response({'error': 'resultado debe ser confirmar o rechazar'}, status=status.HTTP_400_BAD_REQUEST)

    admin_nombre = request.user.get_full_name() or request.user.username

    if resultado == 'confirmar':
        if solicitud.estado == 'confirmada':
            return Response({'estado': 'confirmada', 'message': 'Ya confirmada'}, status=status.HTTP_409_CONFLICT)
        tm = _confirmar_solicitud(solicitud, revisor=request.user)
        solicitud.refresh_from_db()
        telegram_bot.marcar_procesada(solicitud, 'confirmar', admin_nombre)
    else:  # rechazar — sirve también para revertir una confirmada en la conciliación
        motivo = request.data.get('motivo', 'Rechazado en revisión manual')
        tm = _revertir_solicitud(solicitud, motivo, revisor=request.user)
        telegram_bot.marcar_procesada(solicitud, 'rechazar', admin_nombre)

    response_data = {'estado': solicitud.estado}
    if tm:
        response_data['fecha_vencimiento'] = str(tm.fecha_vencimiento)
    return Response(response_data, status=status.HTTP_200_OK)


def comprobar_estado_membresia(tienda_id):
    '''verificamos el estado de la membresia'''
    suscripcion_tienda = Tienda_Membresia.objects.get(tienda=tienda_id)
    hoy = datetime.date.today()

    # Pre-activada: acceso temporal mientras se revisa el comprobante
    if suscripcion_tienda.estado == 'Pre-activada':
        if suscripcion_tienda.pre_activada_hasta and suscripcion_tienda.pre_activada_hasta >= hoy:
            return  # Período temporal vigente, no degradar
        # Período temporal expirado sin aprobación → degradar
        suscripcion_tienda.estado = 'Pendiente Pago'
        suscripcion_tienda.pre_activada_hasta = None
        suscripcion_tienda.save()
        return

    pendiente_pago = suscripcion_tienda.fecha_vencimiento + datetime.timedelta(days=1)
    vencida = pendiente_pago + datetime.timedelta(days=2)

    if suscripcion_tienda.estado == 'Activa' and hoy >= pendiente_pago:
        suscripcion_tienda.estado = 'Pendiente Pago'
        suscripcion_tienda.save()

    if suscripcion_tienda.estado == 'Pendiente Pago' and hoy >= vencida:
        suscripcion_tienda.estado = 'Vencida'
        suscripcion_tienda.save()


####### INFORME DE INGRESOS POR MEMBRESÍAS (solo root) ##########

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ingresos_membresias(request):
    """Informe de ingresos por renovaciones de membresía, agrupado por mes.
    ?year=2026 (default: año actual). Solo superusuarios."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)

    hoy = datetime.date.today()
    try:
        year = int(request.GET.get('year', hoy.year))
    except (TypeError, ValueError):
        year = hoy.year

    pagos = PagoMembresia.objects.filter(
        fecha__year=year
    ).select_related('tienda', 'membresia', 'registrado_por').order_by('-fecha', '-creado')

    por_mes = [
        {'mes': m, 'total': 0, 'cantidad': 0,
         'mensuales': 0, 'anuales': 0,
         'monto_mensuales': 0, 'monto_anuales': 0}
        for m in range(1, 13)
    ]
    detalle = []
    total_anual = 0

    for p in pagos:
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
        detalle.append({
            'id': p.id,
            'tienda': p.tienda.nombre if p.tienda else (p.tienda_nombre or 'Ruta eliminada'),
            'tienda_id': p.tienda_id,
            'plan': p.membresia.nombre,
            'monto': monto,
            'fecha': str(p.fecha),
            'origen': p.origen,
            'codigo': p.solicitud.codigo if p.solicitud_id else None,
        })

    total_anterior = PagoMembresia.objects.filter(
        fecha__year=year - 1
    ).aggregate(t=Sum('monto'))['t'] or 0

    anios = sorted({d.year for d in PagoMembresia.objects.dates('fecha', 'year')} | {hoy.year}, reverse=True)

    return Response({
        'year': year,
        'anios_disponibles': anios,
        'total_anual': total_anual,
        'total_anio_anterior': float(total_anterior),
        'por_mes': por_mes,
        'pagos': detalle,
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_resumen(request):
    """Panel de administración del root: KPIs globales del negocio en una sola
    respuesta (rutas por estado, ingresos del mes/año, MRR estimado, conciliación
    pendiente y próximas a vencer). Solo superusuarios."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)

    # Recalcular estados para que los conteos reflejen la realidad de hoy
    _actualizar_estados_membresias()

    hoy = datetime.date.today()
    inicio_mes = hoy.replace(day=1)

    # Rutas por estado (las archivadas no cuentan en las métricas activas)
    qs = Tienda_Membresia.objects.filter(archivada=False)
    rutas = {
        'total': qs.count(),
        'activas': qs.filter(estado='Activa').count(),
        'pendientes': qs.filter(estado='Pendiente Pago').count(),
        'vencidas': qs.filter(estado='Vencida').count(),
        'preactivadas': qs.filter(estado='Pre-activada').count(),
        'archivadas': Tienda_Membresia.objects.filter(archivada=True).count(),
    }

    # Próximas a vencer: activas que vencen en los próximos 3 días (incluido hoy)
    por_vencer = qs.filter(
        estado='Activa',
        fecha_vencimiento__gte=hoy,
        fecha_vencimiento__lte=hoy + datetime.timedelta(days=3),
    ).count()

    # Ingresos
    ingresos_mes = PagoMembresia.objects.filter(fecha__gte=inicio_mes, fecha__lte=hoy).aggregate(t=Sum('monto'))['t'] or 0
    ingresos_anio = PagoMembresia.objects.filter(fecha__year=hoy.year).aggregate(t=Sum('monto'))['t'] or 0
    renovaciones_mes = PagoMembresia.objects.filter(fecha__gte=inicio_mes, fecha__lte=hoy).count()

    # Mes anterior (para el % de crecimiento)
    fin_mes_ant = inicio_mes - datetime.timedelta(days=1)
    inicio_mes_ant = fin_mes_ant.replace(day=1)
    ingresos_mes_anterior = PagoMembresia.objects.filter(
        fecha__gte=inicio_mes_ant, fecha__lte=fin_mes_ant
    ).aggregate(t=Sum('monto'))['t'] or 0

    # Tendencia: ingresos de los últimos 6 meses (incluido el actual)
    MESES_ES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    ingresos_6m = []
    yy, mm = hoy.year, hoy.month
    serie = []
    for _ in range(6):
        serie.append((yy, mm))
        mm -= 1
        if mm == 0:
            mm = 12
            yy -= 1
    for (y, m) in reversed(serie):
        total = PagoMembresia.objects.filter(fecha__year=y, fecha__month=m).aggregate(t=Sum('monto'))['t'] or 0
        ingresos_6m.append({'label': MESES_ES[m - 1], 'anio': y, 'mes': m, 'total': float(total)})

    # Composición de la base activa por plan
    distribucion_plan = dict(
        qs.filter(estado='Activa').values_list('membresia__nombre').annotate(n=Count('id'))
    )

    # Crecimiento: nuevas rutas registradas este mes
    nuevas_rutas_mes = Tienda.objects.filter(
        fecha_registro__year=hoy.year, fecha_registro__month=hoy.month
    ).count()

    # MRR estimado: mensuales activas a precio mensual + anuales activas prorrateadas /12
    precio_mensual = (Membresia.objects.filter(nombre='Mensual').values_list('precio', flat=True).first()) or 0
    precio_anual = (Membresia.objects.filter(nombre='Anual').values_list('precio', flat=True).first()) or 0
    mensuales_activas = qs.filter(estado='Activa', membresia__nombre='Mensual').count()
    anuales_activas = qs.filter(estado='Activa', membresia__nombre='Anual').count()
    mrr_estimado = float(precio_mensual) * mensuales_activas + (float(precio_anual) / 12.0) * anuales_activas

    # Conciliación pendiente
    conciliacion_pendiente = SolicitudPago.objects.filter(estado='pendiente_confirmacion').count()

    # ── Retención y conversión ──────────────────────────────────────
    # Churn: rutas que se bloquearon este mes (el bloqueo ocurre en vencimiento+2d)
    bloqueadas_mes = qs.filter(
        estado='Vencida',
        fecha_vencimiento__gte=inicio_mes - datetime.timedelta(days=2),
        fecha_vencimiento__lte=hoy - datetime.timedelta(days=2),
    ).count()

    # Conversión trial→pago (histórica, incluye archivadas):
    # toda ruta nace en Prueba, así que convertida = tiene al menos un pago;
    # trial perdido = sigue en Prueba, vencida y sin pagos.
    todas = Tienda_Membresia.objects.all()
    convertidas = todas.filter(tienda__pagos_membresia__isnull=False).distinct().count()
    trials_perdidos = todas.filter(
        membresia__nombre='Prueba', estado='Vencida',
        tienda__pagos_membresia__isnull=True,
    ).count()
    base_conv = convertidas + trials_perdidos
    conversion_trial = round(convertidas * 100.0 / base_conv) if base_conv else None

    # Trials en curso: en Prueba y aún no bloqueados
    trials_en_curso = qs.filter(membresia__nombre='Prueba').exclude(estado='Vencida').count()

    return Response({
        'rutas': rutas,
        'por_vencer': por_vencer,
        'ingresos_mes': float(ingresos_mes),
        'ingresos_anio': float(ingresos_anio),
        'ingresos_mes_anterior': float(ingresos_mes_anterior),
        'renovaciones_mes': renovaciones_mes,
        'mrr_estimado': round(mrr_estimado),
        'conciliacion_pendiente': conciliacion_pendiente,
        'ingresos_6m': ingresos_6m,
        'distribucion_plan': distribucion_plan,
        'nuevas_rutas_mes': nuevas_rutas_mes,
        'retencion': {
            'bloqueadas_mes': bloqueadas_mes,
            'conversion_trial': conversion_trial,   # % histórico o null sin datos
            'trials_en_curso': trials_en_curso,
            'trials_perdidos': trials_perdidos,
        },
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def archivar_ruta(request, pk):
    """Archiva o desarchiva una ruta (Tienda_Membresia). Solo root.
    Body: {"archivar": true|false}. El archivado es reversible y no borra datos."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)
    tm = Tienda_Membresia.objects.filter(id=pk).first()
    if not tm:
        return Response({'error': 'Ruta no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    archivar = request.data.get('archivar', True)
    tm.archivada = bool(archivar)
    tm.fecha_archivado = datetime.date.today() if tm.archivada else None
    tm.save(update_fields=['archivada', 'fecha_archivado'])
    return Response({'archivada': tm.archivada, 'fecha_archivado': str(tm.fecha_archivado) if tm.fecha_archivado else None}, status=status.HTTP_200_OK)



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
        'total_pagado': sum(p['monto'] for p in pagos),
    })
