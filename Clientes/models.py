from django.db import models

from Tiendas.models import Tienda


class Cliente(models.Model):
    '''informacion personal de un cliente(datos personales de contacto)'''

    ESTADO_CLIENTE_CHOICES = [
        ('Activo','Activo'),
        ('Inactivo','Inactivo'),
        ('Bloqueado','Bloqueado')
    ]

    identificacion = models.CharField(max_length=12, unique=True)
    nombres = models.CharField(max_length=100)
    apellidos = models.CharField(max_length=100)
    nombre_local = models.CharField(max_length=100, blank=True)
    telefono_principal = models.CharField(max_length=15)
    telefono_opcional = models.CharField(max_length=15, blank=True)
    direccion = models.CharField(max_length=100)
    estado_cliente = models.CharField(choices=ESTADO_CLIENTE_CHOICES, max_length=50, default="Activo")
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE)
    fecha_creacion = models.DateField(auto_now_add=True)

    def __str__(self):
        nombres = (self.nombres)
        apellidos = self.apellidos 
        identificacion = self.identificacion
        cliente = (nombres+' ' + apellidos + ' ' + identificacion)
        return cliente