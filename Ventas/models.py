from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils import timezone

from Clientes.models import Cliente
from Tiendas.models import Tienda
from Ventas.riesgo import calcular_cuotas_atrasadas, dias_completos_sin_abono
from Tiendas.fecha_operativa import fecha_operativa




class Venta(models.Model):
    '''Registro de las ventas de la tienda'''

    Estado_Venta_Choises = [
        ('Vigente','Vigente'),
        ('Vencido','Vencido'),
        ('Pagado','Pagado'),
        ('Perdida','Perdida'),
        ('Atrasado','Atrasado'),
    ]

    Plazo_Venta_Choises = [
        ('Diario','Diario'),
        ('Semanal','Semanal'),
        ('Mensual','Mensual'),
    ]


    fecha_venta = models.DateField(auto_now=False)
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    valor_venta = models.DecimalField(max_digits=10, decimal_places=2)
    interes = models.IntegerField(null=False, blank=False, default=20)
    cuotas = models.IntegerField(blank=False,null=False, default=20)
    plazo = models.CharField(max_length=10, choices=Plazo_Venta_Choises, default='Diario')
    # 0 conserva la cronología histórica (mensual 30 días y tolerancia final).
    # 1: nuevos no diarios con fechas contractuales explícitas, mensual calendario.
    calendario_version = models.PositiveSmallIntegerField(default=0, editable=False)
    comentario = models.CharField(max_length=100, blank=True,)
    estado_venta = models.CharField(max_length=10, choices=Estado_Venta_Choises, default='Vigente')
    saldo_actual = models.DecimalField(max_digits=10,decimal_places=2, null=True, blank=True)
    fecha_vencimiento = models.DateField(auto_now=False, null=True, blank=True)
    tienda = models.ForeignKey(Tienda,on_delete=models.CASCADE)
    # Usuario que registró el crédito. Nulo para ventas históricas creadas
    # antes de activar la auditoría.
    creado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ventas_creadas',
    )
    # Si esta venta fue creada renovando otra (vencida), apunta a la original.
    # La inversa (venta.renovacion) marca a la vieja como "renovada" en el
    # cálculo del score crediticio.
    origen_renovacion = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='renovacion',
    )
    # Vacío para históricos sin evidencia. No se deduce de un FK nulo:
    # una venta original eliminada puede haber borrado ese vínculo.
    tipo_origen = models.CharField(
        max_length=10, blank=True, default='', editable=False,
        choices=[('Nuevo', 'Nuevo'), ('Renovacion', 'Renovación')],
    )

    def __str__(self):
        return self.cliente.nombres

    def total_a_pagar(self):
        # Todo en Decimal: no truncar centavos ni caer en aritmética flotante.
        return self.valor_venta + (Decimal(self.interes) / Decimal(100)) * self.valor_venta

    def valor_cuota(self):
        if not self.cuotas:
            return 0
        return self.total_a_pagar() / self.cuotas

    def pagos_realizados(self):
        if not self.valor_cuota():
            return 0
        saldo = self.saldo_actual or Decimal(0)
        return round((self.total_a_pagar() - saldo) / self.valor_cuota(), 2)

    def pagos_pendientes(self):
        if not self.valor_cuota():
            return 0
        saldo = self.saldo_actual or Decimal(0)
        return round(saldo / self.valor_cuota(), 2)

    def total_abonado(self):
        saldo = self.saldo_actual or Decimal(0)
        return self.total_a_pagar() - saldo


    def promedio_pago(self):
        venta = Venta.objects.get(id=self.id)
        recaudos = venta.recaudo_set.filter(
            venta=venta.id,
            es_renovacion=False,
        )
        if recaudos:
            return round(self.total_abonado() / recaudos.count(), 0)
        else:
            return 0

    def dias_atrasados(self):
        from Ventas.calendario import calendario_venta
        calendario = calendario_venta(self)
        if calendario is not None:
            return Decimal(calendario['cuotas_atrasadas'])
        if not self.valor_cuota():
            return 0
        venta = Venta.objects.get(id=self.id)
        recaudos = venta.recaudo_set.filter(
            venta=venta.id,
            es_renovacion=False,
        )
        return calcular_cuotas_atrasadas(
            self.valor_cuota(),
            recaudos.count(),
            self.total_abonado(),
        )

    def perdida(self):
        return self.saldo_actual or Decimal(0)

    def dias_sin_abono(self):
        """Jornadas completas cerradas desde el último abono real.

        Si el crédito nunca ha recibido un abono, se cuenta desde la fecha de
        venta, sin sumar el día actual mientras la jornada siga abierta. Es
        una señal informativa para clasificar el deterioro de la cartera
        (créditos abandonados / posible castigo); no afecta saldos."""
        ultimo = (
            self.recaudo_set.filter(
                valor_recaudo__gt=0,
                es_renovacion=False,
            )
            .order_by('-fecha_recaudo')
            .first()
        )
        referencia = ultimo.fecha_recaudo if ultimo else self.fecha_venta
        return dias_completos_sin_abono(
            referencia,
            hoy=fecha_operativa(self.tienda),
        )


