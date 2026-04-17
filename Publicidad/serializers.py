from rest_framework.serializers import ModelSerializer, SerializerMethodField
from Publicidad.models import Publicidad


class PublicidadSerializer(ModelSerializer):
    trabajador_nombre = SerializerMethodField()

    class Meta:
        model = Publicidad
        fields = '__all__'

    def get_trabajador_nombre(self, obj):
        user = obj.trabajador.trabajador
        return f"{user.first_name} {user.last_name}".strip() or user.username


class PublicidadCreateSerializer(ModelSerializer):
    class Meta:
        model = Publicidad
        fields = ['latitud', 'longitud', 'precision_gps', 'nota']
