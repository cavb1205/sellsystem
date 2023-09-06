
from rest_framework import serializers
from .models import Tienda, Cierre_Caja, Tienda_Membresia, Membresia, Tienda_Administrador
from Trabajadores.serializers import UserSerializer, PerfilSerializer


class TiendaCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tienda
        fields = ['nombre','administrador']


class TiendaSerializer(serializers.ModelSerializer):

    class Meta:
        model = Tienda
        fields = '__all__'

    def to_representation(self, instance):
        return {
            'id':instance.id,
            'nombre':instance.nombre,
            'telefono':instance.telefono,
            'fecha_registro': instance.fecha_registro,
            'administrador':instance.administrador.first_name,
            'administrador_id': instance.administrador.id,
            'caja':instance.caja_inicial,
            'estado': instance.estado,
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
            'aportes_mes': instance.aportes_mes(),
            'gastos_mes': instance.gastos_mes(),
            'utilidades_mes': instance.utilidades_mes(),
            'ventas_netas_mes': instance.ventas_netas_mes(),
            'aportes_ano': instance.aportes_ano(),
            'gastos_ano': instance.gastos_ano(),
            'utilidades_ano': instance.utilidades_ano(),
            'ventas_netas_ano': instance.ventas_netas_ano(),
            'perdidas_ano': instance.perdidas_ano(),
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
    class Meta:
        model = Tienda_Membresia
        fields = '__all__'

class CajaSerializer(serializers.ModelSerializer):

    class Meta:
        model = Cierre_Caja
        fields = '__all__'