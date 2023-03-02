from django.db import models
from Tiendas.models import Tienda
from Trabajadores.models import Perfil


class Aporte(models.Model):
    fecha = models.DateField(auto_now=False)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    comentario = models.CharField(max_length=100, blank=True)
    trabajador = models.ForeignKey(Perfil, on_delete=models.CASCADE)
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE)

    def __str__(self):
        return str(self.fecha)
