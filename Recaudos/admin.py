from django.contrib import admin

# Register your models here.
from .models import Recaudo, Visita_Blanco

admin.site.register(Recaudo)
admin.site.register(Visita_Blanco)