from pyexpat import model
from rest_framework.serializers import ModelSerializer
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from django.contrib.auth.models import User
from Trabajadores.models import Perfil


class UserTokenLoginObtainSerializer(TokenObtainPairSerializer):
    pass

class UserLoginSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = '__all__'

class UserSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = '__all__'


class UserCreateSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = ['username','first_name','last_name','password']


class UserUpdateSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = ['username','first_name','last_name','is_active','is_staff']


class UserUpdatePasswordSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = ['password',]



class PerfilSerializer(ModelSerializer):
    class Meta:
        model = Perfil
        fields = '__all__'


    def to_representation(self, instance):
        return {
            'id':instance.id,
            'trabajador':instance.trabajador.get_full_name(),
            'identificacion':instance.identificacion,
            'telefono':instance.telefono,
            'direccion':instance.direccion,
            'tienda':instance.tienda.id
        }


class PerfilCreateSerializer(ModelSerializer):
    class Meta:
        model = Perfil
        fields = ['tienda','trabajador']