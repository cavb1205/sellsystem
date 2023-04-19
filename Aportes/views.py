from typing import OrderedDict
from urllib import request
from rest_framework import generics

from datetime import datetime, date

from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated


from .models import *
from .serializers import AporteSerializer, AporteUpdateSerializer, AporteDetailSerializer


@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def list_aportes(request, tienda_id=None):
    '''obtenemos todas los aportes'''

    user = request.user
    if tienda_id:
        aportes = Aporte.objects.filter(tienda=tienda_id).order_by('-id')
    else:
        aportes = Aporte.objects.filter(tienda=user.perfil.tienda).order_by('-id')
    if aportes:
        aporte_serializer = AporteDetailSerializer(aportes, many=True)
        return Response(aporte_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado aportes'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def list_aportes_fecha(request, date, tienda_id=None):
    '''obtenemos los aportes x fecha'''

    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    aportes = Aporte.objects.filter(tienda=tienda).filter(fecha=date)
    if aportes:
        aporte_serializer = AporteDetailSerializer(aportes, many=True)
        return Response(aporte_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontraron aportes'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_aporte(request, pk):
    aporte = Aporte.objects.filter(id=pk).first()
    if aporte:
        aporte_serializer = AporteSerializer(aporte, many=False)
        return Response(aporte_serializer.data)
    else:
        return Response({'message': 'No se encontro el aporte'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_aporte(request, pk, tienda_id=None):
    aporte_inicial = Aporte.objects.filter(id=pk).first()
    if tienda_id:
        tienda = Tienda.objects.get(id=tienda_id)    
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if aporte_inicial:
        aporte_serializer = AporteUpdateSerializer(
            aporte_inicial, data=request.data)
        if aporte_serializer.is_valid():
            new_aporte = aporte_serializer.validated_data['valor']
            if (aporte_inicial.valor <= new_aporte):
                tienda.caja_inicial = tienda.caja_inicial + \
                    (new_aporte - aporte_inicial.valor)
            elif (aporte_inicial.valor >= new_aporte):
                tienda.caja_inicial = tienda.caja_inicial - \
                    (aporte_inicial.valor-new_aporte)
            aporte_serializer.save()
            tienda.save()
            return Response(aporte_serializer.data, status=status.HTTP_200_OK)
        return Response(aporte_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message': 'No se encontró el aporte'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def post_aporte(request, tienda_id=None):
    '''creamos un aporte'''
    print('ingresa create aporte')
    if request.method == 'POST':
        if tienda_id:
            tienda = Tienda.objects.filter(id=tienda_id).first()
        else:
            tienda = Tienda.objects.filter(
                id=request.user.perfil.tienda.id).first()
        new_data = request.data
        new_data['tienda'] = tienda.id

        aporte_serializer = AporteSerializer(data=new_data)
        if aporte_serializer.is_valid():
            aporte_serializer.save()
            tienda.caja_inicial = tienda.caja_inicial + \
                aporte_serializer.validated_data['valor']
            tienda.save()
            return Response(aporte_serializer.data, status=status.HTTP_200_OK)
        return Response({'message': 'Por favor completar los campos del formulario.'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
def delete_aporte(request, pk, tienda_id=None):
    aporte = Aporte.objects.filter(id=pk).first()
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if aporte:
        aporte.delete()
        tienda.caja_inicial = tienda.caja_inicial - aporte.valor
        tienda.save()
        return Response({'message': 'Aporte eliminado correctamente'}, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontró el aporte'}, status=status.HTTP_400_BAD_REQUEST)
