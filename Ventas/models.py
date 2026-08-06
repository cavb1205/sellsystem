from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal

from Clientes.models import Cliente
from Tiendas.models import Tienda




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
        if not self.valor_cuota():
            return 0
        venta = Venta.objects.get(id=self.id)
        recaudos = venta.recaudo_set.filter(
            venta=venta.id,
            es_renovacion=False,
        )
        dias_atrasados = round(((self.valor_cuota() * recaudos.count()) - self.total_abonado()) / self.valor_cuota(), 2)
        return dias_atrasados

    def perdida(self):
        return self.saldo_actual or Decimal(0)

    def dias_sin_abono(self):
        """Días calendario desde el último abono real (recaudo con valor > 0).

        Si el crédito nunca ha recibido un abono, se cuenta desde la fecha de
        venta. Es una señal informativa para clasificar el deterioro de la
        cartera (créditos abandonados / posible castigo); no afecta saldos."""
        from datetime import date
        ultimo = (
            self.recaudo_set.filter(
                valor_recaudo__gt=0,
                es_renovacion=False,
            )
            .order_by('-fecha_recaudo')
            .first()
        )
        referencia = ultimo.fecha_recaudo if ultimo else self.fecha_venta
        return (date.today() - referencia).days


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
