from django.db import models
from django.conf import settings
from django.utils import timezone
from Tiendas.models import Tienda
from Trabajadores.models import Perfil


class Publicidad(models.Model):
    fecha = models.DateField(default=timezone.localdate, editable=False)
    hora = models.DateTimeField(auto_now_add=True)
    trabajador = models.ForeignKey(Perfil, on_delete=models.SET_NULL, null=True, blank=True, related_name='publicidades')
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='publicidad_registrada')
    autor_nombre = models.CharField(max_length=180, blank=True, default='')
    autor_rol = models.CharField(max_length=15, default='Historico',
        choices=[('Administrador', 'Administrador'), ('Trabajador', 'Trabajador'), ('Historico', 'Histórico')])
    solicitud_id = models.UUIDField(null=True, blank=True, editable=False)
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE, related_name='publicidades')
    latitud = models.DecimalField(max_digits=10, decimal_places=7)
    longitud = models.DecimalField(max_digits=10, decimal_places=7)
    precision_gps = models.FloatField(null=True, blank=True)
    nota = models.CharField(max_length=150, blank=True, null=True)

    class Meta:
        ordering = ['-hora']
        constraints = [models.UniqueConstraint(fields=['tienda', 'solicitud_id'],
            condition=models.Q(solicitud_id__isnull=False), name='publicidad_solicitud_unica')]

    def __str__(self):
        return f"{self.trabajador} - {self.fecha} - {self.hora.strftime('%H:%M')}"
