from dataclasses import field, fields
from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from Ventas.models import Venta
from Ventas.riesgo import calcular_riesgo_venta, dias_completos_sin_abono
from Clientes.serializers import ClienteSerializer
from Clientes.models import Cliente


def _renovacion_id(obj):
    """Devuelve el id de la venta nueva que renovó esta, o None."""
    nueva = obj.renovacion.only('id').first() if obj.pk else None
    return nueva.id if nueva else None


class VentaSerializer(ModelSerializer):
    fue_renovada = serializers.SerializerMethodField()
    renovacion_id = serializers.SerializerMethodField()
    creado_por = serializers.PrimaryKeyRelatedField(read_only=True)

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
        exclude = ['cliente', 'creado_por']


class VentaCorreccionAdministrativaSerializer(serializers.Serializer):
    """Campos permitidos al corregir una venta que ya tiene recaudos."""

    fecha_venta = serializers.DateField()
    cuotas = serializers.IntegerField(min_value=1)
    motivo = serializers.CharField(min_length=5, max_length=500, trim_whitespace=True)


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
            'perdida','dias_sin_abono',
            'fue_renovada', 'renovacion_id', 'origen_renovacion_id', 'creado_por',
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


class VentaListaSerializer(ModelSerializer):
    """Representación para listados y reportes.

    A diferencia del detalle, nunca consulta la base de datos por cada venta.
    Los campos que antes ejecutaban consultas reciben sus valores mediante
    anotaciones de ``Ventas.views._anotar_ventas_lista``.
    """
    cliente = ClienteSerializer(read_only=True)
    total_a_pagar = serializers.SerializerMethodField()
    valor_cuota = serializers.SerializerMethodField()
    pagos_pendientes = serializers.SerializerMethodField()
    pagos_realizados = serializers.SerializerMethodField()
    total_abonado = serializers.SerializerMethodField()
    promedio_pago = serializers.SerializerMethodField()
    dias_atrasados = serializers.SerializerMethodField()
    perdida = serializers.SerializerMethodField()
    dias_sin_abono = serializers.SerializerMethodField()
    fecha_ultimo_abono = serializers.SerializerMethodField()
    fue_renovada = serializers.SerializerMethodField()
    renovacion_id = serializers.SerializerMethodField()
    origen_renovacion_id = serializers.IntegerField(read_only=True)
    riesgo_cartera = serializers.SerializerMethodField()

    class Meta:
        model = Venta
        fields = (
            'id', 'fecha_venta', 'cliente', 'valor_venta', 'interes', 'cuotas',
            'plazo', 'comentario', 'estado_venta', 'tienda', 'total_a_pagar',
            'valor_cuota', 'saldo_actual', 'pagos_pendientes',
            'pagos_realizados', 'fecha_vencimiento', 'total_abonado',
            'promedio_pago', 'dias_atrasados', 'perdida', 'dias_sin_abono',
            'fecha_ultimo_abono',
            'fue_renovada', 'renovacion_id', 'origen_renovacion_id',
            'creado_por', 'riesgo_cartera',
        )

    @staticmethod
    def _total(obj):
        return obj.total_a_pagar()

    @staticmethod
    def _cuota(obj):
        return obj.valor_cuota()

    @staticmethod
    def _saldo(obj):
        return obj.saldo_actual or 0

    def _recaudos_count(self, obj):
        return getattr(obj, '_recaudos_count', 0) or 0

    def get_total_a_pagar(self, obj):
        return self._total(obj)

    def get_valor_cuota(self, obj):
        return self._cuota(obj)

    def get_pagos_pendientes(self, obj):
        cuota = self._cuota(obj)
        if not cuota:
            return 0
        return round(self._saldo(obj) / cuota, 2)

    def get_pagos_realizados(self, obj):
        cuota = self._cuota(obj)
        if not cuota:
            return 0
        return round((self._total(obj) - self._saldo(obj)) / cuota, 2)

    def get_total_abonado(self, obj):
        return self._total(obj) - self._saldo(obj)

    def get_promedio_pago(self, obj):
        count = self._recaudos_count(obj)
        if not count:
            return 0
        return round(self.get_total_abonado(obj) / count, 0)

    def get_dias_atrasados(self, obj):
        cuota = self._cuota(obj)
        if not cuota:
            return 0
        return round(
            ((cuota * self._recaudos_count(obj)) - self.get_total_abonado(obj)) / cuota,
            2,
        )

    def get_perdida(self, obj):
        return self._saldo(obj)

    def get_dias_sin_abono(self, obj):
        referencia = getattr(obj, '_ultimo_abono_real', None) or obj.fecha_venta
        return dias_completos_sin_abono(referencia)

    def get_fecha_ultimo_abono(self, obj):
        return getattr(obj, '_ultimo_abono_real', None)

    def get_riesgo_cartera(self, obj):
        return calcular_riesgo_venta(
            plazo=obj.plazo,
            estado_venta=obj.estado_venta,
            dias_sin_abono=self.get_dias_sin_abono(obj),
            dias_atrasados=self.get_dias_atrasados(obj),
            total_abonado=self.get_total_abonado(obj),
        )

    def get_fue_renovada(self, obj):
        return getattr(obj, '_renovacion_id', None) is not None

    def get_renovacion_id(self, obj):
        return getattr(obj, '_renovacion_id', None)


class VentaReporteSerializer(ModelSerializer):
    """Campos mínimos para agregaciones de utilidad y cierre de caja."""
    total_a_pagar = serializers.SerializerMethodField()
    perdida = serializers.SerializerMethodField()

    class Meta:
        model = Venta
        fields = (
            'id', 'fecha_venta', 'valor_venta', 'estado_venta',
            'total_a_pagar', 'perdida',
        )

    def get_total_a_pagar(self, obj):
        return obj.total_a_pagar()

    def get_perdida(self, obj):
        return obj.saldo_actual or 0
