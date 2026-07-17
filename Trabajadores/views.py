from django.contrib.auth import authenticate
import datetime

from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken

from django.contrib.auth.models import User
from Trabajadores.models import Perfil

from Trabajadores.serializers import UserCreateSerializer, PerfilSerializer, UserSerializer, UserUpdateSerializer,PerfilCreateSerializer
from Trabajadores.serializers import UserTokenLoginObtainSerializer,UserLoginSerializer
from Tiendas.models import Tienda, Cierre_Caja, Tienda_Membresia, Membresia, Tienda_Administrador
from Tiendas.serializers import TiendaCreateSerializer
from Tiendas.views import comprobar_estado_membresia
from Tiendas import telegram_bot
from Tiendas.permissions import requiere_acceso_tienda, usuario_puede_acceder_tienda, respuesta_sin_permiso
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
    trabajadores = Perfil.objects.filter(tienda=tienda.id)
    if trabajadores:
        trabajadores_serializer = PerfilSerializer(trabajadores, many = True)
        return Response(trabajadores_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado trabajadores'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_trabajador(request, pk):
    if not _es_admin(request.user):
        return respuesta_sin_permiso()
    trabajador = Perfil.objects.filter(id=pk).first()
    if trabajador:
        if not usuario_puede_acceder_tienda(request.user, trabajador.tienda_id):
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
        if not usuario_puede_acceder_tienda(request.user, trabajador.tienda_id):
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
        'tienda':request.data['tienda']
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
            trabajador_serializer.save()
            return Response(trabajador_serializer.data,status=status.HTTP_200_OK)
        else:
            Response(trabajador_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response(user_serializer.errors,status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([RegisterRateThrottle])
def register_user(request):
    username = request.data.get('username', '')
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
            Cierre_Caja.objects.create(tienda=tienda, valor=tienda.caja_inicial, fecha_cierre=(datetime.date.today() - datetime.timedelta(days=1)))
            Tienda_Membresia.objects.create(
                tienda=tienda,
                membresia=Membresia.objects.get(nombre='Prueba'),
                fecha_activacion=datetime.date.today(),
                fecha_vencimiento=(datetime.date.today() + datetime.timedelta(days=7)),
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
        if not usuario_puede_acceder_tienda(request.user, trabajador.tienda_id):
            return respuesta_sin_permiso()
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
        if not usuario_puede_acceder_tienda(request.user, trabajador.tienda_id):
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