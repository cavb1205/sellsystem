from decimal import Decimal
from datetime import date, datetime, timedelta
import Ventas

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from django.db import transaction
from django.db.models import Count, Max, Q, OuterRef, Subquery
from django.utils import timezone

from Ventas.models import Venta, AjusteVentaAdministrativo
from Ventas.serializers import (
    VentaSerializer,
    VentaDetailSerializer,
    VentaListaSerializer,
    VentaReporteSerializer,
    VentaUpdateSerializer,
    VentaCorreccionAdministrativaSerializer,
)
from Tiendas.models import Tienda
from Recaudos.models import Recaudo
from Clientes.models import Cliente
from Tiendas.permissions import (
    requiere_acceso_tienda,
    usuario_puede_acceder_tienda,
    usuario_es_administrador_tienda,
    respuesta_sin_permiso,
)
from Tiendas.alertas_operativas import registrar_alerta_venta
from Clientes.views import _calcular_score
from Ventas.riesgo import calcular_fecha_vencimiento, normalizar_plazo


def _anotar_ventas_lista(queryset):
    """Prepara ventas para listados sin consultas por cada fila."""
    renovacion = Venta.objects.filter(
        origen_renovacion_id=OuterRef('pk'),
    ).order_by('id')
    return queryset.select_related('cliente').annotate(
        # Las visitas fallidas cuentan como ciclos; la liquidación automática
        # de una renovación no es un ciclo de cobro del crédito anterior.
        _recaudos_count=Count(
            'recaudo',
            filter=Q(recaudo__es_renovacion=False),
        ),
        _ultimo_abono_real=Max(
            'recaudo__fecha_recaudo',
            filter=Q(
                recaudo__valor_recaudo__gt=0,
                recaudo__es_renovacion=False,
            ),
        ),
        _renovacion_id=Subquery(renovacion.values('id')[:1]),
    )


