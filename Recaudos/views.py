from itertools import count
from Ventas.models import Venta
from rest_framework.decorators import api_view
from rest_framework import generics
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import LimitOffsetPagination
from django.core.paginator import Paginator

from django.db import transaction

from Recaudos.models import Recaudo, Visita_Blanco
from Recaudos.serializers import (
    RecaudoSerializer,
    Visitas_BlancoSerializer,
    RecaudoDetailSerializer,
    RecaudoHistorialSerializer,
    RecaudoListaSerializer,
    RecaudoUpdateSerializer,
)
from Tiendas.models import Tienda
from Tiendas.alertas_operativas import revisar_riesgo_venta
from Tiendas.views import comprobar_estado_membresia
from Tiendas.permissions import requiere_acceso_tienda, usuario_puede_acceder_tienda, respuesta_sin_permiso
from Ventas.models import Venta

from datetime import datetime
from decimal import Decimal
from django.utils import timezone


def _validar_ruta_de_venta(venta, tienda):
    """Evita registrar dinero de una venta en la caja de otra ruta."""
    if not tienda:
        return Response(
            {'error': 'Ruta no encontrada'},
            status=status.HTTP_404_NOT_FOUND,
        )
    if venta.tienda_id != tienda.id:
        return Response(
            {
                'error': (
                    f'La venta #{venta.id} pertenece a otra ruta. '
                    'Selecciona la ruta de la venta antes de registrar el recaudo.'
                ),
            },
            status=status.HTTP_409_CONFLICT,
        )
    return None

@api_view(['GET'])
def list_recaudos(request):
    '''obtenemos todas las recaudos'''
    user = request.user
    tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    recaudos = Recaudo.objects.filter(tienda=tienda.id)
    vista_lista = request.query_params.get('vista') == 'lista'
    if vista_lista:
        recaudos = recaudos.select_related('venta__cliente', 'visita_blanco')
    if recaudos.exists():
        serializer_class = RecaudoListaSerializer if vista_lista else RecaudoDetailSerializer
        recaudo_serializer = serializer_class(recaudos, many=True)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado recaudos'}, status=status.HTTP_200_OK)

