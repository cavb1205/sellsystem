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


@api_view(['GET'])
def list_ventas_all(request):
    '''obtenemos todas las ventas'''

    user = request.user
    tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    
    ventas = Venta.objects.filter(tienda=tienda.id)
    
    if ventas:
        venta_serializer = VentaDetailSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se encontraron ventas'}, status=status.HTTP_200_OK)

@api_view(['GET'])
def list_ventas_activas(request):
    '''obtenemos todas las ventas'''

    user = request.user
    tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    
    ventas = Venta.objects.filter(tienda=tienda.id).exclude(estado_venta='Pagado').exclude(estado_venta='Perdida')
    
    if ventas:
        venta_serializer = VentaDetailSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado ventas'}, status=status.HTTP_200_OK)

@api_view(['GET'])
def list_ventas_a_liquidar(request, date ):
    '''obtenemos todas las ventas'''

    user = request.user
    tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    
    ventas = Venta.objects.filter(tienda=tienda.id).exclude(estado_venta='Pagado').exclude(estado_venta='Perdida')
    
    ventas = ventas.exclude(recaudo__fecha_recaudo=datetime.strptime(date,'%Y-%m-%d')).order_by('id')
    if ventas:
        venta_serializer = VentaDetailSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado ventas'}, status=status.HTTP_200_OK)

@api_view(['GET'])
def list_ventas_activas_cliente(request,pk):
    user = request.user
    tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(tienda=tienda.id).filter(cliente=pk).order_by('-id')
    if ventas:
        venta_serializer = VentaDetailSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado ventas'}, status=status.HTTP_200_OK)
    
@api_view(['GET'])
def list_ventas_pagas(request): 
    user = request.user
    tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas = Venta.objects.filter(tienda=tienda.id).filter(estado_venta='Pagado')
    if ventas:
        venta_serializer = VentaDetailSerializer(ventas, many=True)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado ventas'}, status=status.HTTP_200_OK)

@api_view(['GET'])
def get_venta(request, pk):
    venta = Venta.objects.filter(id=pk).first()
    
    if venta:
        venta_serializer = VentaDetailSerializer(venta, many=False)
        return Response(venta_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message':'No se encontro la venta'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_venta(request, pk):
    venta = Venta.objects.filter(id=pk).first()
    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if venta:
        new_data = request.data
        fecha_venta=datetime.strptime(new_data['fecha_venta'],'%Y-%m-%d')
        fecha_venta=datetime.date(fecha_venta)
        new_data['fecha_vencimiento'] = str(fecha_venta + timedelta(days=(int(new_data['cuotas'])+4)))
        
        venta_serializer = VentaUpdateSerializer(venta, data=new_data)
        
        
        if venta_serializer.is_valid():
            
            venta_serializer.validated_data['saldo_actual'] = venta_serializer.validated_data['valor_venta'] + (Decimal(venta_serializer.validated_data['interes'] / 100) * venta_serializer.validated_data['valor_venta'])
            
            if venta_serializer.validated_data['valor_venta'] != venta.valor_venta:
                tienda.caja_inicial = tienda.caja_inicial + venta.valor_venta
                tienda.caja_inicial = tienda.caja_inicial - venta_serializer.validated_data['valor_venta']
                
                venta_serializer.save()
                tienda.save()
            else:
                venta_serializer.save()
                

            return Response(venta_serializer.data,status=status.HTTP_200_OK)
        return Response(venta_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message':'No se encontr?? la venta'}, status=status.HTTP_400_BAD_REQUEST)
        

@api_view(['POST'])
def post_venta(request):
    '''creamos una venta'''
    if request.method == 'POST':
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
        new_data = request.data
        new_data['tienda']=tienda.id
        valor_venta = int(new_data['valor_venta'])
        new_data['saldo_actual'] = int(valor_venta + ((int(new_data['interes'])/100) * valor_venta))
        fecha_venta=datetime.strptime(new_data['fecha_venta'],'%Y-%m-%d')
        fecha_venta=datetime.date(fecha_venta)
        new_data['fecha_vencimiento'] = str(fecha_venta + timedelta(days=(int(new_data['cuotas'])+4)))
        venta_serializer = VentaSerializer(data = new_data)
        if venta_serializer.is_valid():
            venta_serializer.save()
            tienda.caja_inicial = tienda.caja_inicial - venta_serializer.validated_data['valor_venta']
            tienda.save()
            return Response(venta_serializer.data, status=status.HTTP_200_OK)
        return Response(venta_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['DELETE'])
def delete_venta(request, pk):
    venta = Venta.objects.filter(id=pk).first()
    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    recaudos = Recaudo.objects.filter(venta=venta.id)
    if recaudos:
        return Response({'message':'No se puede eliminar la venta por que ya se realizaron pagos a la misma.'},status=status.HTTP_406_NOT_ACCEPTABLE)
    else:
        venta.delete()
        tienda.caja_inicial = tienda.caja_inicial + venta.valor_venta
        tienda.save()
        return Response({'message':'Venta eliminada correctamente'},status=status.HTTP_200_OK)
    return Response({'message':'No se encontr?? la venta'}, status=status.HTTP_400_BAD_REQUEST)
