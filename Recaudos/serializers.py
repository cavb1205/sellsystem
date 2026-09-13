from decimal import Decimal

from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from Recaudos.models import Recaudo
from Recaudos.models import Visita_Blanco
from Clientes.models import Cliente
from Ventas.models import Venta

from Ventas.serializers import VentaDetailSerializer
from Tiendas.fecha_operativa import fecha_operativa

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
    valor_recaudo = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0'),
    )

    class Meta:
        model = Recaudo
        fields = '__all__'

    def validate(self, attrs):
        venta = attrs.get('venta')
        tienda = attrs.get('tienda')
        if venta and tienda and venta.tienda_id != tienda.id:
            raise serializers.ValidationError(
                {'tienda': 'El recaudo y la venta deben pertenecer a la misma ruta.'}
            )
        fecha = attrs.get('fecha_recaudo')
        if fecha and tienda and fecha > fecha_operativa(tienda):
            raise serializers.ValidationError(
                {'fecha_recaudo': 'La fecha del recaudo no puede quedar en el futuro.'}
            )
        if fecha and venta and fecha < venta.fecha_venta:
            raise serializers.ValidationError(
                {'fecha_recaudo': 'La fecha del recaudo no puede ser anterior a la venta.'}
            )
        return attrs


class RecaudoUpdateSerializer(ModelSerializer):
    valor_recaudo = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0'),
    )

    class Meta:
        model = Recaudo
        fields = ['fecha_recaudo','valor_recaudo']

    def validate_fecha_recaudo(self, value):
        tienda = getattr(self.instance, 'tienda', None)
        if tienda and value > fecha_operativa(tienda):
            raise serializers.ValidationError('La fecha del recaudo no puede quedar en el futuro.')
        if self.instance and self.instance.venta and value < self.instance.venta.fecha_venta:
            raise serializers.ValidationError('La fecha del recaudo no puede ser anterior a la venta.')
        return value