@api_view(['GET'])
@requiere_acceso_tienda
def list_recaudos_fecha(request, date, tienda_id=None):
    '''obtenemos todas las recaudos'''

    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    comprobar_estado_membresia(tienda.id)
    recaudos = Recaudo.objects.filter(tienda=tienda.id).filter(fecha_recaudo=date)
    vista_lista = request.query_params.get('vista') == 'lista'
    if vista_lista:
        recaudos = recaudos.select_related('venta__cliente', 'visita_blanco')
    if recaudos.exists():
        serializer_class = RecaudoListaSerializer if vista_lista else RecaudoDetailSerializer
        recaudo_serializer = serializer_class(recaudos, many=True)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado recaudos'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def calcular_sueldo_trabajador(request, date1, date2, porcentaje=None, tienda_id=None):
    '''
    Calcula el sueldo del trabajador basado en un porcentaje de los recaudos 
    en un rango de fechas específico.
    '''
    
    try:
        # Determinar la tienda
        if tienda_id:
            try:
                tienda_id_int = int(tienda_id)
                tienda = Tienda.objects.filter(id=tienda_id_int).first()
            except ValueError:
                return Response({'error': 'ID de tienda debe ser un número'}, 
                               status=status.HTTP_400_BAD_REQUEST)
        else:
            user = request.user
            if hasattr(user, 'perfil') and hasattr(user.perfil, 'tienda'):
                tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
            else:
                return Response({'error': 'Usuario no tiene tienda asociada'}, 
                               status=status.HTTP_400_BAD_REQUEST)
        
        if not tienda:
            return Response({'error': 'Tienda no encontrada'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Convertir strings a objetos date
        try:
            fecha_inicio_obj = datetime.strptime(date1, '%Y-%m-%d').date()
            fecha_fin_obj = datetime.strptime(date2, '%Y-%m-%d').date()
        except ValueError:
            return Response({'error': 'Formato de fecha incorrecto. Use YYYY-MM-DD'}, 
                           status=status.HTTP_400_BAD_REQUEST)
        
        # Validar que fecha_inicio no sea mayor que fecha_fin
        if fecha_inicio_obj > fecha_fin_obj:
            return Response({'error': 'fecha_inicio no puede ser mayor que fecha_fin'}, 
                           status=status.HTTP_400_BAD_REQUEST)
        
        # Establecer porcentaje por defecto si no se proporciona
        if porcentaje is None:
            porcentaje_valor = 3.0
        else:
            try:
                porcentaje_valor = float(porcentaje)
            except ValueError:
                return Response({'error': 'Porcentaje debe ser un número válido'}, 
                               status=status.HTTP_400_BAD_REQUEST)
        
        # Filtrar recaudos para el rango de fechas
        recaudos = Recaudo.objects.filter(
            tienda=tienda.id,
            fecha_recaudo__range=[fecha_inicio_obj, fecha_fin_obj]
        )
        
        # Calcular total recaudado usando aggregate para evitar problemas con None
        from django.db.models import Sum
        total_recaudado = recaudos.aggregate(total=Sum('valor_recaudo'))['total'] or 0

        total_recaudado_float = float(total_recaudado)
        sueldo = total_recaudado_float * (porcentaje_valor / 100)
        
        return Response({
            'fecha_inicio': fecha_inicio_obj,
            'fecha_fin': fecha_fin_obj,
            'total_recaudado': float(total_recaudado),
            'porcentaje_aplicado': porcentaje_valor,
            'sueldo_calculado': float(sueldo),
            'cantidad_recaudos': recaudos.count(),
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'error': 'Error interno del servidor'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
@api_view(['GET'])
def list_recaudos_venta(request, venta_id):
    '''obtenemos los recaudos pertenecientes a una venta en particular'''

    venta = Venta.objects.filter(id=venta_id).first()
    if venta and not usuario_puede_acceder_tienda(request.user, venta.tienda_id):
        return respuesta_sin_permiso()
    recaudos = Recaudo.objects.filter(venta=venta_id).order_by('-id')

    if recaudos:
        recaudo_serializer = RecaudoDetailSerializer(recaudos, many=True)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado recaudos'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def list_recaudos_venta_paginados(request, venta_id):
    """Devuelve el historial de una venta por páginas y sin datos anidados.

    La ruta anterior se conserva para no romper otros consumidores. Esta
    versión evita serializar la venta completa dentro de cada recaudo y solo
    consulta el bloque que la pantalla está mostrando.
    """

    venta = Venta.objects.filter(id=venta_id).first()
    if not venta:
        return Response(
            {'message': 'No se encontró la venta'},
            status=status.HTTP_404_NOT_FOUND,
        )
    if not usuario_puede_acceder_tienda(request.user, venta.tienda_id):
        return respuesta_sin_permiso()

    try:
        page_size = int(request.query_params.get('page_size', 8))
    except (TypeError, ValueError):
        page_size = 8
    page_size = min(max(page_size, 1), 50)

    try:
        page_number = int(request.query_params.get('page', 1))
    except (TypeError, ValueError):
        page_number = 1
    page_number = max(page_number, 1)

    filtro = request.query_params.get('filtro', 'todos')
    base = Recaudo.objects.filter(venta_id=venta_id).order_by('-id')
    counts = {
        'todos': base.count(),
        'abonos': base.filter(valor_recaudo__gt=0).count(),
        'fallidas': base.filter(
            valor_recaudo=0,
            visita_blanco__isnull=False,
        ).count(),
    }

    if filtro == 'abonos':
        queryset = base.filter(valor_recaudo__gt=0)
    elif filtro == 'fallidas':
        queryset = base.filter(
            valor_recaudo=0,
            visita_blanco__isnull=False,
        )
    else:
        filtro = 'todos'
        queryset = base

    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(page_number)
    recaudos = page_obj.object_list.select_related('visita_blanco')

    return Response({
        'count': paginator.count,
        'total': counts['todos'],
        'counts': counts,
        'filtro': filtro,
        'page': page_obj.number,
        'page_size': page_size,
        'pages': paginator.num_pages,
        'latest_id': base.values_list('id', flat=True).first(),
        'results': RecaudoHistorialSerializer(recaudos, many=True).data,
    }, status=status.HTTP_200_OK)

    

@api_view(['GET'])
def get_recaudo(request, pk):
    recaudo = Recaudo.objects.filter(id=pk).first()
    if recaudo:
        if not usuario_puede_acceder_tienda(request.user, recaudo.tienda_id):
            return respuesta_sin_permiso()
        recaudo_serializer = RecaudoDetailSerializer(recaudo, many=False)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message':'No se encontro el recaudo'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_recaudo(request, pk, tienda_id=None):
    recaudo = Recaudo.objects.filter(id=pk).first()
    if not recaudo:
        return Response({'message':'No se encontró el recaudo'}, status=status.HTTP_400_BAD_REQUEST)
    if not usuario_puede_acceder_tienda(request.user, recaudo.tienda_id):
        return respuesta_sin_permiso()
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=recaudo.tienda_id).first()
    venta = Venta.objects.filter(id=recaudo.venta_id).first()
    if not venta:
        return Response({'message': 'Venta no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    if not tienda:
        return Response({'message': 'Ruta no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    if recaudo.tienda_id != venta.tienda_id or tienda.id != recaudo.tienda_id:
        return Response(
            {
                'error': (
                    'Este recaudo está asociado a una ruta distinta de la venta. '
                    'Corrige primero la asignación administrativa.'
                ),
            },
            status=status.HTTP_409_CONFLICT,
        )
    recaudo_serializer = RecaudoUpdateSerializer(recaudo, data=request.data)
    if recaudo_serializer.is_valid():
        nuevo_valor = recaudo_serializer.validated_data['valor_recaudo']
        if nuevo_valor < Decimal('0'):
            return Response(
                {'error': 'El valor del recaudo no puede ser negativo.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        saldo_disponible = (venta.saldo_actual or Decimal('0')) + recaudo.valor_recaudo
        if nuevo_valor > saldo_disponible:
            return Response(
                {
                    'error': (
                        f'El nuevo recaudo no puede superar el saldo disponible '
                        f'de ${saldo_disponible:,.0f}.'
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            recaudo = Recaudo.objects.select_for_update().get(pk=pk)
            venta = Venta.objects.select_for_update().get(pk=recaudo.venta_id)
            tienda = Tienda.objects.select_for_update().get(pk=recaudo.tienda_id)
            saldo_disponible = (venta.saldo_actual or Decimal('0')) + recaudo.valor_recaudo
            if nuevo_valor > saldo_disponible:
                return Response(
                    {
                        'error': (
                            f'El nuevo recaudo no puede superar el saldo disponible '
                            f'de ${saldo_disponible:,.0f}.'
                        ),
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            diferencia = nuevo_valor - recaudo.valor_recaudo
            recaudo.fecha_recaudo = recaudo_serializer.validated_data['fecha_recaudo']
            recaudo.valor_recaudo = nuevo_valor
            recaudo.save(update_fields=['fecha_recaudo', 'valor_recaudo'])
            venta.saldo_actual = (venta.saldo_actual or Decimal('0')) - diferencia
            recaudos = Recaudo.objects.filter(
                venta=venta.id,
                es_renovacion=False,
            )
            if venta.promedio_pago() >= venta.valor_cuota():
                venta.estado_venta = 'Vigente'
            if venta.promedio_pago() < venta.valor_cuota():
                venta.estado_venta = 'Atrasado'
            if venta.cuotas < recaudos.count():
                venta.estado_venta = 'Vencido'
            if venta.saldo_actual <= 0:
                venta.estado_venta = 'Pagado'
            tienda.caja_inicial = tienda.caja_inicial - diferencia
            tienda.save(update_fields=['caja_inicial'])
            venta.save(update_fields=['saldo_actual', 'estado_venta'])

        revisar_riesgo_venta(venta)
        return Response(
            RecaudoUpdateSerializer(recaudo).data,
            status=status.HTTP_200_OK,
        )
    return Response(recaudo_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        

@api_view(['POST'])
@requiere_acceso_tienda
def post_recaudo(request, tienda_id=None):
    '''creamos una recaudo'''
    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    if not tienda:
        return Response({'error': 'Ruta no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    new_data = request.data.copy()
    new_data['tienda'] = tienda.id
    # Las renovaciones se crean únicamente mediante renovar_venta().
    new_data['es_renovacion'] = False

    venta = Venta.objects.filter(id=new_data.get('venta')).first()
    if not venta:
        return Response({'message': 'Venta no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    error_ruta = _validar_ruta_de_venta(venta, tienda)
    if error_ruta:
        return error_ruta

    if request.method == 'POST':
        recaudo_serializer = RecaudoSerializer(data = new_data)
        if recaudo_serializer.is_valid():
            valor_recaudo = recaudo_serializer.validated_data['valor_recaudo']
            if valor_recaudo <= Decimal('0'):
                return Response(
                    {'error': 'El valor del abono debe ser mayor que cero.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            with transaction.atomic():
                venta = Venta.objects.select_for_update().get(pk=venta.pk)
                tienda = Tienda.objects.select_for_update().get(pk=tienda.pk)
                if venta.tienda_id != tienda.id:
                    return Response(
                        {'error': 'La venta pertenece a otra ruta.'},
                        status=status.HTTP_409_CONFLICT,
                    )
                saldo_actual = venta.saldo_actual
                if saldo_actual is None:
                    return Response(
                        {'error': 'La venta no tiene un saldo válido para recibir abonos.'},
                        status=status.HTTP_409_CONFLICT,
                    )
                if valor_recaudo > saldo_actual:
                    return Response(
                        {
                            'error': (
                                f'El abono no puede superar el saldo pendiente '
                                f'de ${saldo_actual:,.0f}.'
                            ),
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
                recaudo_serializer.save(
                    venta=venta,
                    tienda=tienda,
                    es_renovacion=False,
                )
                tienda.caja_inicial = tienda.caja_inicial + valor_recaudo
                venta.saldo_actual = saldo_actual - valor_recaudo
                recaudos = Recaudo.objects.filter(
                    venta=venta.id,
                    es_renovacion=False,
                )
                if venta.promedio_pago() >= venta.valor_cuota():
                    venta.estado_venta = 'Vigente'
                if venta.promedio_pago() < venta.valor_cuota():
                    venta.estado_venta = 'Atrasado'
                if venta.cuotas < recaudos.count():
                    venta.estado_venta = 'Vencido'
                if venta.saldo_actual <= 0:
                    venta.estado_venta = 'Pagado'
                tienda.save()
                venta.save()
                revisar_riesgo_venta(venta)
            return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
        return Response(recaudo_serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@requiere_acceso_tienda
def post_recaudo_no_pay(request, tienda_id=None):
    '''creamos una recaudo'''
    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    if not tienda:
        return Response({'error': 'Ruta no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    new_data = request.data.copy()
    visita_blanco = new_data['visita_blanco']

    new_data['tienda'] = tienda.id
    new_data['valor_recaudo'] = Decimal('0')
    new_data['es_renovacion'] = False
    venta = Venta.objects.filter(id=new_data.get('venta')).first()
    if not venta:
        return Response({'message': 'Venta no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    error_ruta = _validar_ruta_de_venta(venta, tienda)
    if error_ruta:
        return error_ruta

    if request.method == 'POST':
        visita_blanco_serializer = Visitas_BlancoSerializer(data=visita_blanco)
        if not visita_blanco_serializer.is_valid():
            return Response(visita_blanco_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            venta = Venta.objects.select_for_update().get(pk=venta.pk)
            tienda = Tienda.objects.select_for_update().get(pk=tienda.pk)
            visita_blanco = visita_blanco_serializer.save()
            new_data['visita_blanco'] = visita_blanco.id
            recaudo_serializer = RecaudoSerializer(data=new_data)
            if not recaudo_serializer.is_valid():
                # recaudo inválido → revierte la visita_blanco recién creada
                transaction.set_rollback(True)
                return Response(recaudo_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            recaudo_serializer.save(
                venta=venta,
                tienda=tienda,
                es_renovacion=False,
            )
            tienda.caja_inicial = tienda.caja_inicial + recaudo_serializer.validated_data['valor_recaudo']
            venta.saldo_actual = venta.saldo_actual - recaudo_serializer.validated_data['valor_recaudo']
            recaudos = Recaudo.objects.filter(
                venta=venta.id,
                es_renovacion=False,
            )
            if venta.promedio_pago() >= venta.valor_cuota():
                venta.estado_venta = 'Vigente'
            if venta.promedio_pago() < venta.valor_cuota():
                venta.estado_venta = 'Atrasado'
            if venta.cuotas < recaudos.count():
                venta.estado_venta = 'Vencido'
            if venta.saldo_actual <= 0:
                venta.estado_venta = 'Pagado'
            tienda.save()
            venta.save()
            revisar_riesgo_venta(venta)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)

@api_view(['DELETE'])
def delete_recaudo(request, pk):
    recaudo = Recaudo.objects.filter(id=pk).first()
    if not recaudo:
        return Response({'message':'No se encontró el recaudo'}, status=status.HTTP_400_BAD_REQUEST)
    if not usuario_puede_acceder_tienda(request.user, recaudo.tienda_id):
        return respuesta_sin_permiso()
    tienda = Tienda.objects.filter(id=recaudo.tienda_id).first()
    venta = Venta.objects.filter(id=recaudo.venta_id).first()
    if venta and venta.tienda_id != recaudo.tienda_id:
        return Response(
            {
                'error': (
                    'Este recaudo está asociado a una ruta distinta de la venta. '
                    'Corrige primero la asignación administrativa.'
                ),
            },
            status=status.HTTP_409_CONFLICT,
        )
    if recaudo:
        with transaction.atomic():
            tienda.caja_inicial = tienda.caja_inicial - recaudo.valor_recaudo
            venta.saldo_actual = venta.saldo_actual + recaudo.valor_recaudo
            recaudos = Recaudo.objects.filter(
                venta=venta.id,
                es_renovacion=False,
            )
            recaudo.delete()
            if venta.promedio_pago() >= venta.valor_cuota():
                venta.estado_venta = 'Vigente'
            elif venta.promedio_pago() < venta.valor_cuota():
                venta.estado_venta = 'Atrasado'
            elif venta.cuotas < recaudos.count():
                venta.estado_venta = 'Vencido'
            elif venta.saldo_actual <= 0:
                venta.estado_venta = 'Pagado'
            tienda.save()
            venta.save()
            revisar_riesgo_venta(venta)
        return Response({'message':'Recaudo eliminado correctamente'},status=status.HTTP_200_OK)
    return Response({'message':'No se encontró el recaudo'}, status=status.HTTP_400_BAD_REQUEST)
