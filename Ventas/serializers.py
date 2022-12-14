from dataclasses import field, fields
from rest_framework.serializers import ModelSerializer

from Ventas.models import Venta
from Clientes.serializers import ClienteSerializer
from Clientes.models import Cliente

class VentaSerializer(ModelSerializer):
    class Meta:
        model = Venta
        fields = '__all__'



class VentaDetailSerializer(ModelSerializer):
    cliente = ClienteSerializer()
    class Meta:
        model = Venta
        fields = (
            'id','fecha_venta','cliente','valor_venta','interes','cuotas',
            'plazo','comentario','estado_venta','tienda','total_a_pagar',
            'valor_cuota','saldo_actual','pagos_pendientes','pagos_realizados',
            'fecha_vencimiento','total_abonado','promedio_pago','dias_atrasados',
            )

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

        }