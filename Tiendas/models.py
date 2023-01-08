from django.db import models
from django.contrib.auth.models import User
from datetime import *







class Tienda(models.Model):
    nombre = models.CharField(max_length=200)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    fecha_registro = models.DateField(auto_now_add=True)
    administrador = models.ForeignKey(User, on_delete=models.CASCADE)
    caja_inicial = models.DecimalField(max_digits=10,decimal_places=0, default=0)
    estado = models.BooleanField(default=True)
    

    def __str__(self):
        return self.nombre



class Cierre_Caja(models.Model):
    fecha_cierre = models.DateField(auto_now=False)
    valor = models.DecimalField(max_digits=10, decimal_places=0)
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE)
    

    def __str__(self):
        return str(self.fecha_cierre)




#suscripcion

class Membresia(models.Model):
    opciones_membresia = (
    ('Prueba','Prueba'),
    ('Mensual','Mensual'),
    ('Anual','Anual'),
    )
    nombre = models.CharField(max_length=100, choices=opciones_membresia)
    precio = models.DecimalField( max_digits=10 ,decimal_places=0, default=0)
    

    def __str__(self):
        todo = self.nombre + ' ' + str(self.precio) + ' ' + 'USD'
        return todo


class Tienda_Membresia(models.Model):
    estado_choices = (
        ('Activa','Activa'),
        ('Vencida','Vencida'),
        ('Pendiente Pago','Pendiente Pago')
    )
    tienda = models.OneToOneField(Tienda, on_delete=models.CASCADE, 
                                    null=False, blank=False)
    membresia = models.ForeignKey(Membresia, on_delete=models.CASCADE)
    fecha_activacion = models.DateField()
    fecha_vencimiento = models.DateField()
    estado = models.CharField(max_length=50, choices=estado_choices, default='Activa')

    def __str__(self):
        return str(self.tienda) + ' - ' + str(self.membresia) 