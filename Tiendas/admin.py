from django.contrib import admin
from .models import (
    AlertaOperativa,
    Tienda,
    Cierre_Caja,
    MovimientoCaja,
    Membresia,
    Tienda_Membresia,
    Tienda_Administrador,
    PagoMembresia,
)




@admin.register(Tienda)
class TiendaAdmin(admin.ModelAdmin):
    readonly_fields = ('caja_inicial', 'fecha_registro')


class SoloLecturaAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Tienda_Administrador)
admin.site.register(Cierre_Caja, SoloLecturaAdmin)
admin.site.register(MovimientoCaja, SoloLecturaAdmin)
admin.site.register(Membresia)
admin.site.register(Tienda_Membresia)
admin.site.register(PagoMembresia)
admin.site.register(AlertaOperativa)
