
from rest_framework import serializers
from .models import Tienda, Cierre_Caja, Tienda_Membresia, Membresia


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
            'caja':instance.caja_inicial,
            'estado': instance.estado,

        }

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