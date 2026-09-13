from dataclasses import field, fields
from decimal import Decimal
from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from Ventas.models import Venta
from Ventas.riesgo import (
    calcular_cuotas_atrasadas,
    calcular_riesgo_venta,
    dias_completos_sin_abono,
)
from Clientes.serializers import ClienteSerializer
from Clientes.models import Cliente
from Tiendas.fecha_operativa import fecha_operativa
from Ventas.riesgo import calcular_fecha_vencimiento


def _renovacion_id(obj):
    """Devuelve el id de la venta nueva que renovó esta, o None."""
    nueva = obj.renovacion.only('id').first() if obj.pk else None
    return nueva.id if nueva else None


class VentaSerializer(ModelSerializer):
    valor_venta = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0.01'),
    )
    interes = serializers.IntegerField(min_value=0, max_value=100)
    cuotas = serializers.IntegerField(min_value=1, max_value=120)
    saldo_actual = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )
    fecha_vencimiento = serializers.DateField(read_only=True)
    estado_venta = serializers.CharField(read_only=True)
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

    def validate_fecha_venta(self, value):
        tienda = self.context.get('tienda') or getattr(self.instance, 'tienda', None)
        if tienda and value > fecha_operativa(tienda):
            raise serializers.ValidationError('La fecha de venta no puede quedar en el futuro.')
        return value

    def create(self, validated_data):
        valor_venta = validated_data['valor_venta']
        interes = Decimal(validated_data.get('interes', 0))
        validated_data['saldo_actual'] = valor_venta + (interes / Decimal('100')) * valor_venta
        validated_data['estado_venta'] = 'Vigente'
        validated_data['fecha_vencimiento'] = calcular_fecha_vencimiento(
            validated_data['fecha_venta'],
            validated_data['cuotas'],
            validated_data['plazo'],
        )
        return super().create(validated_data)



class VentaUpdateSerializer(ModelSerializer):
    valor_venta = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0.01'),
    )
    interes = serializers.IntegerField(min_value=0, max_value=100)
    cuotas = serializers.IntegerField(min_value=1, max_value=120)

    class Meta:
        model = Venta
        exclude = ['cliente', 'creado_por']

    def to_internal_value(self, data):
        # Estos campos los recalcula el backend; nunca se acepta su valor
        # enviado por el navegador.
        data = data.copy()
        for field_name in ('saldo_actual', 'estado_venta', 'fecha_vencimiento'):
            data.pop(field_name, None)
        return super().to_internal_value(data)


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
    capital_recuperado = serializers.SerializerMethodField()
    capital_expuesto = serializers.SerializerMethodField()
    interes_cobrado = serializers.SerializerMethodField()
    interes_no_cobrado = serializers.SerializerMethodField()
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
            'capital_recuperado', 'capital_expuesto', 'interes_cobrado',
            'interes_no_cobrado',
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
        return calcular_cuotas_atrasadas(
            cuota,
            self._recaudos_count(obj),
            self.get_total_abonado(obj),
        )

    def get_perdida(self, obj):
        return self._saldo(obj)

    def _abonos_reales(self, obj):
        return max(Decimal(getattr(obj, '_recaudos_total', 0) or 0), Decimal('0'))

    def _interes_total(self, obj):
        capital = Decimal(obj.valor_venta or 0)
        return max(
            capital * Decimal(obj.interes or 0) / Decimal('100'),
            Decimal('0'),
        )

    def get_capital_recuperado(self, obj):
        return min(self._abonos_reales(obj), Decimal(obj.valor_venta or 0))

    def get_capital_expuesto(self, obj):
        return max(
            Decimal(obj.valor_venta or 0) - self.get_capital_recuperado(obj),
            Decimal('0'),
        )

    def get_interes_cobrado(self, obj):
        abonos_despues_del_capital = max(
            self._abonos_reales(obj) - Decimal(obj.valor_venta or 0),
            Decimal('0'),
        )
        return min(abonos_despues_del_capital, self._interes_total(obj))

    def get_interes_no_cobrado(self, obj):
        return max(
            self._interes_total(obj) - self.get_interes_cobrado(obj),
            Decimal('0'),
        )

    def get_dias_sin_abono(self, obj):
        referencia = getattr(obj, '_ultimo_abono_real', None) or obj.fecha_venta
        return dias_completos_sin_abono(
            referencia,
            hoy=fecha_operativa(obj.tienda),
        )

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
