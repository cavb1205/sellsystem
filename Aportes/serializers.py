
from .models import *
from rest_framework.serializers import ModelSerializer

from Trabajadores.serializers import UserSerializer, PerfilSerializer


class AporteSerializer(ModelSerializer):
    class Meta:
        model = Aporte
        fields = '__all__'


class AporteDetailSerializer(ModelSerializer):
    trabajador = PerfilSerializer()
    class Meta:
        model = Aporte
        fields = '__all__'


class AporteUpdateSerializer(ModelSerializer):
    class Meta:
        model = Aporte
        exclude = ['tienda','trabajador']