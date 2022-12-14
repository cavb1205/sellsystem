
from django.db import models
from django.contrib.auth.models import User

from Tiendas.models import Tienda



class Perfil(models.Model):
    '''Perfil de los trabajadores heredando del modelo user de django'''

    trabajador = models.OneToOneField(User, on_delete=models.CASCADE)
    identificacion = models.CharField(max_length=50)
    telefono = models.CharField(max_length=15)
    direccion = models.CharField(max_length=100,blank=True,null=True)
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE)

    def __str__(self):
        return self.identificacion





 