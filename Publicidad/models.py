from django.db import models
from Tiendas.models import Tienda
from Trabajadores.models import Perfil


class Publicidad(models.Model):
    fecha = models.DateField(auto_now_add=True)
    hora = models.DateTimeField(auto_now_add=True)
    trabajador = models.ForeignKey(Perfil, on_delete=models.CASCADE, related_name='publicidades')
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE, related_name='publicidades')
    latitud = models.DecimalField(max_digits=10, decimal_places=7)
    longitud = models.DecimalField(max_digits=10, decimal_places=7)
    precision_gps = models.FloatField(null=True, blank=True)
    nota = models.CharField(max_length=150, blank=True, null=True)

    class Meta:
        ordering = ['-hora']

    def __str__(self):
        return f"{self.trabajador} - {self.fecha} - {self.hora.strftime('%H:%M')}"
