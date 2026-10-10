from datetime import date as calendar_date
from django.db import transaction
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from Tiendas.permission_suscripcion import IsAuthenticatedOperativo as IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from Publicidad.models import Publicidad
from Publicidad.serializers import PublicidadSerializer, PublicidadCreateSerializer
from Tiendas.models import Tienda
from Tiendas.permissions import (requiere_acceso_tienda, usuario_puede_acceder_tienda,
    usuario_es_administrador_tienda, respuesta_sin_permiso)
from Tiendas.fecha_operativa import fecha_operativa
from Trabajadores.models import Perfil


def _contexto(request, tienda):
    return {'request': request, 'admin_ruta': usuario_es_administrador_tienda(request.user, tienda.pk)}


def _registros(tienda):
    return Publicidad.objects.filter(tienda=tienda).select_related(
        'tienda', 'registrado_por', 'trabajador__trabajador').order_by('-hora', '-id')


def _propios(queryset, user):
    return queryset.filter(Q(registrado_por=user) | Q(registrado_por__isnull=True, trabajador__trabajador=user))


def _fecha(value):
    parsed = calendar_date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('Formato de fecha inválido')
    return parsed


def _lista(request, tienda, inicio, fin, propios=False):
    if inicio > fin or (fin - inicio).days > 92 or fin > fecha_operativa(tienda):
        return Response({'error': 'Selecciona hasta 93 días, sin fechas futuras y con inicio anterior al fin.'}, status=400)
    queryset = _registros(tienda).filter(fecha__range=[inicio, fin])
    if propios or request.query_params.get('propios') == '1':
        queryset = _propios(queryset, request.user)
    registros = list(queryset[:5001])
    if len(registros) > 5000:
        return Response({'error': 'Hay más de 5.000 puntos. Reduce el rango de fechas para verlos completos.'}, status=400)
    return Response(PublicidadSerializer(registros, many=True, context=_contexto(request, tienda)).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@requiere_acceso_tienda
def list_publicidad_fecha(request, date, tienda_id):
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    try:
        fecha = _fecha(date)
    except ValueError:
        return Response({'error': 'Fecha inválida.'}, status=400)
    return _lista(request, tienda, fecha, fecha)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@requiere_acceso_tienda
def list_publicidad_rango(request, inicio, fin, tienda_id):
    tienda = Tienda.objects.filter(pk=tienda_id).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=404)
    try:
        desde, hasta = _fecha(inicio), _fecha(fin)
    except ValueError:
        return Response({'error': 'Fecha inválida.'}, status=400)
    return _lista(request, tienda, desde, hasta)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@requiere_acceso_tienda
def list_publicidad_fecha_worker(request, date, tienda_id):
    """Solo los puntos del trabajador autenticado."""
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    try:
        fecha = _fecha(date)
    except ValueError:
        return Response({'error': 'Fecha inválida.'}, status=400)
    return _lista(request, tienda, fecha, fecha, propios=True)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@requiere_acceso_tienda
def create_publicidad(request, tienda_id):
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    serializer = PublicidadCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    datos = dict(serializer.validated_data)
    solicitud_id = datos.pop('solicitud_id', None)
    contexto = _contexto(request, tienda)
    perfil = Perfil.objects.filter(trabajador=request.user).first()
    if not contexto['admin_ruta'] and not perfil:
        return Response({'error': 'Tu usuario no tiene perfil de trabajador.'}, status=403)
    defaults = {**datos, 'fecha': fecha_operativa(tienda), 'trabajador': perfil,
        'registrado_por': request.user,
        'autor_nombre': (request.user.get_full_name().strip() or request.user.username)[:180],
        'autor_rol': 'Administrador' if contexto['admin_ruta'] else 'Trabajador'}
    with transaction.atomic():
        if solicitud_id:
            punto, creado = Publicidad.objects.get_or_create(tienda=tienda, solicitud_id=solicitud_id, defaults=defaults)
            if not creado and (punto.registrado_por_id != request.user.pk or any(
                    getattr(punto, campo) != datos.get(campo)
                    for campo in ('latitud', 'longitud', 'precision_gps', 'nota'))):
                return Response({'error': 'Este identificador ya pertenece a otra entrega. Abre un registro nuevo.'}, status=409)
        else:
            punto = Publicidad.objects.create(tienda=tienda, **defaults)
            creado = True
    return Response(PublicidadSerializer(punto, context=contexto).data, status=201 if creado else 200)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_publicidad(request, pk):
    publicidad = Publicidad.objects.select_related('tienda', 'trabajador__trabajador').filter(id=pk).first()
    if not publicidad:
        return Response({'message': 'Registro no encontrado'}, status=status.HTTP_404_NOT_FOUND)

    if not usuario_puede_acceder_tienda(request.user, publicidad.tienda_id):
        return respuesta_sin_permiso()

    is_admin = usuario_es_administrador_tienda(request.user, publicidad.tienda_id)
    autor_id = publicidad.registrado_por_id or (publicidad.trabajador.trabajador_id if publicidad.trabajador_id else None)
    is_owner = autor_id == request.user.pk
    is_today = publicidad.fecha == fecha_operativa(publicidad.tienda)

    if not is_admin and not (is_owner and is_today):
        return Response(
            {'message': 'Solo puedes borrar tus propios puntos registrados hoy.'},
            status=status.HTTP_403_FORBIDDEN
        )

    publicidad.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
