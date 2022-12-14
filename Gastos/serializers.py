
from rest_framework.serializers import ModelSerializer
from Gastos.models import Gasto, Tipo_Gasto



class TipoGastoSerializer(ModelSerializer):
    class Meta:
        model = Tipo_Gasto
        fields = '__all__'


class GastoSerializer(ModelSerializer):
    class Meta:
        model = Gasto
        fields = '__all__'
    
 
class GastoDetailSerializer(ModelSerializer):
    tipo_gasto = TipoGastoSerializer()
    class Meta:
        model = Gasto
        fields = '__all__'


class GastoUpdateSerializer(ModelSerializer):
    class Meta:
        model = Gasto
        exclude =['tienda','trabajador','tipo_gasto']