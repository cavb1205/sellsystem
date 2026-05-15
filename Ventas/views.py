from decimal import Decimal
from datetime import date, datetime, timedelta
import Ventas

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from django.db import transaction

from Ventas.models import Venta
from Ventas.serializers import VentaSerializer, VentaDetailSerializer, VentaUpdateSerializer
from Tiendas.models import Tienda
from Recaudos.models import Recaudo
from Clientes.models import Cliente


@api_view(['GET'])
def list_ventas_activas(request, tienda_id=None):
    '''obtenemos todas las ventas'''

    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(tienda=tienda.id).exclude(
        estado_venta='Pagado').exclude(estado_venta='Perdida')

    if ventas:
        venta_serializer = VentaDetailSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado ventas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
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
    if ventas:
        venta_serializer = VentaDetailSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado ventas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def list_ventas_x_fecha(request, date, tienda_id=None):
    """obtenemos lista de ventas ingresadas en una fecha determinada"""

    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(tienda=tienda).filter(fecha_venta=date)
    if ventas:
        venta_serializer = VentaDetailSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontraron ventas'}, status=status.HTTP_200_OK)

@api_view(['GET'])
def list_ventas_x_fecha_range(request, date1, date2, tienda_id=None):
    """obtenemos lista de ventas ingresadas en un rango de fechas determinado"""

    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(tienda=tienda).filter(fecha_venta__range=[date1, date2])
    if ventas:
        venta_serializer = VentaDetailSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontraron ventas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def list_ventas_activas_cliente(request, pk, tienda_id=None):
    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(tienda=tienda.id).filter(
        cliente=pk).order_by('-id')
    if ventas:
        venta_serializer = VentaDetailSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado ventas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def list_ventas_perdidas(request, tienda_id=None):
    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(
        tienda=tienda.id).filter(estado_venta='Perdida')
    if ventas:
        venta_serializer = VentaDetailSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontraron ventas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_venta(request, pk):
    venta = Venta.objects.filter(id=pk).first()
    if venta:
        venta_serializer = VentaDetailSerializer(venta, many=False)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontro la venta'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_venta(request, pk, tienda_id=None):
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(
            id=request.user.perfil.tienda.id).first()
    venta = Venta.objects.filter(id=pk).first()
    if venta:
        new_data = request.data
        fecha_venta = datetime.strptime(new_data['fecha_venta'], '%Y-%m-%d')
        fecha_venta = datetime.date(fecha_venta)
        new_data['fecha_vencimiento'] = str(
            fecha_venta + timedelta(days=(int(new_data['cuotas'])+4)))

        venta_serializer = VentaUpdateSerializer(venta, data=new_data)

        if venta_serializer.is_valid():

            venta_serializer.validated_data['saldo_actual'] = venta_serializer.validated_data['valor_venta'] + (
                Decimal(venta_serializer.validated_data['interes'] / 100) * venta_serializer.validated_data['valor_venta'])
            if venta_serializer.validated_data['valor_venta'] != venta.valor_venta:
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


@api_view(['POST'])
def post_venta(request, tienda_id=None):
    '''creamos una venta'''
    if request.method == 'POST':
        if tienda_id:
            tienda = Tienda.objects.filter(id=tienda_id).first()
        else:
            tienda = Tienda.objects.filter(
                id=request.user.perfil.tienda.id).first()
        new_data = request.data
        new_data['tienda'] = tienda.id
        valor_venta = int(new_data['valor_venta'])
        new_data['saldo_actual'] = int(
            valor_venta + ((int(new_data['interes'])/100) * valor_venta))
        fecha_venta = datetime.strptime(new_data['fecha_venta'], '%Y-%m-%d')
        fecha_venta = datetime.date(fecha_venta)
        new_data['fecha_vencimiento'] = str(
            fecha_venta + timedelta(days=(int(new_data['cuotas'])+4)))
        venta_serializer = VentaSerializer(data=new_data)
        if venta_serializer.is_valid():
            venta_serializer.save()
            tienda.caja_inicial = tienda.caja_inicial - \
                venta_serializer.validated_data['valor_venta']
            tienda.save()
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
    recaudos = Recaudo.objects.filter(venta=venta.id)
    if recaudos:
        return Response({'message': 'No se puede eliminar la venta por que ya se realizaron pagos a la misma.'}, status=status.HTTP_406_NOT_ACCEPTABLE)
    else:
        venta.delete()
        tienda.caja_inicial = tienda.caja_inicial + venta.valor_venta
        tienda.save()
        return Response({'message': 'Venta eliminada correctamente'}, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontró la venta'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def perdida_venta(request, pk):
    venta = Venta.objects.filter(id=pk).first()
    if venta:
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
        nuevo_total = int(saldo + (Decimal(interes) / 100) * saldo)
        fecha_vencimiento = fecha_venta + timedelta(days=cuotas + 4)
        nueva_venta = Venta.objects.create(
            fecha_venta=fecha_venta,
            cliente=venta_vieja.cliente,
            valor_venta=saldo,
            interes=interes,
            cuotas=cuotas,
            plazo=venta_vieja.plazo,
            comentario=f'Renovación de crédito #{venta_vieja.id}',
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
