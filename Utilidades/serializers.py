from rest_framework.serializers import ModelSerializer

from Utilidades.models import Utilidad
from Trabajadores.serializers import PerfilSerializer
class UtilidadSerializer(ModelSerializer):
    class Meta:
        model = Utilidad
        fields = '__all__'


class UtilidadDetailSerializer(ModelSerializer):
    trabajador = PerfilSerializer()
    class Meta:
        model = Utilidad
        fields = '__all__'


class UtilidadUpdateSerializer(ModelSerializer):
    class Meta:
        model = Utilidad
        exclude = ['trabajador']