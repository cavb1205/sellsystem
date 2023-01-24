from django.db import models
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
    valor_venta = models.DecimalField(max_digits=10, decimal_places=0)
    interes = models.IntegerField(null=False, blank=False, default=20)
    cuotas = models.IntegerField(blank=False,null=False, default=20)
    plazo = models.CharField(max_length=10, choices=Plazo_Venta_Choises, default='Diario')
    comentario = models.CharField(max_length=100, blank=True,)
    estado_venta = models.CharField(max_length=10, choices=Estado_Venta_Choises, default='Vigente')
    saldo_actual = models.DecimalField(max_digits=10,decimal_places=0, null=True, blank=True)
    fecha_vencimiento = models.DateField(auto_now=False, null=True, blank=True)
    tienda = models.ForeignKey(Tienda,on_delete=models.CASCADE)

    def __str__(self):
        return self.cliente.nombres

    def total_a_pagar(self):
        valor_venta = int(self.valor_venta)
        total_a_pagar = valor_venta + ((self.interes/100) * valor_venta)
        return total_a_pagar
    
    def valor_cuota(self):
        total_a_pagar = self.total_a_pagar()
        valor_cuota = total_a_pagar / self.cuotas
        return valor_cuota

    def pagos_realizados(self):
        pagos_realizados = round((self.total_a_pagar() - int(self.saldo_actual)) / self.valor_cuota(),2)
        return pagos_realizados

    def pagos_pendientes(self):        
        pagos_pendientes = round(int(self.saldo_actual) / self.valor_cuota(),2)
        return pagos_pendientes

    def total_abonado(self):
        total_abonado = self.total_a_pagar() - int(self.saldo_actual)
        return total_abonado

  
    def promedio_pago(self):
        venta = Venta.objects.get(id=self.id)
        recaudos = venta.recaudo_set.filter(venta=venta.id)
        if recaudos:
            promedio = round(self.total_abonado() / recaudos.count(),0)
            return promedio
        else:
            return 0

    def dias_atrasados(self):
        venta = Venta.objects.get(id=self.id)
        recaudos = venta.recaudo_set.filter(venta=venta.id)
        dias_atrasados = round(((self.valor_cuota() * recaudos.count()) - self.total_abonado()) / self.valor_cuota(),2)
        return dias_atrasados 

    def perdida(self):
        valor_perdida = int(self.valor_venta) - self.total_abonado()
        return valor_perdida