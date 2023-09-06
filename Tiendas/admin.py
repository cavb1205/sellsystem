from django.contrib import admin
from .models import Tienda, Cierre_Caja, Membresia, Tienda_Membresia, Tienda_Administrador




admin.site.register(Tienda)
admin.site.register(Tienda_Administrador)
admin.site.register(Cierre_Caja)
admin.site.register(Membresia)
admin.site.register(Tienda_Membresia)