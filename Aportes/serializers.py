
from .models import *
from decimal import Decimal

from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from Trabajadores.serializers import UserSerializer, PerfilSerializer
from Tiendas.fecha_operativa import fecha_operativa
from Tiendas.models import Tienda


class AporteSerializer(ModelSerializer):
    valor = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))

    class Meta:
        model = Aporte
        fields = '__all__'

    def validate_fecha(self, value):
        tienda_id = self.initial_data.get('tienda')
        tienda = Tienda.objects.filter(pk=tienda_id).first()
        if tienda and value > fecha_operativa(tienda):
            raise serializers.ValidationError('La fecha del aporte no puede quedar en el futuro.')
        return value


class AporteDetailSerializer(ModelSerializer):
    trabajador = PerfilSerializer()
    class Meta:
        model = Aporte
        fields = '__all__'


class AporteUpdateSerializer(ModelSerializer):
    valor = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))

    class Meta:
        model = Aporte
        exclude = ['tienda','trabajador']

    def validate_fecha(self, value):
        tienda = getattr(self.instance, 'tienda', None)
        if tienda and value > fecha_operativa(tienda):
            raise serializers.ValidationError('La fecha del aporte no puede quedar en el futuro.')
        return value
