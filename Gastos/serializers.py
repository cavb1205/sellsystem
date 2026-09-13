
from decimal import Decimal

from rest_framework import serializers
from rest_framework.serializers import ModelSerializer
from Gastos.models import Gasto, Tipo_Gasto
from Tiendas.fecha_operativa import fecha_operativa
from Tiendas.models import Tienda



class TipoGastoSerializer(ModelSerializer):
    class Meta:
        model = Tipo_Gasto
        fields = '__all__'


class GastoSerializer(ModelSerializer):
    valor = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))

    class Meta:
        model = Gasto
        fields = '__all__'

    def validate_fecha(self, value):
        tienda = self.initial_data.get('tienda')
        if tienda:
            tienda_obj = Tienda.objects.filter(pk=tienda).first()
            if tienda_obj and value > fecha_operativa(tienda_obj):
                raise serializers.ValidationError('La fecha del gasto no puede quedar en el futuro.')
        return value
    
 
class GastoDetailSerializer(ModelSerializer):
    tipo_gasto = TipoGastoSerializer()
    class Meta:
        model = Gasto
        fields = '__all__'


class GastoUpdateSerializer(ModelSerializer):
    valor = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))

    class Meta:
        model = Gasto
        exclude =['tienda','trabajador','tipo_gasto']

    def validate_fecha(self, value):
        tienda = getattr(self.instance, 'tienda', None)
        if tienda and value > fecha_operativa(tienda):
            raise serializers.ValidationError('La fecha del gasto no puede quedar en el futuro.')
        return value
