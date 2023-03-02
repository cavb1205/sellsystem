from django.db import models

from Tiendas.models import Tienda
from Trabajadores.models import Perfil


class Tipo_Gasto(models.Model):
    '''Tipos de gasto que puede tener una tienda'''

    tipo_gasto = models.CharField(max_length=100)

    def __str__(self):
        return self.tipo_gasto



class Gasto(models.Model):
    '''Registro de gastos de la tienda'''

    fecha = models.DateField(auto_now=False)
    tipo_gasto = models.ForeignKey(Tipo_Gasto, on_delete=models.CASCADE)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    comentario = models.CharField(max_length=100,null=True, blank=True)
    trabajador = models.ForeignKey(Perfil, on_delete=models.CASCADE)
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE)

    def __str__(self):
        return str(self.fecha)