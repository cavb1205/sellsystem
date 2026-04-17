from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

from Publicidad.models import Publicidad
from Publicidad.serializers import PublicidadSerializer, PublicidadCreateSerializer
from Tiendas.models import Tienda
from Trabajadores.models import Perfil


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_publicidad_fecha(request, date, tienda_id):
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    registros = Publicidad.objects.filter(tienda=tienda, fecha=date)
    serializer = PublicidadSerializer(registros, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_publicidad_fecha_worker(request, date, tienda_id):
    """Solo los puntos del trabajador autenticado."""
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    perfil = request.user.perfil
    registros = Publicidad.objects.filter(tienda=tienda, fecha=date, trabajador=perfil)
    serializer = PublicidadSerializer(registros, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_publicidad(request, tienda_id):
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    perfil = request.user.perfil
    serializer = PublicidadCreateSerializer(data=request.data)
    if serializer.is_valid():
        publicidad = serializer.save(trabajador=perfil, tienda=tienda)
        return Response(PublicidadSerializer(publicidad).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_publicidad(request, pk):
    publicidad = Publicidad.objects.filter(id=pk).first()
    if not publicidad:
        return Response({'message': 'Registro no encontrado'}, status=status.HTTP_404_NOT_FOUND)

    is_admin = request.user.is_staff or request.user.is_superuser
    is_owner = publicidad.trabajador == request.user.perfil
    is_today = publicidad.fecha == timezone.localdate()

    if not is_admin and not (is_owner and is_today):
        return Response(
            {'message': 'Solo puedes borrar puntos marcados hoy'},
            status=status.HTTP_403_FORBIDDEN
        )

    publicidad.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
