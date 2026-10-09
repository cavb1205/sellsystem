
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


class AsignacionTrabajador(models.Model):
    """Acceso operativo de un trabajador a una ruta adicional."""

    perfil = models.ForeignKey(
        Perfil,
        on_delete=models.CASCADE,
        related_name='asignaciones_ruta',
    )
    tienda = models.ForeignKey(
        Tienda,
        on_delete=models.CASCADE,
        related_name='asignaciones_trabajador',
    )
    activa = models.BooleanField(default=True)
    asignada_en = models.DateTimeField(auto_now_add=True)
    asignada_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asignaciones_trabajador_creadas',
    )
    retirada_en = models.DateTimeField(null=True, blank=True)
    retirada_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asignaciones_trabajador_retiradas',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['perfil', 'tienda'],
                name='unique_asignacion_trabajador_tienda',
            ),
        ]
        ordering = ['tienda__nombre']

    def __str__(self):
        return f'{self.perfil_id} -> {self.tienda_id}'
