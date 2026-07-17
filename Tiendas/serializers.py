
from rest_framework import serializers
from .models import Tienda, Cierre_Caja, Tienda_Membresia, Membresia, Tienda_Administrador, SolicitudPago, CuentaDestino
from Trabajadores.serializers import UserSerializer, PerfilSerializer


class TiendaCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tienda
        fields = ['id', 'nombre', 'administrador']

    def validate(self, attrs):
        nombre = attrs.get('nombre', '').strip()
        administrador = attrs.get('administrador')
        if Tienda.objects.filter(nombre__iexact=nombre, administrador=administrador).exists():
            raise serializers.ValidationError(
                {'nombre': 'Ya tienes una ruta con ese nombre. Elige un nombre diferente.'}
            )
        attrs['nombre'] = nombre
        return attrs


class TiendaSerializer(serializers.ModelSerializer):

    class Meta:
        model = Tienda
        fields = '__all__'

    def to_representation(self, instance):
        return {
            'id':instance.id,
            'nombre':instance.nombre,
            'telefono':instance.telefono,
            'prefijo_telefono': instance.prefijo_telefono,
            'fecha_registro': instance.fecha_registro,
            'administrador':instance.administrador.first_name,
            'administrador_id': instance.administrador.id,
            'caja':instance.caja_inicial,
            'estado': instance.estado,
            'cupo_minimo_nuevo': instance.cupo_minimo_nuevo,
            'cantidad_clientes': instance.cliente_set.count(),
            'cantidad_ventas': instance.venta_set.count(),
            'inversion': instance.inversion(),
            'gastos': instance.gastos(),
            'utilidades': instance.utilidades(),
            'perdidas': instance.perdidas(),
            'ingresos_ventas_finalizadas': instance.ingresos_x_ventas(),
            'dinero_x_cobrar': instance.dinero_x_cobrar(),
            'aportes_dia': instance.aportes_dia(),
            'gastos_dia': instance.gastos_dia(),
            'utilidades_dia': instance.utilidades_dia(),
            'recaudos_dia': instance.recaudos_dia(),
            'ventas_netas_dia': instance.ventas_netas_dia(),
            'utilidad_estimada_dia': instance.utilidad_estimada_dia(),
            'aportes_mes': instance.aportes_mes(),
            'gastos_mes': instance.gastos_mes(),
            'utilidades_mes': instance.utilidades_mes(),
            'ventas_netas_mes': instance.ventas_netas_mes(),
            'utilidad_estimada_mes': instance.utilidad_estimada_mes(),
            'aportes_ano': instance.aportes_ano(),
            'gastos_ano': instance.gastos_ano(),
            'utilidades_ano': instance.utilidades_ano(),
            'ventas_netas_ano': instance.ventas_netas_ano(),
            'perdidas_ano': instance.perdidas_ano(),
            'utilidad_estimada_ano': instance.utilidad_estimada_ano(),
        }


class TiendaAdminSerializer(serializers.ModelSerializer):
    tienda = TiendaSerializer()
    administrador = UserSerializer()

    class Meta:
        model = Tienda_Administrador
        fields = '__all__'

class MembresiaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Membresia
        fields = '__all__'

class TiendaMembresiaSerializer(serializers.ModelSerializer):
    tienda = TiendaSerializer()
    membresia = MembresiaSerializer()
    # Última señal de uso real de la ruta (recaudo o cierre de caja).
    # Se llena solo cuando la vista anota _ult_recaudo/_ult_cierre (panel root).
    ultima_actividad = serializers.SerializerMethodField()

    class Meta:
        model = Tienda_Membresia
        fields = '__all__'

    def get_ultima_actividad(self, obj):
        fechas = [f for f in (getattr(obj, '_ult_recaudo', None),
                              getattr(obj, '_ult_cierre', None)) if f]
        return max(fechas) if fechas else None

class SolicitudPagoSerializer(serializers.ModelSerializer):
    plan = serializers.CharField(source='membresia.nombre', read_only=True)
    monto_plan = serializers.DecimalField(source='membresia.precio', max_digits=12, decimal_places=0, read_only=True)
    tienda_nombre = serializers.CharField(source='tienda.nombre', read_only=True)
    tienda_id = serializers.IntegerField(source='tienda.id', read_only=True)
    solicitante = serializers.SerializerMethodField()
    revisor = serializers.SerializerMethodField()
    tiene_comprobante = serializers.SerializerMethodField()

    class Meta:
        model = SolicitudPago
        fields = [
            'id', 'codigo', 'estado', 'plan', 'monto_plan',
            'tienda_nombre', 'tienda_id',
            'solicitante', 'revisor', 'tiene_comprobante',
            'monto_detectado', 'motivo_rechazo',
            'confianza_ia', 'referencia_bancaria',
            'creada', 'procesada', 'expira',
        ]

    def _nombre_usuario(self, user):
        if not user:
            return None
        return user.get_full_name() or user.username

    def get_solicitante(self, obj):
        return self._nombre_usuario(obj.solicitada_por)

    def get_revisor(self, obj):
        return self._nombre_usuario(obj.revisada_por)

    def get_tiene_comprobante(self, obj):
        return bool(obj.comprobante)


class CuentaDestinoSerializer(serializers.ModelSerializer):
    class Meta:
        model = CuentaDestino
        fields = ['banco', 'numero', 'titular', 'tipo', 'actualizada']
        read_only_fields = ['actualizada']


class CajaSerializer(serializers.ModelSerializer):

    class Meta:
        model = Cierre_Caja
        fields = '__all__'
