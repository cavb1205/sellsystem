from django.contrib.auth import authenticate
import datetime

from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from Trabajadores.models import AsignacionTrabajador, Perfil

from Trabajadores.serializers import UserCreateSerializer, PerfilSerializer, UserSerializer, UserUpdateSerializer,PerfilCreateSerializer
from Trabajadores.serializers import UserTokenLoginObtainSerializer,UserLoginSerializer
from Tiendas.models import Tienda, Cierre_Caja, Tienda_Membresia, Membresia, Tienda_Administrador
from Tiendas.serializers import TiendaCreateSerializer
from Tiendas.views import comprobar_estado_membresia
from Tiendas import telegram_bot
from Tiendas.permissions import (
    requiere_acceso_tienda,
    usuario_puede_acceder_tienda,
    usuario_es_administrador_tienda,
    respuesta_sin_permiso,
)
from Tiendas.fecha_operativa import fecha_operativa
from Trabajadores.throttles import LoginRateThrottle, RegisterRateThrottle
##### LOGIN #####

class Login(TokenObtainPairView):
    serializer_class = UserTokenLoginObtainSerializer
    throttle_classes = [LoginRateThrottle]

    def post(self, request, *args, **kwargs):
        username = request.data.get('username','')
        password = request.data.get('password','')
        user = authenticate(
            username=username,
            password=password
        )
        if user:
            # Perfil ausente: error controlado en vez de 500 (Perfil.objects.get)
            perfil = Perfil.objects.filter(trabajador=user.id).first()
            if perfil is None:
                return Response(
                    {'error': 'Tu usuario no tiene un perfil de ruta asociado. Contacta al soporte.'},
                    status=status.HTTP_400_BAD_REQUEST)
            if not (user.is_staff or user.is_superuser):
                tiene_ruta_activa = AsignacionTrabajador.objects.filter(
                    perfil=perfil,
                    activa=True,
                    tienda__estado=True,
                    tienda__administrador_id=F('perfil__tienda__administrador_id'),
                ).exists()
                tiene_asignaciones = AsignacionTrabajador.objects.filter(
                    perfil=perfil,
                ).exists()
                if not tiene_ruta_activa and not tiene_asignaciones and perfil.tienda.estado:
                    tiene_ruta_activa = True
                if not tiene_ruta_activa:
                    return Response(
                        {'error': 'Tu usuario no tiene rutas activas asignadas. Contacta al administrador.'},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            login_serializer = self.serializer_class(data=request.data)
            if login_serializer.is_valid():
                user_serializer = UserLoginSerializer(user)
                perfil_serializer = PerfilSerializer(perfil)
                membresia = comprobar_estado_membresia(perfil.tienda.id)
                return Response({
                    'token': login_serializer.validated_data['access'],
                    'refresh': login_serializer.validated_data['refresh'],
                    'user': user_serializer.data,
                    'perfil':perfil_serializer.data,
                    'membresia': membresia,
                }, status=status.HTTP_200_OK)
            return Response({'error':'Usuario o contraseña incorrectos.'},status=status.HTTP_400_BAD_REQUEST)
        # authenticate() también rechaza cuentas desactivadas con la clave
        # correcta — avisar la causa real evita que el trabajador crea que
        # olvidó su contraseña y reintente en vano.
        existente = User.objects.filter(username=username).first()
        if existente and not existente.is_active and existente.check_password(password):
            return Response(
                {'error': 'Tu cuenta está desactivada. Contacta al administrador de tu ruta.'},
                status=status.HTTP_403_FORBIDDEN)
        return Response({'error':'Usuario o contraseña incorrectos.'},status=status.HTTP_400_BAD_REQUEST)

   



#### CRUD TRABAJADORES #####

def _es_admin(user):
    """Solo el admin de la ruta (is_staff) o root gestionan la nómina.
    Los cobradores pertenecen a la tienda pero NO pueden crear/editar/eliminar
    trabajadores ni cambiar contraseñas ajenas (fix escalación de privilegios)."""
    return bool(user and (user.is_staff or user.is_superuser))


@api_view(['GET'])
@requiere_acceso_tienda
def list_trabajadores(request, tienda_id = None):
    if not _es_admin(request.user):
        return respuesta_sin_permiso()
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()    
    else:    
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if not tienda:
        return Response({'error': 'Ruta no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    trabajadores = Perfil.objects.filter(
        Q(tienda_id=tienda.id, asignaciones_ruta__isnull=True)
        | Q(asignaciones_ruta__tienda_id=tienda.id, asignaciones_ruta__activa=True)
    ).distinct()
    if trabajadores.exists():
        trabajadores_serializer = PerfilSerializer(trabajadores, many = True)
        return Response(trabajadores_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado trabajadores'}, status=status.HTTP_200_OK)


def _rutas_perfil_ids(perfil):
    asignaciones = AsignacionTrabajador.objects.filter(perfil=perfil)
    if not asignaciones.exists():
        return [perfil.tienda_id]
    return list(asignaciones.filter(activa=True).values_list('tienda_id', flat=True))


def _admin_puede_gestionar_perfil(user, perfil):
    if not _es_admin(user):
        return False
    return any(
        usuario_es_administrador_tienda(user, tienda_id)
        for tienda_id in _rutas_perfil_ids(perfil)
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def mis_rutas(request):
    """Lista mínima de rutas activas asignadas al trabajador autenticado."""
    if request.user.is_staff or request.user.is_superuser:
        return respuesta_sin_permiso()
    perfil = Perfil.objects.select_related('tienda').filter(trabajador=request.user).first()
    if not perfil:
        return Response(
            {'error': 'Perfil de trabajador no encontrado.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    tienda_ids = _rutas_perfil_ids(perfil)
    tiendas = Tienda.objects.filter(
        id__in=tienda_ids,
        estado=True,
        administrador_id=perfil.tienda.administrador_id,
    ).order_by('nombre')
    return Response(
        [{'id': tienda.id, 'nombre': tienda.nombre} for tienda in tiendas],
        status=status.HTTP_200_OK,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@requiere_acceso_tienda
def candidatos_asignacion(request, tienda_id=None):
    """Devuelve trabajadores existentes del mismo propietario."""
    if not _es_admin(request.user):
        return respuesta_sin_permiso()
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'error': 'Ruta no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
    if not usuario_es_administrador_tienda(request.user, tienda.id):
        return respuesta_sin_permiso()
    if not tienda.estado:
        return Response(
            {'error': 'Solo puedes asignar trabajadores a una ruta activa.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    candidatos = Perfil.objects.filter(
        tienda__administrador_id=tienda.administrador_id,
        trabajador__is_staff=False,
        trabajador__is_superuser=False,
    ).exclude(
        asignaciones_ruta__tienda_id=tienda.id,
        asignaciones_ruta__activa=True,
    ).select_related('trabajador', 'tienda').distinct().order_by(
        'trabajador__first_name',
        'trabajador__username',
    )
    return Response(
        [{
            'id': perfil.id,
            'trabajador': perfil.trabajador.get_full_name() or perfil.trabajador.username,
            'username': perfil.trabajador.username,
            'identificacion': perfil.identificacion,
            'ruta_origen': perfil.tienda.nombre,
        } for perfil in candidatos],
        status=status.HTTP_200_OK,
    )


@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated])
@requiere_acceso_tienda
def asignar_trabajador_ruta(request, pk, tienda_id=None):
    """Asigna o retira acceso operativo sin duplicar la cuenta del usuario."""
    if not _es_admin(request.user):
        return respuesta_sin_permiso()
    tienda = Tienda.objects.filter(id=tienda_id).first()
    perfil = Perfil.objects.select_related('tienda', 'trabajador').filter(id=pk).first()
    if not tienda or not perfil:
        return Response(
            {'error': 'Trabajador o ruta no encontrados.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    if not usuario_es_administrador_tienda(request.user, tienda.id):
        return respuesta_sin_permiso()
    if request.method == 'POST' and not tienda.estado:
        return Response(
            {'error': 'Solo puedes asignar trabajadores a una ruta activa.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if perfil.tienda.administrador_id != tienda.administrador_id:
        return Response(
            {'error': 'Solo puedes asignar trabajadores de rutas del mismo administrador propietario.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    if perfil.trabajador.is_staff or perfil.trabajador.is_superuser:
        return Response(
            {'error': 'Solo se pueden asignar cuentas de trabajador.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        asignaciones = AsignacionTrabajador.objects.select_for_update().filter(
            perfil=perfil,
            tienda=tienda,
        )
        if request.method == 'DELETE':
            asignacion = asignaciones.filter(activa=True).first()
            if not asignacion:
                return Response(
                    {'error': 'El trabajador no está asignado a esta ruta.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            asignacion.activa = False
            asignacion.retirada_en = timezone.now()
            asignacion.retirada_por = request.user
            asignacion.save(update_fields=['activa', 'retirada_en', 'retirada_por'])
            return Response(
                {'message': 'Se retiró el acceso del trabajador a esta ruta.'},
                status=status.HTTP_200_OK,
            )

        asignacion, creada = AsignacionTrabajador.objects.select_for_update().get_or_create(
            perfil=perfil,
            tienda=tienda,
            defaults={'asignada_por': request.user},
        )
        if asignacion.activa and not creada:
            return Response(
                {'message': 'El trabajador ya está asignado a esta ruta.'},
                status=status.HTTP_200_OK,
            )
        asignacion.activa = True
        asignacion.asignada_por = request.user
        asignacion.retirada_en = None
        asignacion.retirada_por = None
        asignacion.save(update_fields=[
            'activa', 'asignada_por', 'retirada_en', 'retirada_por',
        ])

    return Response(
        {'message': 'Trabajador asignado a la ruta correctamente.'},
        status=status.HTTP_201_CREATED if creada else status.HTTP_200_OK,
    )


@api_view(['GET'])
def get_trabajador(request, pk):
    if not _es_admin(request.user):
        return respuesta_sin_permiso()
    trabajador = Perfil.objects.filter(id=pk).first()
    if trabajador:
        if not _admin_puede_gestionar_perfil(request.user, trabajador):
            return respuesta_sin_permiso()
        user_id = trabajador.trabajador.id
        user = User.objects.filter(id=user_id).first()
        trabajador_serializer = PerfilSerializer(trabajador, many=False)
        user_serializer = UserSerializer(user, many=False)
        
        return Response(
            {
                'id':trabajador_serializer.data['id'],
                'username': user_serializer.data['username'],
                'identificacion': trabajador_serializer.data['identificacion'],
                'first_name': user_serializer.data['first_name'],
                'last_name': user_serializer.data['last_name'],
                'email':user_serializer.data['email'],
                'telefono': trabajador_serializer.data['telefono'],
                'direccion': trabajador_serializer.data['direccion'],
                'is_active':user_serializer.data['is_active'],
                'is_staff': user_serializer.data['is_staff'],
                'last_login':user_serializer.data['last_login'],
                'date_joined':user_serializer.data['date_joined'],
                'tienda': trabajador_serializer.data['tienda'],
        })
    else:
        return Response({'message':'No se encontró el trabajador'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_trabajador(request, pk):
    if not _es_admin(request.user):
        return respuesta_sin_permiso()
    trabajador = Perfil.objects.filter(id=pk).first()
    if trabajador:
        if not _admin_puede_gestionar_perfil(request.user, trabajador):
            return respuesta_sin_permiso()
        user = User.objects.filter(id=trabajador.trabajador.id).first()
        user_data = {
            "username":request.data['username'],
            "first_name":request.data['first_name'],
            "last_name":request.data['last_name'],
            "is_active":request.data['is_active'],
            "is_staff": request.data['is_staff'],
        }
        trabajador_data = {
        'trabajador':user.id,
        'identificacion':request.data['identificacion'],
        'telefono':request.data['telefono'],
        'direccion':request.data['direccion'],
        # La ruta principal no se modifica desde la edición del perfil.
        # Las rutas adicionales se gestionan con la operación de asignación.
        'tienda':trabajador.tienda_id
        }
        user_serializer = UserUpdateSerializer(user, data=user_data)
        trabajador_serializer = PerfilSerializer(trabajador, data=trabajador_data)
        
        if trabajador_serializer.is_valid() and user_serializer.is_valid():
            user_serializer.save()
            trabajador_serializer.save()
            return Response(trabajador_serializer.data,status=status.HTTP_200_OK)
        return Response(trabajador_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message':'Trabajador no existe'}, status=status.HTTP_400_BAD_REQUEST)
        

@api_view(['POST'])
@requiere_acceso_tienda
def post_trabajador(request, tienda_id = None):
    if not _es_admin(request.user):
        return respuesta_sin_permiso()
    if len(request.data.get('password') or '') < 8:
        return Response({'error': 'La contraseña debe tener al menos 8 caracteres.'},
                        status=status.HTTP_400_BAD_REQUEST)
    user_data = {
        "username":request.data['username'],
        "first_name":request.data['first_name'],
        "last_name":request.data['last_name'],
        "password":request.data['password']
    }
    
    user_serializer = UserCreateSerializer(data = user_data)
    
    if user_serializer.is_valid():
        user = user_serializer.save()
        user.set_password(request.data['password'])
        user.save()

        if tienda_id:
            trabajador_data = {
            'trabajador':user.id,
            'identificacion':request.data['identificacion'],
            'telefono':request.data['telefono'],
            'direccion':request.data['direccion'],
            'tienda': tienda_id
        }
        else:
            trabajador_data = {
            'trabajador':user.id,
            'identificacion':request.data['identificacion'],
            'telefono':request.data['telefono'],
            'direccion':request.data['direccion'],
            'tienda': request.user.perfil.tienda.id
        }
        
        trabajador_serializer = PerfilSerializer(data = trabajador_data)
        if trabajador_serializer.is_valid():
            perfil = trabajador_serializer.save()
            AsignacionTrabajador.objects.get_or_create(
                perfil=perfil,
                tienda_id=perfil.tienda_id,
                defaults={'asignada_por': request.user},
            )
            return Response(trabajador_serializer.data,status=status.HTTP_200_OK)
        else:
            Response(trabajador_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response(user_serializer.errors,status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([RegisterRateThrottle])
def register_user(request):
    username = request.data.get('username', '')
    # Última vía sin validar: el front exige 8, pero la API directa aceptaba
    # cualquier largo (mismo criterio que update_password/post_trabajador).
    if len(request.data.get('password') or '') < 8:
        return Response({'error': 'La contraseña debe tener al menos 8 caracteres.'},
                        status=status.HTTP_400_BAD_REQUEST)
    user_data = {
        "username": username,
        "first_name": request.data.get('first_name', ''),
        "last_name": request.data.get('last_name', ''),
        "password": request.data.get('password', '')
    }

    serializer = UserCreateSerializer(data=user_data)
    if serializer.is_valid():
        user = serializer.save()
        user.set_password(request.data['password'])
        user.email = f"{username}@carterafinanciera.com"
        user.is_staff = True
        user.is_superuser = False
        user.save()

        tienda_data = {
            "nombre": request.data.get('nombre_ruta', ''),
            "administrador": user.id,
        }
        serializer_tienda = TiendaCreateSerializer(data=tienda_data)
        if serializer_tienda.is_valid():
            tienda = serializer_tienda.save()
            hoy = fecha_operativa(tienda)
            Cierre_Caja.objects.create(tienda=tienda, valor=tienda.caja_inicial, fecha_cierre=(hoy - datetime.timedelta(days=1)))
            Tienda_Membresia.objects.create(
                tienda=tienda,
                membresia=Membresia.objects.get(nombre='Prueba'),
                fecha_activacion=hoy,
                fecha_vencimiento=(hoy + datetime.timedelta(days=7)),
                estado='Activa'
            )
            Tienda_Administrador.objects.create(tienda=tienda, administrador=user)

            perfil_data = {
                'trabajador': user.id,
                'tienda': tienda.id
            }
            serializer_perfil = PerfilCreateSerializer(data=perfil_data)
            if serializer_perfil.is_valid():
                perfil = serializer_perfil.save()
                perfil.telefono = request.data.get('telefono', '')
                perfil.save()

                # Aviso al admin: nuevo registro en la app (no rompe el flujo)
                telegram_bot.notificar_nuevo_usuario(user, tienda, perfil.telefono)

                # Auto-login: generate JWT and return same structure as Login view
                refresh = RefreshToken.for_user(user)
                perfil_serializer = PerfilSerializer(perfil)
                user_serializer = UserLoginSerializer(user)
                membresia = comprobar_estado_membresia(tienda.id)

                return Response({
                    'token': str(refresh.access_token),
                    'refresh': str(refresh),
                    'user': user_serializer.data,
                    'perfil': perfil_serializer.data,
                    'membresia': membresia,
                }, status=status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



@api_view(['DELETE'])
def delete_trabajador(request, pk):
    if not _es_admin(request.user):
        return respuesta_sin_permiso()
    trabajador = Perfil.objects.filter(id=pk).first()
    if trabajador:
        if not _admin_puede_gestionar_perfil(request.user, trabajador):
            return respuesta_sin_permiso()
        rutas_activas = AsignacionTrabajador.objects.filter(
            perfil=trabajador,
            activa=True,
        ).count()
        if rutas_activas > 1:
            return Response(
                {'error': 'Este trabajador tiene varias rutas asignadas. Retira primero cada asignación; así no se eliminará su cuenta en otras rutas.'},
                status=status.HTTP_409_CONFLICT,
            )
        user = User.objects.filter(id=trabajador.trabajador.id).first()
        # Blindaje anti-cascada: Tienda.administrador es FK CASCADE — borrar al
        # usuario que administra una ruta destruiría la tienda completa con sus
        # clientes, ventas y recaudos. Nunca se permite por esta vía.
        if user and Tienda.objects.filter(administrador=user).exists():
            return Response(
                {'error': 'Este usuario es administrador de una ruta y no se puede eliminar. '
                          'Si necesitas bloquear su acceso, desactívalo desde Editar.'},
                status=status.HTTP_400_BAD_REQUEST)
        trabajador.delete()
        user.delete()
        return Response({'message':'Trabajador eliminado correctamente'},status=status.HTTP_200_OK)
    return Response({'message':'No se encontró el trabajador'}, status=status.HTTP_400_BAD_REQUEST)



@api_view(['POST'])
def update_password(request, pk):
    trabajador = Perfil.objects.filter(id=pk).first()
    if trabajador:
        # Un trabajador solo puede cambiar SU propia contraseña (perfil);
        # cambiar la de otros (incluido el admin) requiere rol admin.
        es_propia = trabajador.trabajador_id == request.user.id
        if not es_propia and not _es_admin(request.user):
            return respuesta_sin_permiso()
        if not any(
            usuario_puede_acceder_tienda(request.user, tienda_id)
            for tienda_id in _rutas_perfil_ids(trabajador)
        ):
            return respuesta_sin_permiso()
        password = request.data.get('passwordNuevo') or ''
        if len(password) < 8:
            return Response({'error': 'La contraseña debe tener al menos 8 caracteres.'},
                            status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.filter(id=trabajador.trabajador.id).first()
        user.set_password(password)
        user.save()
        return Response({'message':'Contraseña cambiada con éxito'}, status=status.HTTP_200_OK)
    return Response({'message':'No se encontro el trabajador'}, status=status.HTTP_400_BAD_REQUEST)
