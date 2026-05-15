from dataclasses import field, fields
from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from Ventas.models import Venta
from Clientes.serializers import ClienteSerializer
from Clientes.models import Cliente


def _renovacion_id(obj):
    """Devuelve el id de la venta nueva que renovó esta, o None."""
    nueva = obj.renovacion.only('id').first() if obj.pk else None
    return nueva.id if nueva else None


class VentaSerializer(ModelSerializer):
    fue_renovada = serializers.SerializerMethodField()
    renovacion_id = serializers.SerializerMethodField()

    class Meta:
        model = Venta
        fields = '__all__'

    def get_fue_renovada(self, obj):
        return _renovacion_id(obj) is not None

    def get_renovacion_id(self, obj):
        return _renovacion_id(obj)



class VentaUpdateSerializer(ModelSerializer):
    class Meta:
        model = Venta
        exclude = ['cliente']


class VentaDetailSerializer(ModelSerializer):
    cliente = ClienteSerializer()
    fue_renovada = serializers.SerializerMethodField()
    renovacion_id = serializers.SerializerMethodField()
    origen_renovacion_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Venta
        fields = (
            'id','fecha_venta','cliente','valor_venta','interes','cuotas',
            'plazo','comentario','estado_venta','tienda','total_a_pagar',
            'valor_cuota','saldo_actual','pagos_pendientes','pagos_realizados',
            'fecha_vencimiento','total_abonado','promedio_pago','dias_atrasados',
            'perdida',
            'fue_renovada', 'renovacion_id', 'origen_renovacion_id',
            )

    def get_fue_renovada(self, obj):
        return _renovacion_id(obj) is not None

    def get_renovacion_id(self, obj):
        return _renovacion_id(obj)

    def to_representarion(self, instance):
        return {
            'id':instance.id,
            'fecha_venta':instance.fecha_venta,
            'fecha_vencimiento':instance.fecha_vencimiento,
            'cliente':instance.cliente,
            'valor_venta':instance.valor_venta,
            'interes':instance.interes,
            'cuotas':instance.cuotas,
            'plazo':instance.plazo,
            'comentario':instance.comentario,
            'estado_venta':instance.estado_venta,
            'tienda':instance.tienda,
            'total_a_pagar':instance.total_a_pagar(),
            'valor_cuota': instance.valor_cuota(),
            'pagos_pendientes':instance.pagos_pendientes(),
            'pagos_realizados':instance.pagos_realizados(),
            'saldo_actual':instance.saldo_actual,
            'total_abonado':instance.total_abonado(),
            'promedio_pago':instance.promedio_pago(),
            'dias_atrasados': instance.dias_atrasados(),
            'perdida':instance.perdida(),
        }