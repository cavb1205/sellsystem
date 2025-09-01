from django.db import models
from django.contrib.auth.models import User
from django.db.models import Sum, Q
from datetime import date


class Tienda(models.Model):
    nombre = models.CharField(max_length=200)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    fecha_registro = models.DateField(auto_now_add=True)
    administrador = models.ForeignKey(User, on_delete=models.CASCADE)
    caja_inicial = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    estado = models.BooleanField(default=True)
    
    def __str__(self):
        return self.nombre

    def inversion(self):
        return self.aporte_set.aggregate(total=Sum('valor'))['total'] or 0

    def gastos(self):
        return self.gasto_set.aggregate(total=Sum('valor'))['total'] or 0

    def utilidades(self):
        return self.utilidad_set.aggregate(total=Sum('valor'))['total'] or 0

    def perdidas(self):
        return self.venta_set.filter(estado_venta='Perdida').aggregate(
            total=Sum('saldo_actual'))['total'] or 0

    def ingresos_x_ventas(self):
        ventas_pagas = self.venta_set.filter(estado_venta='Pagado')
        total = 0
        for venta in ventas_pagas:
            total += (int(venta.total_a_pagar()) - int(venta.valor_venta))
        return total

    def dinero_x_cobrar(self):
        return self.venta_set.exclude(
            estado_venta__in=['Pagado', 'Perdida']
        ).aggregate(total=Sum('saldo_actual'))['total'] or 0

    ### Calculamos lo correspondiente al día actual ###
    def aportes_dia(self):
        hoy = date.today()
        return self.aporte_set.filter(fecha=hoy).aggregate(
            total=Sum('valor'))['total'] or 0

    def gastos_dia(self):
        hoy = date.today()
        return self.gasto_set.filter(fecha=hoy).aggregate(
            total=Sum('valor'))['total'] or 0

    def utilidades_dia(self):
        hoy = date.today()
        return self.utilidad_set.filter(fecha=hoy).aggregate(
            total=Sum('valor'))['total'] or 0

    def recaudos_dia(self):
        hoy = date.today()
        return self.recaudo_set.filter(fecha_recaudo=hoy).aggregate(
            total=Sum('valor_recaudo'))['total'] or 0

    def ventas_netas_dia(self):
        hoy = date.today()
        return self.venta_set.filter(fecha_venta=hoy).aggregate(
            total=Sum('valor_venta'))['total'] or 0

    ### Calculamos lo correspondiente al mes actual  ####
    def aportes_mes(self):
        hoy = date.today()
        return self.aporte_set.filter(
            fecha__year=hoy.year,
            fecha__month=hoy.month
        ).aggregate(total=Sum('valor'))['total'] or 0

    def gastos_mes(self):
        hoy = date.today()
        return self.gasto_set.filter(
            fecha__year=hoy.year,
            fecha__month=hoy.month
        ).aggregate(total=Sum('valor'))['total'] or 0

    def utilidades_mes(self):
        hoy = date.today()
        return self.utilidad_set.filter(
            fecha__year=hoy.year,
            fecha__month=hoy.month
        ).aggregate(total=Sum('valor'))['total'] or 0

    def ventas_netas_mes(self):
        hoy = date.today()
        return self.venta_set.filter(
            fecha_venta__year=hoy.year,
            fecha_venta__month=hoy.month
        ).aggregate(total=Sum('valor_venta'))['total'] or 0

    ### Calculamos lo correspondiente al año actual ###
    def aportes_ano(self):
        hoy = date.today()
        return self.aporte_set.filter(
            fecha__year=hoy.year
        ).aggregate(total=Sum('valor'))['total'] or 0

    def gastos_ano(self):
        hoy = date.today()
        return self.gasto_set.filter(
            fecha__year=hoy.year
        ).aggregate(total=Sum('valor'))['total'] or 0

    def utilidades_ano(self):
        hoy = date.today()
        return self.utilidad_set.filter(
            fecha__year=hoy.year
        ).aggregate(total=Sum('valor'))['total'] or 0

    def ventas_netas_ano(self):
        hoy = date.today()
        return self.venta_set.filter(
            fecha_venta__year=hoy.year
        ).aggregate(total=Sum('valor_venta'))['total'] or 0

    def perdidas_ano(self):
        hoy = date.today()
        return self.venta_set.filter(
            estado_venta='Perdida',
            fecha_venta__year=hoy.year
        ).aggregate(total=Sum('saldo_actual'))['total'] or 0
        
class Cierre_Caja(models.Model):
    fecha_cierre = models.DateField(auto_now=False)
    valor = models.DecimalField(max_digits=10, decimal_places=0)
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE)
    

    def __str__(self):
        return str(self.fecha_cierre)

class Tienda_Administrador(models.Model):
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE)
    administrador = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return str(self.tienda) + ' - ' + str(self.administrador)



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
        ('Pendiente Pago','Pendiente Pago') ## si se vence el tiempo pasa a este por 3 dias si no se paga se bloquea
    )
    tienda = models.OneToOneField(Tienda, on_delete=models.CASCADE, 
                                    null=False, blank=False)
    membresia = models.ForeignKey(Membresia, on_delete=models.CASCADE)
    fecha_activacion = models.DateField()
    fecha_vencimiento = models.DateField()
    estado = models.CharField(max_length=50, choices=estado_choices, default='Activa')

    def __str__(self):
        return str(self.tienda) + ' - ' + str(self.membresia) 
