from rest_framework.serializers import ModelSerializer

from Recaudos.models import Recaudo
from Recaudos.models import Visita_Blanco
from Clientes.models import Cliente
from Ventas.models import Venta

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


class RecaudoHistorialSerializer(ModelSerializer):
    """Representación liviana para el historial de una sola venta.

    El detalle anterior incluía VentaDetailSerializer dentro de cada recaudo,
    repitiendo la misma venta y sus cálculos cientos de veces. Esta versión
    solo devuelve los datos que necesita la pantalla del crédito.
    """

    visita_blanco = Visitas_BlancoSerializer(read_only=True)

    class Meta:
        model = Recaudo
        fields = [
            'id', 'fecha_recaudo', 'valor_recaudo', 'venta', 'tienda',
            'visita_blanco', 'latitud', 'longitud', 'precision_gps',
            'es_renovacion',
        ]


class RecaudoClienteListaSerializer(ModelSerializer):
    """Datos del cliente usados por auditorías y mapas de recaudos."""

    class Meta:
        model = Cliente
        fields = [
            'id', 'identificacion', 'nombres', 'apellidos', 'nombre_local',
        ]


class RecaudoVentaListaSerializer(ModelSerializer):
    """Datos mínimos de la venta asociados a un recaudo."""
    cliente = RecaudoClienteListaSerializer(read_only=True)

    class Meta:
        model = Venta
        fields = ['id', 'cliente', 'saldo_actual']


class RecaudoListaSerializer(ModelSerializer):
    """Representación liviana para listados por fecha.

    No incluye VentaDetailSerializer: repetir el detalle completo por cada
    recaudo provoca consultas y payload innecesarios.
    """
    visita_blanco = Visitas_BlancoSerializer(read_only=True)
    venta = RecaudoVentaListaSerializer(read_only=True)

    class Meta:
        model = Recaudo
        fields = [
            'id', 'fecha_recaudo', 'valor_recaudo', 'venta', 'tienda',
            'visita_blanco', 'latitud', 'longitud', 'precision_gps',
            'es_renovacion',
        ]


class RecaudoSerializer(ModelSerializer):
    
    class Meta:
        model = Recaudo
        fields = '__all__'


class RecaudoUpdateSerializer(ModelSerializer):
    class Meta:
        model = Recaudo
        fields = ['fecha_recaudo','valor_recaudo']