@api_view(['GET'])
@requiere_acceso_tienda
def list_ventas_activas(request, tienda_id=None):
    '''obtenemos todas las ventas'''

    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(tienda=tienda.id).exclude(
        estado_venta='Pagado').exclude(estado_venta='Perdida')
    ventas = _anotar_ventas_lista(ventas)

    if ventas.exists():
        venta_serializer = VentaListaSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado ventas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def list_ventas_a_liquidar(request, date, tienda_id=None):
    '''obtenemos todas las ventas'''

    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()

    ventas = Venta.objects.filter(tienda=tienda.id).exclude(
        estado_venta='Pagado').exclude(estado_venta='Perdida')

    parsed_date = datetime.strptime(date, '%Y-%m-%d')
    ventas = ventas.filter(fecha_venta__lt=parsed_date).exclude(
        recaudo__fecha_recaudo=parsed_date).order_by('id')
    ventas = _anotar_ventas_lista(ventas)
    if ventas.exists():
        venta_serializer = VentaListaSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado ventas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def list_ventas_x_fecha(request, date, tienda_id=None):
    """obtenemos lista de ventas ingresadas en una fecha determinada"""

    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(tienda=tienda).filter(fecha_venta=date)
    reporte_compacto = request.query_params.get('vista') == 'reporte'
    if not reporte_compacto:
        ventas = _anotar_ventas_lista(ventas)
    if ventas.exists():
        serializer_class = VentaReporteSerializer if reporte_compacto else VentaListaSerializer
        venta_serializer = serializer_class(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontraron ventas'}, status=status.HTTP_200_OK)

@api_view(['GET'])
@requiere_acceso_tienda
def list_ventas_x_fecha_range(request, date1, date2, tienda_id=None):
    """obtenemos lista de ventas ingresadas en un rango de fechas determinado"""

    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(tienda=tienda).filter(fecha_venta__range=[date1, date2])
    reporte_compacto = request.query_params.get('vista') == 'reporte'
    if not reporte_compacto:
        ventas = _anotar_ventas_lista(ventas)
    if ventas.exists():
        serializer_class = VentaReporteSerializer if reporte_compacto else VentaListaSerializer
        venta_serializer = serializer_class(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontraron ventas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def list_ventas_activas_cliente(request, pk, tienda_id=None):
    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(tienda=tienda.id).filter(
        cliente=pk).order_by('-id')
    ventas = _anotar_ventas_lista(ventas)
    if ventas.exists():
        venta_serializer = VentaListaSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado ventas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def list_ventas_perdidas(request, tienda_id=None):
    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(
        tienda=tienda.id).filter(estado_venta='Perdida')
    ventas = _anotar_ventas_lista(ventas)
    if ventas.exists():
        venta_serializer = VentaListaSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontraron ventas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_venta(request, pk):
    venta = Venta.objects.filter(id=pk).first()
    if venta:
        if not usuario_puede_acceder_tienda(request.user, venta.tienda_id):
            return respuesta_sin_permiso()
        venta_serializer = VentaDetailSerializer(venta, many=False)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontro la venta'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def put_venta(request, pk, tienda_id=None):
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(
            id=request.user.perfil.tienda.id).first()
    venta = Venta.objects.filter(id=pk).first()
    if venta and not usuario_puede_acceder_tienda(request.user, venta.tienda_id):
        return respuesta_sin_permiso()
    if venta and not usuario_es_administrador_tienda(request.user, venta.tienda_id):
        return Response(
            {'error': 'Solo un administrador puede editar una venta.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    if venta and Recaudo.objects.filter(venta=venta).exists():
        return Response(
            {
                'error': (
                    'Esta venta tiene recaudos. Use la corrección administrativa '
                    'para ajustar únicamente fecha, cuotas y motivo.'
                ),
            },
            status=status.HTTP_409_CONFLICT,
        )
    if venta:
        new_data = request.data.copy()
        fecha_venta = datetime.strptime(new_data['fecha_venta'], '%Y-%m-%d')
        fecha_venta = datetime.date(fecha_venta)
        plazo = normalizar_plazo(new_data.get('plazo') or venta.plazo)
        if not plazo:
            return Response(
                {'plazo': 'El plazo debe ser Diario, Semanal o Mensual.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        new_data['plazo'] = plazo
        new_data['fecha_vencimiento'] = str(
            calcular_fecha_vencimiento(fecha_venta, new_data['cuotas'], plazo)
        )

        venta_serializer = VentaUpdateSerializer(venta, data=new_data)

        if venta_serializer.is_valid():

            vv = venta_serializer.validated_data['valor_venta']
            interes = venta_serializer.validated_data['interes']
            venta_serializer.validated_data['saldo_actual'] = vv + (Decimal(interes) / Decimal(100)) * vv
            if venta_serializer.validated_data['valor_venta'] != venta.valor_venta:
                with transaction.atomic():
                    tienda.caja_inicial = tienda.caja_inicial + venta.valor_venta
                    tienda.caja_inicial = tienda.caja_inicial - \
                        venta_serializer.validated_data['valor_venta']
                    venta_serializer.save()
                    tienda.save()
            else:
                venta_serializer.save()
            return Response(venta_serializer.data, status=status.HTTP_200_OK)
        return Response(venta_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message': 'No se encontró la venta'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
@requiere_acceso_tienda
def corregir_venta_administrativamente(request, pk, tienda_id=None):
    """Corrige fecha y cuotas sin alterar el estado financiero del crédito.

    Es la única vía para editar la cronología de una venta que ya tiene
    recaudos. Exige administrador, motivo y deja un registro auditable.
    """
    venta = Venta.objects.filter(pk=pk).first()
    if not venta:
        return Response(
            {'error': 'No se encontró la venta.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    if tienda_id is not None and str(venta.tienda_id) != str(tienda_id):
        return Response(
            {'error': 'La venta no pertenece a la ruta indicada.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    if not usuario_puede_acceder_tienda(request.user, venta.tienda_id):
        return respuesta_sin_permiso()
    if not usuario_es_administrador_tienda(request.user, venta.tienda_id):
        return Response(
            {'error': 'Solo los administradores pueden hacer esta corrección.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    if not Recaudo.objects.filter(venta=venta).exists():
        return Response(
            {
                'error': (
                    'Esta venta no tiene recaudos. Use la edición normal para '
                    'corregirla.'
                ),
            },
            status=status.HTTP_409_CONFLICT,
        )

    serializer = VentaCorreccionAdministrativaSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    fecha_nueva = serializer.validated_data['fecha_venta']
    cuotas_nuevas = serializer.validated_data['cuotas']
    motivo = serializer.validated_data['motivo']

    if fecha_nueva > timezone.localdate():
        return Response(
            {'error': 'La fecha de venta no puede quedar en el futuro.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if venta.estado_venta in ('Pagado', 'Perdida'):
        return Response(
            {'error': 'No se pueden corregir ventas pagadas o enviadas a pérdida.'},
            status=status.HTTP_409_CONFLICT,
        )
    if venta.origen_renovacion_id or venta.renovacion.exists():
        return Response(
            {'error': 'No se pueden corregir ventas relacionadas con una renovación.'},
            status=status.HTTP_409_CONFLICT,
        )

    primer_abono = (
        Recaudo.objects.filter(
            venta=venta,
            valor_recaudo__gt=0,
            es_renovacion=False,
        )
        .order_by('fecha_recaudo', 'id')
        .first()
    )
    if primer_abono and fecha_nueva > primer_abono.fecha_recaudo:
        return Response(
            {
                'error': (
                    'La fecha no puede ser posterior al primer abono real '
                    f'({primer_abono.fecha_recaudo:%d/%m/%Y}).'
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    fecha_vencimiento_nueva = calcular_fecha_vencimiento(
        fecha_nueva,
        cuotas_nuevas,
        venta.plazo,
    )
    total_a_pagar = venta.total_a_pagar()
    valor_cuota_nueva = (total_a_pagar / cuotas_nuevas).quantize(Decimal('0.01'))
    valor_cuota_anterior = venta.valor_cuota()
    if valor_cuota_anterior:
        valor_cuota_anterior = valor_cuota_anterior.quantize(Decimal('0.01'))
    else:
        valor_cuota_anterior = Decimal('0.00')

    with transaction.atomic():
        venta = Venta.objects.select_for_update().get(pk=venta.pk)
        ajuste = AjusteVentaAdministrativo.objects.create(
            venta=venta,
            usuario=request.user,
            motivo=motivo,
            fecha_venta_anterior=venta.fecha_venta,
            fecha_venta_nueva=fecha_nueva,
            cuotas_anteriores=venta.cuotas,
            cuotas_nuevas=cuotas_nuevas,
            fecha_vencimiento_anterior=venta.fecha_vencimiento,
            fecha_vencimiento_nueva=fecha_vencimiento_nueva,
            valor_cuota_anterior=valor_cuota_anterior,
            valor_cuota_nueva=valor_cuota_nueva,
        )
        venta.fecha_venta = fecha_nueva
        venta.cuotas = cuotas_nuevas
        venta.fecha_vencimiento = fecha_vencimiento_nueva
        # saldo_actual, estado, capital, intereses y recaudos se preservan.
        venta.save(update_fields=['fecha_venta', 'cuotas', 'fecha_vencimiento'])

        try:
            from Tiendas.telegram_bot import notificar_correccion_venta
            transaction.on_commit(
                lambda: notificar_correccion_venta(
                    venta,
                    request.user,
                    ajuste,
                )
            )
        except Exception:
            # Telegram es una notificación secundaria; nunca debe deshacer la
            # corrección ni esconder un cambio que quedó auditado.
            pass

    return Response(
        {
            'venta': VentaDetailSerializer(venta).data,
            'ajuste': {
                'id': ajuste.id,
                'motivo': ajuste.motivo,
                'fecha_venta_anterior': ajuste.fecha_venta_anterior,
                'fecha_venta_nueva': ajuste.fecha_venta_nueva,
                'cuotas_anteriores': ajuste.cuotas_anteriores,
                'cuotas_nuevas': ajuste.cuotas_nuevas,
            },
        },
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
@requiere_acceso_tienda
def post_venta(request, tienda_id=None):
    '''creamos una venta'''
    if request.method == 'POST':
        if tienda_id:
            tienda = Tienda.objects.filter(id=tienda_id).first()
        else:
            tienda = Tienda.objects.filter(
                id=request.user.perfil.tienda.id).first()
        new_data = request.data.copy()
        new_data['tienda'] = tienda.id
        new_data['creado_por'] = request.user.id
        plazo_recibido = new_data.get('plazo') or 'Diario'
        plazo = normalizar_plazo(plazo_recibido)
        if not plazo:
            return Response(
                {'plazo': 'El plazo debe ser Diario, Semanal o Mensual.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not usuario_es_administrador_tienda(request.user, tienda.id):
            if plazo != 'Diario':
                return Response(
                    {'error': 'Solo un administrador puede seleccionar un plazo distinto de Diario.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            plazo = 'Diario'
        new_data['plazo'] = plazo
        valor_venta = Decimal(str(new_data['valor_venta']))
        interes = Decimal(str(new_data['interes']))
        try:
            score_previo = _calcular_score(new_data['cliente'], tienda.id)
        except (TypeError, ValueError, Cliente.DoesNotExist):
            score_previo = None
        new_data['saldo_actual'] = valor_venta + (interes / Decimal(100)) * valor_venta
        fecha_venta = datetime.strptime(new_data['fecha_venta'], '%Y-%m-%d')
        fecha_venta = datetime.date(fecha_venta)
        new_data['fecha_vencimiento'] = str(
            calcular_fecha_vencimiento(fecha_venta, new_data['cuotas'], plazo)
        )
        venta_serializer = VentaSerializer(data=new_data)
        if venta_serializer.is_valid():
            with transaction.atomic():
                venta_nueva = venta_serializer.save(creado_por=request.user)
                tienda.caja_inicial = tienda.caja_inicial - \
                    venta_serializer.validated_data['valor_venta']
                tienda.save()
                # La alerta se programa después del commit. Si Telegram está
                # caído, la venta igualmente se completa sin error.
                registrar_alerta_venta(
                    venta_nueva,
                    score_previo=score_previo,
                    usuario=request.user,
                )
            return Response(venta_serializer.data, status=status.HTTP_200_OK)
        return Response(venta_serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
def delete_venta(request, pk, tienda_id=None):
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(
            id=request.user.perfil.tienda.id).first()
    venta = Venta.objects.filter(id=pk).first()
    if not venta:
        return Response({'message': 'No se encontró la venta'}, status=status.HTTP_400_BAD_REQUEST)
    if not usuario_puede_acceder_tienda(request.user, venta.tienda_id):
        return respuesta_sin_permiso()
    recaudos = Recaudo.objects.filter(venta=venta.id)
    if recaudos:
        return Response({'message': 'No se puede eliminar la venta por que ya se realizaron pagos a la misma.'}, status=status.HTTP_406_NOT_ACCEPTABLE)
    with transaction.atomic():
        venta.delete()
        tienda.caja_inicial = tienda.caja_inicial + venta.valor_venta
        tienda.save()
    return Response({'message': 'Venta eliminada correctamente'}, status=status.HTTP_200_OK)


@api_view(['PUT'])
def perdida_venta(request, pk):
    venta = Venta.objects.filter(id=pk).first()
    if venta:
        if not usuario_puede_acceder_tienda(request.user, venta.tienda_id):
            return respuesta_sin_permiso()
        cliente = Cliente.objects.get(pk=venta.cliente.id)
        cliente.estado_cliente = 'Bloqueado'
        cliente.save()
        venta.estado_venta = 'Perdida'
        venta.comentario = 'Venta en pérdida, cliente bloqueado'
        venta.save()
        return Response({'message': 'Venta enviada como pérdida.'})
    else:
        return Response({'message': 'No se encontró la venta'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@requiere_acceso_tienda
def renovar_venta(request, pk, tienda_id=None):
    """Renueva un crédito atómicamente:
    1) Cierra el crédito vencido con un Recaudo marcado es_renovacion=True
    2) Crea un crédito nuevo apuntando al viejo (origen_renovacion)
    Caja no se mueve (entra el saldo y sale como nuevo capital → neto 0).
    El recaudo de renovación NO cuenta como pago en el score crediticio.
    """
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if not tienda:
        return Response({'error': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    venta_vieja = Venta.objects.filter(id=pk, tienda=tienda).first()
    if not venta_vieja:
        return Response({'error': 'Crédito no encontrado'}, status=status.HTTP_404_NOT_FOUND)
    if venta_vieja.estado_venta == 'Pagado':
        return Response({'error': 'El crédito ya está pagado'}, status=status.HTTP_409_CONFLICT)
    if venta_vieja.estado_venta == 'Perdida':
        return Response({'error': 'No se puede renovar un crédito en pérdida'}, status=status.HTTP_409_CONFLICT)

    try:
        fecha_venta = datetime.strptime(request.data.get('fecha_venta'), '%Y-%m-%d').date()
        interes = int(request.data.get('interes'))
        cuotas = int(request.data.get('cuotas'))
    except (TypeError, ValueError):
        return Response({'error': 'fecha_venta, interes y cuotas son requeridos'}, status=status.HTTP_400_BAD_REQUEST)
    if cuotas < 1 or interes < 0:
        return Response({'error': 'interes y cuotas inválidos'}, status=status.HTTP_400_BAD_REQUEST)

    saldo = venta_vieja.saldo_actual or Decimal('0')
    if saldo <= 0:
        return Response({'error': 'No hay saldo pendiente para renovar'}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        # 1. Recaudo que cierra el viejo (marcado como renovación)
        Recaudo.objects.create(
            fecha_recaudo=fecha_venta,
            valor_recaudo=saldo,
            venta=venta_vieja,
            tienda=tienda,
            es_renovacion=True,
        )
        venta_vieja.saldo_actual = 0
        venta_vieja.estado_venta = 'Pagado'
        venta_vieja.save()

        # 2. Nueva venta vinculada al original
        nuevo_total = saldo + (Decimal(interes) / Decimal(100)) * saldo
        fecha_vencimiento = calcular_fecha_vencimiento(
            fecha_venta,
            cuotas,
            venta_vieja.plazo,
        )
        nueva_venta = Venta.objects.create(
            fecha_venta=fecha_venta,
            cliente=venta_vieja.cliente,
            valor_venta=saldo,
            interes=interes,
            cuotas=cuotas,
            plazo=venta_vieja.plazo,
            comentario=f'Renovación de crédito #{venta_vieja.id}',
            creado_por=request.user,
            estado_venta='Vigente',
            saldo_actual=nuevo_total,
            fecha_vencimiento=fecha_vencimiento,
            tienda=tienda,
            origen_renovacion=venta_vieja,
        )
        # Caja: +saldo (recaudo) -saldo (capital nuevo) = neto 0. No tocar.

    return Response({
        'venta_anterior_id': venta_vieja.id,
        'nueva_venta_id': nueva_venta.id,
        'saldo_renovado': str(saldo),
    }, status=status.HTTP_201_CREATED)
