from rest_framework.serializers import ModelSerializer

from Recaudos.models import Recaudo
from Recaudos.models import Visita_Blanco

from Ventas.serializers import VentaDetailSerializer

class Visitas_BlancoSerializer(ModelSerializer):
    class Meta:
        model = Visita_Blanco
        fields = '__all__'

class RecaudoDetailSerializer(ModelSerializer):
    visita_blanco = Visitas_BlancoSerializer()
    venta = VentaDetailSerializer()
    class Meta:
        model = Recaudo
        fields = '__all__'


class RecaudoSerializer(ModelSerializer):
    
    class Meta:
        model = Recaudo
        fields = '__all__'


class RecaudoUpdateSerializer(ModelSerializer):
    class Meta:
        model = Recaudo
        fields = ['fecha_recaudo','valor_recaudo']

