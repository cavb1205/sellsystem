from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from Clientes.models import Cliente


class ClienteSerializer(ModelSerializer):
    class Meta:
        model = Cliente
        fields = '__all__'


class ClienteCreateSerializer(ModelSerializer):
    class Meta:
        model = Cliente
        exclude = ['fecha_creacion', 'estado_cliente']

    def validate(self, attrs):
        identificacion = attrs.get('identificacion', '').strip()
        tienda = attrs.get('tienda')
        if Cliente.objects.filter(identificacion=identificacion, tienda=tienda).exists():
            raise serializers.ValidationError(
                {'identificacion': 'Ya existe un cliente con esta identificación en esta ruta.'}
            )
        attrs['identificacion'] = identificacion
        return attrs