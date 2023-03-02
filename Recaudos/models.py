
from unittest.util import _MAX_LENGTH
from django.db import models

from Tiendas.models import Tienda
from Trabajadores.models import Perfil
from Ventas.models import Venta

class Visita_Blanco(models.Model):

    Tipo_Falla_Choices = [

        ('Casa o Local Cerrado','Casa o Local Cerrado'),
        ('Cliente no Tiene Dinero','Cliente no Tiene Dinero'),
        ('Cliente de Viaje','Cliente de Viaje'),
        ('Cliente no Aparece','Cliente no Aparece'),
        ('Cliente Enfermo','Cliente Enfermo'),
        ('Otro Motivo','Otro Motivo'),
    ]

    tipo_falla = models.CharField(max_length=50, choices=Tipo_Falla_Choices)
    comentario = models.CharField(max_length=250, blank=True, null=True)

    def __str__(self):
        return self.tipo_falla
        

class Recaudo(models.Model):

    fecha_recaudo = models.DateField(auto_now=False)
    valor_recaudo = models.DecimalField(max_digits=10, decimal_places=2)
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE)
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE)
    visita_blanco = models.ForeignKey(Visita_Blanco, on_delete=models.CASCADE, null=True ,blank=True)

    def __str__(self):
        return str(self.fecha_recaudo)

