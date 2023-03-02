from django.db import models

from Tiendas.models import Tienda
from Trabajadores.models import Perfil



class Utilidad(models.Model):
    '''Registro de utilidades de la tienda'''

    fecha = models.DateField(auto_now=False)
    comentario = models.CharField(max_length=200,blank=True, null=True)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    trabajador = models.ForeignKey(Perfil, on_delete=models.CASCADE)
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE)

    def __str__(self):
        return str(self.fecha)
