from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from Utilidades.models import Utilidad
from Tiendas.models import Tienda
from Utilidades.serializers import UtilidadSerializer, UtilidadDetailSerializer, UtilidadUpdateSerializer


@api_view(['GET'])
def list_utilidades(request, tienda_id=None):
    '''obtenemos todas las utilidads'''
    user = request.user
    if tienda_id:
        utilidades = Utilidad.objects.filter(tienda=tienda_id)
    else:
        utilidades = Utilidad.objects.filter(tienda=user.perfil.tienda)
    if utilidades:
        utilidad_serializer = UtilidadDetailSerializer(utilidades, many=True)
        return Response(utilidad_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado utilidades'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def list_utilidades_x_fecha(request, date, tienda_id=None):
    '''obtenemos todas las utilidades por fecha'''
    user = request.user
    if tienda_id:
        utilidades = Utilidad.objects.filter(
            tienda=tienda_id).filter(fecha=date).order_by('-id')
    else:
        utilidades = Utilidad.objects.filter(
            tienda=user.perfil.tienda).filter(fecha=date).order_by('-id')
    if utilidades:
        utilidad_serializer = UtilidadDetailSerializer(utilidades, many=True)
        return Response(utilidad_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontraron utilidades'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_utilidad(request, pk):
    utilidad = Utilidad.objects.filter(id=pk).first()
    if utilidad:
        utilidad_serializer = UtilidadSerializer(utilidad, many=False)
        return Response(utilidad_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontro la utilidad'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_utilidad(request, pk, tienda_id=None):
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    utilidad = Utilidad.objects.filter(id=pk).first()
    utilidad_valor = utilidad.valor
    if utilidad:
        utilidad_serializer = UtilidadUpdateSerializer(
            utilidad, data=request.data)
        if utilidad_serializer.is_valid():
            if utilidad_valor < utilidad_serializer.validated_data['valor']:
                tienda.caja_inicial = tienda.caja_inicial - \
                    (utilidad_serializer.validated_data['valor']-utilidad_valor)

            elif utilidad_valor > utilidad_serializer.validated_data['valor']:
                tienda.caja_inicial = tienda.caja_inicial + \
                    (utilidad_valor -
                     utilidad_serializer.validated_data['valor'])
            utilidad_serializer.save()
            tienda.save()
            return Response(utilidad_serializer.data, status=status.HTTP_200_OK)
        return Response(utilidad_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message': 'No se encontró la utilidad'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def post_utilidad(request, tienda_id=None):
    '''creamos una utilidad'''

    if request.method == 'POST':
        if tienda_id:
            tienda = Tienda.objects.filter(
                id=tienda_id).first()
        else:
            tienda = Tienda.objects.filter(
                id=request.user.perfil.tienda.id).first()
        new_data = request.data
        new_data['tienda'] = tienda.id
        utilidad_serializer = UtilidadSerializer(data=new_data)
        if utilidad_serializer.is_valid():
            utilidad_serializer.save()
            tienda.caja_inicial = tienda.caja_inicial - \
                utilidad_serializer.validated_data['valor']
            tienda.save()
            return Response(utilidad_serializer.data, status=status.HTTP_200_OK)
        return Response(utilidad_serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
def delete_utilidad(request, pk, tienda_id=None):
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    utilidad = Utilidad.objects.filter(id=pk).first()
    if utilidad:
        tienda.caja_inicial = tienda.caja_inicial + utilidad.valor
        utilidad.delete()
        tienda.save()
        return Response({'message': 'Utilidad eliminada correctamente'}, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontró la utilidad'}, status=status.HTTP_400_BAD_REQUEST)
