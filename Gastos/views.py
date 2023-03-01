from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import api_view

from .models import *
from .serializers import GastoSerializer, TipoGastoSerializer, GastoUpdateSerializer, GastoDetailSerializer




####### TIPO GASTOS ######
@api_view(['GET'])
def list_tipo_gastos(request):
    '''obtenemos todos los tipo_gastos'''
    tipo_gastos = Tipo_Gasto.objects.all()
    if tipo_gastos:
        tipo_gasto_serializer = TipoGastoSerializer(tipo_gastos, many=True)
        return Response(tipo_gasto_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado tipos de gastos'}, status=status.HTTP_200_OK)
    
    

@api_view(['GET'])
def get_tipo_gasto(request, pk):
    tipo_gasto = Tipo_Gasto.objects.filter(id=pk).first()
    if tipo_gasto:
        tipo_gasto_serializer = TipoGastoSerializer(tipo_gasto, many=False)
        return Response(tipo_gasto_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message':'No se encontro el tipo de gasto'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_tipo_gasto(request, pk):
    
    tipo_gasto = Tipo_Gasto.objects.filter(id=pk).first()
    if tipo_gasto:
        tipo_gasto_serializer = TipoGastoSerializer(tipo_gasto, data=request.data)
        if tipo_gasto_serializer.is_valid():
            tipo_gasto_serializer.save()
            return Response(tipo_gasto_serializer.data,status=status.HTTP_200_OK)
        return Response(tipo_gasto_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message':'No se encontró el tipo de gasto'}, status=status.HTTP_400_BAD_REQUEST)
        

@api_view(['POST'])
def post_tipo_gasto(request):
    '''creamos un tipo_gasto'''
    if request.method == 'POST':
        print('ingresa a post tipo gasto')
        print(request.data)
        tipo_gasto_serializer = TipoGastoSerializer(data = request.data)
        if tipo_gasto_serializer.is_valid():
            tipo_gasto_serializer.save()
            return Response(tipo_gasto_serializer.data, status=status.HTTP_200_OK)
        return Response(tipo_gasto_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['DELETE'])
def delete_tipo_gasto(request, pk):
    tipo_gasto = Tipo_Gasto.objects.filter(id=pk).first()
    if tipo_gasto:
        tipo_gasto.delete()
        return Response({'message':'tipo de gasto eliminado correctamente'},status=status.HTTP_200_OK)
    return Response({'message':'No se encontró el tipo de gasto'}, status=status.HTTP_400_BAD_REQUEST)



######## GASTOS ######

@api_view(['GET'])
def list_gastos(request):
    '''obtenemos todos los gastos'''
    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    gastos = Gasto.objects.filter(tienda=tienda.id).order_by('-id')
    if gastos:
        gasto_serializer = GastoDetailSerializer(gastos, many=True)
        return Response(gasto_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado gastos'}, status=status.HTTP_200_OK)
    
@api_view(['GET'])
def list_gastos_x_fecha(request, date):
    '''obtenemos todos los gastos x fecha'''
    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    gastos = Gasto.objects.filter(tienda=tienda.id).filter(fecha=date).order_by('-id')
    if gastos:
        gasto_serializer = GastoDetailSerializer(gastos, many=True)
        return Response(gasto_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado gastos'}, status=status.HTTP_200_OK)

@api_view(['GET'])
def get_gasto(request, pk):
    gasto = Gasto.objects.filter(id=pk).first()
    if gasto:
        gasto_serializer = GastoSerializer(gasto, many=False)
        return Response(gasto_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message':'No se encontro el gasto'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_gasto(request, pk):
    print('ingresa a editar gasto')
    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    gasto = Gasto.objects.filter(id=pk).first()
    gasto_valor = gasto.valor
    if gasto:
        print('encuentra el gasto')
        print(request.data)
        gasto_serializer = GastoUpdateSerializer(gasto, data=request.data)
        
        if gasto_serializer.is_valid():
            if gasto_valor < gasto_serializer.validated_data['valor']:
                tienda.caja_inicial = tienda.caja_inicial - (gasto_serializer.validated_data['valor']-gasto_valor)
            elif gasto_valor > gasto_serializer.validated_data['valor']:
                tienda.caja_inicial = tienda.caja_inicial + (gasto_valor - gasto_serializer.validated_data['valor'])
            gasto_serializer.save()
            tienda.save()
            return Response(gasto_serializer.data,status=status.HTTP_200_OK)
        return Response(gasto_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message':'No se encontró el gasto'}, status=status.HTTP_400_BAD_REQUEST)
        

@api_view(['POST'])
def post_gasto(request):
    '''creamos un gasto'''
    if request.method == 'POST':
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
        new_data = request.data
        new_data['tienda']=tienda.id
        new_data['trabajador']=request.user.perfil.id
        print(new_data)
        gasto_serializer = GastoSerializer(data = new_data)
        if gasto_serializer.is_valid():
            gasto_serializer.save()
            tienda.caja_inicial = tienda.caja_inicial - gasto_serializer.validated_data['valor']
            tienda.save()
            return Response(gasto_serializer.data, status=status.HTTP_200_OK)
        return Response(gasto_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['DELETE'])
def delete_gasto(request, pk):
    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    gasto = Gasto.objects.filter(id=pk).first()
    if gasto:
        gasto.delete()
        tienda.caja_inicial = tienda.caja_inicial + gasto.valor
        tienda.save()
        return Response({'message':'gasto eliminado correctamente'},status=status.HTTP_200_OK)
    return Response({'message':'No se encontró el gasto'}, status=status.HTTP_400_BAD_REQUEST)
