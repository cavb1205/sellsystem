from itertools import count
from Ventas.models import Venta
from rest_framework.decorators import api_view
from rest_framework import generics
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import LimitOffsetPagination

from Recaudos.models import Recaudo, Visita_Blanco
from Recaudos.serializers import RecaudoSerializer, Visitas_BlancoSerializer, RecaudoDetailSerializer,RecaudoUpdateSerializer
from Tiendas.models import Tienda
from Ventas.models import Venta

@api_view(['GET'])
def list_recaudos(request):
    '''obtenemos todas las recaudos'''
    user = request.user
    tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    recaudos = Recaudo.objects.filter(tienda=tienda.id)
    if recaudos:
        recaudo_serializer = RecaudoDetailSerializer(recaudos, many=True)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado recaudos'}, status=status.HTTP_200_OK)

@api_view(['GET'])
def list_recaudos_fecha(request, date):
    '''obtenemos todas las recaudos'''

    user = request.user
    tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    recaudos = Recaudo.objects.filter(tienda=tienda.id).filter(fecha_recaudo=date)
    if recaudos:
        recaudo_serializer = RecaudoDetailSerializer(recaudos, many=True)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado recaudos'}, status=status.HTTP_200_OK)
    
@api_view(['GET'])
def list_recaudos_venta(request, venta_id):
    '''obtenemos los recaudos pertenecientes a una venta en particular'''

    recaudos = Recaudo.objects.filter(venta=venta_id).order_by('-id')
    
    if recaudos:
        recaudo_serializer = RecaudoDetailSerializer(recaudos, many=True)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado recaudos'}, status=status.HTTP_200_OK)

    

@api_view(['GET'])
def get_recaudo(request, pk):
    recaudo = Recaudo.objects.filter(id=pk).first()
    if recaudo:
        recaudo_serializer = RecaudoDetailSerializer(recaudo, many=False)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message':'No se encontro el recaudo'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_recaudo(request, pk):
    recaudo = Recaudo.objects.filter(id=pk).first()
    tienda = Tienda.objects.get(id=recaudo.tienda.id)
    venta = Venta.objects.get(id=recaudo.venta.id)
    if recaudo:
        recaudo_serializer = RecaudoUpdateSerializer(recaudo, data=request.data)
        if recaudo_serializer.is_valid():
            if recaudo_serializer.validated_data['valor_recaudo'] != recaudo.valor_recaudo:
                venta.saldo_actual = venta.saldo_actual + recaudo.valor_recaudo
                venta.saldo_actual = venta.saldo_actual - recaudo_serializer.validated_data['valor_recaudo']
                tienda.caja_inicial = tienda.caja_inicial - recaudo.valor_recaudo
                tienda.caja_inicial = tienda.caja_inicial + recaudo_serializer.validated_data['valor_recaudo']
                recaudo_serializer.save()
                recaudos = Recaudo.objects.filter(venta=venta.id)
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
            else:
                recaudo_serializer.save()
            return Response(recaudo_serializer.data,status=status.HTTP_200_OK)
        return Response(recaudo_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message':'No se encontró el recaudo'}, status=status.HTTP_400_BAD_REQUEST)
        

@api_view(['POST'])
def post_recaudo(request):
    '''creamos una recaudo'''
    user = request.user
    tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    new_data = request.data
    new_data['tienda']=tienda.id
    
    venta = Venta.objects.get(id = new_data['venta'])
    
    if request.method == 'POST':
        recaudo_serializer = RecaudoSerializer(data = new_data)
        if recaudo_serializer.is_valid():
            recaudo_serializer.save()
            tienda.caja_inicial = tienda.caja_inicial + recaudo_serializer.validated_data['valor_recaudo']
            venta.saldo_actual = venta.saldo_actual - recaudo_serializer.validated_data['valor_recaudo']
            recaudos = Recaudo.objects.filter(venta=venta.id)
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
            return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
        return Response(recaudo_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['POST'])
def post_recaudo_no_pay(request):
    '''creamos una recaudo'''
    user = request.user
    tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    new_data = request.data
    visita_blanco = new_data['visita_blanco']
    
    new_data['tienda']=tienda.id
    venta = Venta.objects.get(id = new_data['venta'])
    
    if request.method == 'POST':
        visita_blanco_serializer = Visitas_BlancoSerializer(data=visita_blanco)
        if visita_blanco_serializer.is_valid():
            visita_blanco = visita_blanco_serializer.save()
            new_data['visita_blanco']=visita_blanco.id
            
            recaudo_serializer = RecaudoSerializer(data = new_data)
            
            if recaudo_serializer.is_valid():
                
                recaudo_serializer.save()
                tienda.caja_inicial = tienda.caja_inicial + recaudo_serializer.validated_data['valor_recaudo']
                venta.saldo_actual = venta.saldo_actual - recaudo_serializer.validated_data['valor_recaudo']
                recaudos = Recaudo.objects.filter(venta=venta.id)
                
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
                return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
        return Response(recaudo_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['DELETE'])
def delete_recaudo(request, pk):
    recaudo = Recaudo.objects.filter(id=pk).first()
    tienda = Tienda.objects.get(id=recaudo.tienda.id)
    venta = Venta.objects.get(id=recaudo.venta.id)
    if recaudo:
        
        tienda.caja_inicial = tienda.caja_inicial - recaudo.valor_recaudo
        venta.saldo_actual = venta.saldo_actual + recaudo.valor_recaudo
        recaudos = Recaudo.objects.filter(venta=venta.id)
        
        if venta.promedio_pago() >= venta.valor_cuota():
            venta.estado_venta = 'Vigente'
        elif venta.promedio_pago() < venta.valor_cuota():
            venta.estado_venta = 'Atrasado'
        elif venta.cuotas < recaudos.count():
            venta.estado_venta = 'Vencido'
        elif venta.saldo_actual <= 0:
            venta.estado_venta = 'Pagado'
        recaudo.delete()
        tienda.save()
        venta.save()
        return Response({'message':'Recaudo eliminado correctamente'},status=status.HTTP_200_OK)
    return Response({'message':'No se encontró el recaudo'}, status=status.HTTP_400_BAD_REQUEST)

