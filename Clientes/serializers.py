from rest_framework.serializers import ModelSerializer

from Clientes.models import Cliente


class ClienteSerializer(ModelSerializer):
    class Meta:
        model = Cliente
        fields = '__all__'
        


class ClienteCreateSerializer(ModelSerializer):
    class Meta:
        model = Cliente
        exclude = ['fecha_creacion','estado_cliente']