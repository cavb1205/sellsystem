from django.contrib.auth import authenticate
import datetime

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from rest_framework_simplejwt.views import TokenObtainPairView

from django.contrib.auth.models import User
from Trabajadores.models import Perfil

from Trabajadores.serializers import UserCreateSerializer, PerfilSerializer, UserSerializer, UserUpdateSerializer,PerfilCreateSerializer
from Trabajadores.serializers import UserTokenLoginObtainSerializer,UserLoginSerializer
from Tiendas.models import Tienda, Cierre_Caja, Tienda_Membresia, Membresia, Tienda_Administrador
from Tiendas.serializers import TiendaCreateSerializer
from Tiendas.views import comprobar_estado_membresia
##### LOGIN #####

class Login(TokenObtainPairView):
    serializer_class = UserTokenLoginObtainSerializer

    def post(self, request, *args, **kwargs):
        username = request.data.get('username','')
        password = request.data.get('password','')
        user = authenticate(
            username=username,
            password=password
        )
        if user:
            perfil = Perfil.objects.get(trabajador=user.id)
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
        return Response({'error':'Usuario o contraseña incorrectos.'},status=status.HTTP_400_BAD_REQUEST)

   



#### CRUD TRABAJADORES #####

@api_view(['GET'])
def list_trabajadores(request, tienda_id = None):
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
    trabajador = Perfil.objects.filter(id=pk).first()
    if trabajador:
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
    
    trabajador = Perfil.objects.filter(id=pk).first()
    if trabajador:
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
def post_trabajador(request, tienda_id = None):
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
def register_user(request):
    user_data = {
        "username":request.data['username'],
        "first_name":request.data['first_name'],
        "last_name":request.data['last_name'],
        "email":request.data['email'],
        "password":request.data['password']
    }
    
    serializer = UserCreateSerializer(data=user_data)
    if serializer.is_valid():
        user = serializer.save()
        user.set_password(request.data['password'])
        user.is_staff = True
        user.is_superuser = True
        user.save()

        tienda_data = {
        "nombre":request.data['nombre_ruta'],
        "administrador":user.id,
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
                'trabajador':user.id,
                'tienda':tienda.id
            }
            serializer_perfil = PerfilCreateSerializer(data=perfil_data)
            if serializer_perfil.is_valid():
                serializer_perfil.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



@api_view(['DELETE'])
def delete_trabajador(request, pk):
    trabajador = Perfil.objects.filter(id=pk).first()
    if trabajador:
        user = User.objects.filter(id=trabajador.trabajador.id).first()
        trabajador.delete()
        user.delete()
        return Response({'message':'Trabajador eliminado correctamente'},status=status.HTTP_200_OK)
    return Response({'message':'No se encontró el trabajador'}, status=status.HTTP_400_BAD_REQUEST)



@api_view(['POST'])
def update_password(request, pk):
    trabajador = Perfil.objects.filter(id=pk).first()
    if trabajador:
        user = User.objects.filter(id=trabajador.trabajador.id).first()
        user.set_password(request.data['passwordNuevo'])
        user.save()
        return Response({'message':'Contraseña cambiada con éxito'}, status=status.HTTP_200_OK)
    return Response({'message':'No se encontro el trabajador'}, status=status.HTTP_400_BAD_REQUEST)