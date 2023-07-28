from decimal import Decimal
from datetime import date, datetime, timedelta
import Ventas

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

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

    ventas = ventas.exclude(recaudo__fecha_recaudo=datetime.strptime(
        date, '%Y-%m-%d')).order_by('id').exclude(fecha_venta=datetime.strptime(date, '%Y-%m-%d'))
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