class DeclaracionPerdidaVenta(models.Model):
    """Foto de una declaración, no una pérdida definitiva neta de recuperaciones.

    Los identificadores y montos sobreviven a eliminaciones. Una nueva transición
    a pérdida tras reactivación es otra declaración; no se reescribe la anterior.
    """

    venta = models.ForeignKey(Venta, on_delete=models.SET_NULL, null=True,
                              related_name='declaraciones_perdida')
    tienda = models.ForeignKey(Tienda, on_delete=models.SET_NULL, null=True)
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    venta_id_ref = models.PositiveBigIntegerField()
    tienda_id_ref = models.PositiveBigIntegerField(db_index=True)
    tienda_nombre = models.CharField(max_length=200)
    usuario_nombre = models.CharField(max_length=150)
    registrada_en = models.DateTimeField(default=timezone.now, editable=False)
    fecha_declaracion = models.DateField(db_index=True)
    zona_horaria = models.CharField(max_length=64)
    fecha_venta_original = models.DateField()
    capital_original = models.DecimalField(max_digits=12, decimal_places=2)
    interes_contratado = models.DecimalField(max_digits=12, decimal_places=2)
    abonos_reales_netos = models.DecimalField(max_digits=12, decimal_places=2)
    saldo_declarado = models.DecimalField(max_digits=12, decimal_places=2)
    capital_no_recuperado = models.DecimalField(max_digits=12, decimal_places=2)
    interes_no_cobrado = models.DecimalField(max_digits=12, decimal_places=2)
    saldo_consistente = models.BooleanField()

    class Meta:
        ordering = ['-registrada_en', '-id']
        indexes = [models.Index(fields=['tienda_id_ref', 'fecha_declaracion'],
                                name='perdida_ruta_fecha_idx')]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Las declaraciones de pérdida no se reescriben.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Las declaraciones de pérdida no se eliminan.')


class AjusteVentaAdministrativo(models.Model):
    """Historial inmutable de correcciones administrativas de una venta.

    Estos ajustes solo corrigen la cronología y el número de cuotas. No
    modifican el capital, la tasa, el saldo ni los recaudos existentes.
    """

    venta = models.ForeignKey(
        Venta,
        on_delete=models.PROTECT,
        related_name='ajustes_administrativos',
    )
    usuario = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='ajustes_ventas_administrativos',
    )
    motivo = models.CharField(max_length=500)
    fecha_venta_anterior = models.DateField()
    fecha_venta_nueva = models.DateField()
    cuotas_anteriores = models.PositiveIntegerField()
    cuotas_nuevas = models.PositiveIntegerField()
    fecha_vencimiento_anterior = models.DateField(null=True, blank=True)
    fecha_vencimiento_nueva = models.DateField(null=True, blank=True)
    valor_cuota_anterior = models.DecimalField(max_digits=12, decimal_places=2)
    valor_cuota_nueva = models.DecimalField(max_digits=12, decimal_places=2)
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creada', '-id']

    def __str__(self):
        return f'Ajuste administrativo venta #{self.venta_id}'
