"""Persistencia independiente para notificaciones de clientes."""
from django.conf import settings
from django.db import models


class TelegramVinculo(models.Model):
    cliente = models.OneToOneField('Clientes.Cliente', on_delete=models.CASCADE,
                                  related_name='telegram_vinculo')
    chat_id = models.BigIntegerField(db_index=True)
    activo = models.BooleanField(default=False)
    consentimiento_en = models.DateTimeField(null=True, blank=True)
    confirmado_en = models.DateTimeField(null=True, blank=True)
    confirmado_por = models.ForeignKey(settings.AUTH_USER_MODEL,
                                      on_delete=models.SET_NULL, null=True)
    version = models.PositiveIntegerField(default=1)
    creado_en = models.DateTimeField(auto_now_add=True)


class TelegramInvitacion(models.Model):
    cliente = models.ForeignKey('Clientes.Cliente', on_delete=models.CASCADE)
    # Guardar solamente el hash: el enlace original se muestra una sola vez.
    token_hash = models.CharField(max_length=64, unique=True)
    expira_en = models.DateTimeField()
    consumida_en = models.DateTimeField(null=True, blank=True)
    creada_por = models.ForeignKey(settings.AUTH_USER_MODEL,
                                  on_delete=models.SET_NULL, null=True)
    creada_en = models.DateTimeField(auto_now_add=True)


class TelegramEvento(models.Model):
    vinculo = models.ForeignKey(TelegramVinculo, on_delete=models.CASCADE)
    version_vinculo = models.PositiveIntegerField()
    clave = models.CharField(max_length=160, unique=True)
    tipo = models.CharField(max_length=30)
    # Referencias conservadas cuando se elimina un recaudo o crédito.
    credito_id_ref = models.PositiveBigIntegerField()
    registro_id_ref = models.PositiveBigIntegerField(null=True, blank=True)
    datos = models.JSONField(default=dict)
    estado = models.CharField(max_length=16, default='pendiente', choices=[
        ('pendiente', 'Pendiente'), ('procesando', 'Procesando'),
        ('enviado', 'Enviado'), ('incierto', 'Resultado incierto'),
        ('fallido', 'Fallido'), ('cancelado', 'Cancelado'),
    ])
    intentos = models.PositiveIntegerField(default=0)
    proximo_intento = models.DateTimeField(null=True, blank=True)
    mensaje_id = models.BigIntegerField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        indexes = [
            models.Index(fields=['estado', 'id'], name='tg_evento_estado_id'),
            models.Index(fields=['estado', 'creado_en', 'id'], name='tg_evento_retencion'),
        ]


class TelegramWorkerCursor(models.Model):
    """Punto de continuación para repartir el procesamiento entre vínculos."""

    clave = models.CharField(max_length=40, unique=True)
    ultimo_vinculo_id = models.PositiveBigIntegerField(default=0)
    actualizado_en = models.DateTimeField(auto_now=True)


class TelegramMensaje(models.Model):
    """Metadatos mínimos para borrar mensajes del chat sin guardar su texto."""
    DIRECCIONES = [('entrante', 'Entrante'), ('saliente', 'Saliente')]

    chat_id = models.BigIntegerField()
    mensaje_id = models.BigIntegerField(null=True, blank=True)
    direccion = models.CharField(max_length=10, choices=DIRECCIONES)
    registrado_en = models.DateTimeField(auto_now_add=True)
    intentos = models.PositiveSmallIntegerField(default=0)
    proximo_intento = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['chat_id', 'mensaje_id'], name='tg_mensaje_chat_id_unico',
        )]
        indexes = [models.Index(
            fields=['registrado_en', 'id'], name='tg_mensaje_limpieza_idx',
        )]
