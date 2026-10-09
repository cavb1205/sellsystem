import math
from rest_framework import serializers
from Publicidad.models import Publicidad
from Tiendas.fecha_operativa import fecha_operativa


class PublicidadSerializer(serializers.ModelSerializer):
    trabajador_nombre = serializers.SerializerMethodField()
    permite_eliminar = serializers.SerializerMethodField()

    class Meta:
        model = Publicidad
        fields = ['id', 'fecha', 'hora', 'trabajador', 'tienda', 'latitud', 'longitud',
                  'precision_gps', 'nota', 'registrado_por', 'autor_nombre', 'autor_rol',
                  'solicitud_id', 'trabajador_nombre', 'permite_eliminar']

    def get_trabajador_nombre(self, obj):
        if obj.autor_nombre:
            return obj.autor_nombre
        user = obj.registrado_por or (obj.trabajador.trabajador if obj.trabajador_id else None)
        return (user.get_full_name().strip() or user.username) if user else 'Autor histórico'

    def get_permite_eliminar(self, obj):
        request = self.context.get('request')
        if not request:
            return False
        if self.context.get('admin_ruta'):
            return True
        autor_id = obj.registrado_por_id or (obj.trabajador.trabajador_id if obj.trabajador_id else None)
        return autor_id == request.user.pk and obj.fecha == fecha_operativa(obj.tienda)


class PublicidadCreateSerializer(serializers.ModelSerializer):
    latitud = serializers.DecimalField(max_digits=10, decimal_places=7, min_value=-90, max_value=90)
    longitud = serializers.DecimalField(max_digits=10, decimal_places=7, min_value=-180, max_value=180)
    solicitud_id = serializers.UUIDField(required=False, allow_null=True)
    class Meta:
        model = Publicidad
        fields = ['latitud', 'longitud', 'precision_gps', 'nota', 'solicitud_id']

    def validate_precision_gps(self, value):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise serializers.ValidationError('La precisión GPS debe ser un número finito mayor o igual a cero.')
        return value
