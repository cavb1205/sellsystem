from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from django.db import transaction

from Utilidades.models import Utilidad
from Tiendas.models import Tienda
from Utilidades.serializers import UtilidadSerializer, UtilidadDetailSerializer, UtilidadUpdateSerializer
from Tiendas.permissions import requiere_acceso_tienda, usuario_puede_acceder_tienda, respuesta_sin_permiso
from Tiendas.caja import registrar_movimiento_caja


@api_view(['GET'])
@requiere_acceso_tienda
def list_utilidades(request, tienda_id=None):
    '''obtenemos todas las utilidades'''
    user = request.user
    if tienda_id:
        utilidades = Utilidad.objects.filter(tienda=tienda_id).order_by('-id')
    else:
        utilidades = Utilidad.objects.filter(tienda=user.perfil.tienda).order_by('-id')
    if utilidades:
        utilidad_serializer = UtilidadDetailSerializer(utilidades, many=True)
        return Response(utilidad_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado utilidades'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
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
        if not usuario_puede_acceder_tienda(request.user, utilidad.tienda_id):
            return respuesta_sin_permiso()
        utilidad_serializer = UtilidadSerializer(utilidad, many=False)
        return Response(utilidad_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontro la utilidad'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_utilidad(request, pk, tienda_id=None):
    utilidad = Utilidad.objects.filter(id=pk).first()
    if not utilidad:
        return Response({'message': 'No se encontró la utilidad'}, status=status.HTTP_400_BAD_REQUEST)
    if not usuario_puede_acceder_tienda(request.user, utilidad.tienda_id):
        return respuesta_sin_permiso()
    utilidad_valor = utilidad.valor
    if utilidad:
        utilidad_serializer = UtilidadUpdateSerializer(
            utilidad, data=request.data)
        if utilidad_serializer.is_valid():
            with transaction.atomic():
                tienda = Tienda.objects.select_for_update().get(pk=utilidad.tienda_id)
                utilidad = Utilidad.objects.select_for_update().get(pk=pk)
                utilidad_serializer = UtilidadUpdateSerializer(
                    utilidad, data=request.data)
                utilidad_serializer.is_valid(raise_exception=True)
                nuevo_valor = utilidad_serializer.validated_data['valor']
                diferencia = nuevo_valor - utilidad.valor
                utilidad_serializer.save()
                if diferencia:
                    # Una utilidad retirada reduce caja; una corrección hacia
                    # abajo devuelve la diferencia a la caja.
                    registrar_movimiento_caja(
                        tienda,
                        -diferencia,
                        tipo='UTILIDAD',
                        accion='CORRECCION',
                        usuario=request.user,
                        origen=utilidad,
                        detalle='Corrección del valor de la utilidad',
                    )
            return Response(utilidad_serializer.data, status=status.HTTP_200_OK)
        return Response(utilidad_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message': 'No se encontró la utilidad'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@requiere_acceso_tienda
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
            with transaction.atomic():
                tienda = Tienda.objects.select_for_update().get(pk=tienda.pk)
                utilidad = utilidad_serializer.save()
                registrar_movimiento_caja(
                    tienda,
                    -utilidad.valor,
                    tipo='UTILIDAD',
                    usuario=request.user,
                    origen=utilidad,
                    detalle=utilidad.comentario or '',
                )
            return Response(utilidad_serializer.data, status=status.HTTP_200_OK)
        return Response(utilidad_serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
def delete_utilidad(request, pk, tienda_id=None):
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    utilidad = Utilidad.objects.filter(id=pk).first()
    if utilidad and not usuario_puede_acceder_tienda(request.user, utilidad.tienda_id):
        return respuesta_sin_permiso()
    if utilidad:
        with transaction.atomic():
            tienda = Tienda.objects.select_for_update().get(pk=utilidad.tienda_id)
            utilidad = Utilidad.objects.select_for_update().get(pk=pk)
            valor = utilidad.valor
            origen_id = utilidad.pk
            origen_tipo = utilidad._meta.label_lower
            utilidad.delete()
            registrar_movimiento_caja(
                tienda,
                valor,
                tipo='UTILIDAD',
                accion='REVERSA',
                usuario=request.user,
                origen_tipo=origen_tipo,
                origen_id=origen_id,
                detalle='Reversa por eliminación de la utilidad',
            )
        return Response({'message': 'Utilidad eliminada correctamente'}, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontró la utilidad'}, status=status.HTTP_400_BAD_REQUEST)
