from pyexpat import model
from rest_framework.serializers import ModelSerializer
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from django.contrib.auth.models import User
from Trabajadores.models import Perfil
from Trabajadores.sesiones import perfil_login_permitido
from Trabajadores.password_policy import errores_password


class UserTokenLoginObtainSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        self.perfil = perfil_login_permitido(self.user)
        return data

class UserLoginSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'email',
                  'is_active', 'is_staff', 'is_superuser', 'last_login', 'date_joined']
        read_only_fields = fields

class UserSerializer(UserLoginSerializer):
    pass


class UserCreateSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = ['username','first_name','last_name','password']
        extra_kwargs = {'password': {'write_only': True}}

    def validate(self, attrs):
        user=User(**{key:value for key,value in attrs.items() if key!='password'})
        errors=errores_password(attrs['password'],user)
        if errors:raise serializers.ValidationError({'password':errors})
        return attrs

    def create(self, validated_data):
        # Hash antes del INSERT: nunca guardar la contraseña en texto claro,
        # ni siquiera durante la creación del perfil y su ruta.
        return User.objects.create_user(**validated_data)


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
