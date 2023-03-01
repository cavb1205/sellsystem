from django.db import models
from django.db.models import Sum
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

    def inversion(self):
        tienda = Tienda.objects.get(id=self.id)
        aportes = tienda.aporte_set.filter(tienda=tienda)
        total_aportes = 0
        for aporte in aportes:
            total_aportes = total_aportes + int(aporte.valor)
        return total_aportes

    def gastos(self):
        tienda = Tienda.objects.get(id=self.id)
        gastos = tienda.gasto_set.filter(tienda=tienda)
        total_gastos = 0
        for gasto in gastos:
            total_gastos = total_gastos + int(gasto.valor)
        return total_gastos

    def utilidades(self):
        tienda = Tienda.objects.get(id=self.id)
        utilidades = tienda.utilidad_set.filter(tienda=tienda)
        total_utilidades = 0
        for utilidad in utilidades:
            total_utilidades = total_utilidades + int(utilidad.valor)
        return total_utilidades

    def perdidas(self):
        tienda = Tienda.objects.get(id=self.id)
        perdidas = tienda.venta_set.filter(tienda=tienda).filter(estado_venta='Perdida')
        total_perdidas = 0
        for perdida in perdidas:
            total_perdidas = total_perdidas + int(perdida.saldo_actual)
        return total_perdidas

    def ingresos_x_ventas(self):
        tienda = Tienda.objects.get(id=self.id)
        ventas_pagas = tienda.venta_set.filter(tienda=tienda).filter(estado_venta='Pagado')
        total = 0
        for venta in ventas_pagas:
            total = total + (int(venta.total_a_pagar()) - int(venta.valor_venta))
        return total

    def dinero_x_cobrar(self):
        tienda = Tienda.objects.get(id=self.id)
        ventas_activas = tienda.venta_set.filter(tienda=tienda).exclude(estado_venta='Pagado').exclude(estado_venta='Perdida')
        total = 0
        for venta in ventas_activas:
            total = total + int(venta.saldo_actual)
        return total

    ### Calculamos lo correspondiente al día actual ###
    def aportes_dia(self):
        tienda = Tienda.objects.get(id=self.id)
        aportes = tienda.aporte_set.filter(tienda=tienda).filter(fecha=datetime.today())
        total = 0
        for aporte in aportes:
            total = total + int(aporte.valor)
        return total

    def gastos_dia(self):
        tienda = Tienda.objects.get(id=self.id)
        gastos = tienda.gasto_set.filter(tienda=tienda).filter(fecha=datetime.today())
        total = 0
        for gasto in gastos:
            total = total + int(gasto.valor)
        return total

    def utilidades_dia(self):
        tienda = Tienda.objects.get(id=self.id)
        utilidades = tienda.utilidad_set.filter(tienda=tienda).filter(fecha=datetime.today())
        total = 0
        for utilidad in utilidades:
            total = total + int(utilidad.valor)
        return total

    def recaudos_dia(self):
        tienda = Tienda.objects.get(id=self.id)
        recaudos = tienda.recaudo_set.filter(tienda=tienda).filter(fecha_recaudo=datetime.today())
        total = 0
        for recaudo in recaudos:
            total = total + int(recaudo.valor_recaudo)
        return total

    def ventas_netas_dia(self):
        tienda = Tienda.objects.get(id=self.id)
        ventas = tienda.venta_set.filter(tienda=tienda).filter(fecha_venta=datetime.today())
        total = 0
        for venta in ventas:
            total = total + int(venta.valor_venta)
        return total

    ### Calculamos lo correspondiente al mes actual  ####
    def aportes_mes(self):
        tienda = Tienda.objects.get(id=self.id)
        aportes = tienda.aporte_set.filter(tienda=tienda).filter(fecha__month=datetime.today().month)
        total = 0
        for aporte in aportes:
            total = total + int(aporte.valor)
        return total

    def gastos_mes(self):
        tienda = Tienda.objects.get(id=self.id)
        gastos = tienda.gasto_set.filter(tienda=tienda).filter(fecha__month=datetime.today().month)
        total = 0
        for gasto in gastos:
            total = total + int(gasto.valor)
        return total

    def utilidades_mes(self):
        tienda = Tienda.objects.get(id=self.id)
        utilidades = tienda.utilidad_set.filter(tienda=tienda).filter(fecha__month=datetime.today().month)
        total = 0
        for utilidad in utilidades:
            total = total + int(utilidad.valor)
        return total

    def ventas_netas_mes(self):
        tienda = Tienda.objects.get(id=self.id)
        ventas = tienda.venta_set.filter(tienda=tienda).filter(fecha_venta__month=datetime.today().month)
        total = 0
        for venta in ventas:
            total = total + int(venta.valor_venta)
        return total

    ### Calculamos lo correspondiente al ultimo año ###
    def aportes_ano(self):
        tienda = Tienda.objects.get(id=self.id)
        aportes = tienda.aporte_set.filter(tienda=tienda).filter(fecha__year=datetime.today().year)
        total = 0
        for aporte in aportes:
            total = total + int(aporte.valor)
        return total

    def gastos_ano(self):
        tienda = Tienda.objects.get(id=self.id)
        gastos = tienda.gasto_set.filter(tienda=tienda).filter(fecha__year=datetime.today().year)
        total = 0
        for gasto in gastos:
            total = total + int(gasto.valor)
        return total

    def utilidades_ano(self):
        tienda = Tienda.objects.get(id=self.id)
        utilidades = tienda.utilidad_set.filter(tienda=tienda).filter(fecha__year=datetime.today().year)
        total = 0
        for utilidad in utilidades:
            total = total + int(utilidad.valor)
        return total

    def ventas_netas_ano(self):
        tienda = Tienda.objects.get(id=self.id)
        ventas = tienda.venta_set.filter(tienda=tienda).filter(fecha_venta__year=datetime.today().year)
        total = 0
        for venta in ventas:
            total = total + int(venta.valor_venta)
        return total

    def perdidas_ano(self):
        tienda = Tienda.objects.get(id=self.id)
        perdidas = tienda.venta_set.filter(tienda=tienda).filter(estado_venta='Perdida').filter(fecha_venta__year=datetime.today().year)
        total = 0
        for perdida in perdidas:
            total = total + int(perdida.saldo_actual)
        return total
        
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