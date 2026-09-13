from decimal import Decimal

from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from Utilidades.models import Utilidad
from Trabajadores.serializers import PerfilSerializer
from Tiendas.fecha_operativa import fecha_operativa
from Tiendas.models import Tienda
class UtilidadSerializer(ModelSerializer):
    valor = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))

    class Meta:
        model = Utilidad
        fields = '__all__'

    def validate_fecha(self, value):
        tienda_id = self.initial_data.get('tienda')
        tienda = Tienda.objects.filter(pk=tienda_id).first()
        if tienda and value > fecha_operativa(tienda):
            raise serializers.ValidationError('La fecha de la utilidad no puede quedar en el futuro.')
        return value


class UtilidadDetailSerializer(ModelSerializer):
    trabajador = PerfilSerializer()
    class Meta:
        model = Utilidad
        fields = '__all__'


class UtilidadUpdateSerializer(ModelSerializer):
    valor = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))

    class Meta:
        model = Utilidad
        exclude = ['trabajador']

    def validate_fecha(self, value):
        tienda = getattr(self.instance, 'tienda', None)
        if tienda and value > fecha_operativa(tienda):
            raise serializers.ValidationError('La fecha de la utilidad no puede quedar en el futuro.')
        return value
