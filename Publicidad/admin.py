from django.contrib import admin
from Publicidad.models import Publicidad

@admin.register(Publicidad)
class PublicidadAdmin(admin.ModelAdmin):
    list_display = ['trabajador', 'tienda', 'fecha', 'hora', 'nota', 'latitud', 'longitud']
    list_filter = ['fecha', 'tienda', 'trabajador']
