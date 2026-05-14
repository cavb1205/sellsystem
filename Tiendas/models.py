import secrets
from django.db import models
from django.contrib.auth.models import User
from django.db.models import Sum, Q
from datetime import date, timedelta
from django.utils import timezone


class Tienda(models.Model):
    nombre = models.CharField(max_length=200)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    prefijo_telefono = models.CharField(max_length=5, default='56', blank=True)
    fecha_registro = models.DateField(auto_now_add=True)
    administrador = models.ForeignKey(User, on_delete=models.CASCADE)
    caja_inicial = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    estado = models.BooleanField(default=True)
    cupo_minimo_nuevo = models.DecimalField(max_digits=12, decimal_places=2, default=100000)
    
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['nombre', 'administrador'],
                name='unique_tienda_nombre_por_admin'
            )
        ]

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

    def utilidad_estimada_dia(self):
        hoy = date.today()
        ventas = self.venta_set.filter(fecha_venta=hoy)
        total_utilidad = 0
        
        for venta in ventas:
            # Calculamos la utilidad estimada para cada venta
            utilidad_venta = float(venta.valor_venta) * (float(venta.interes) / 100)
            total_utilidad += utilidad_venta
            
        return total_utilidad

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

    def utilidad_estimada_mes(self):
        hoy = date.today()
        ventas = self.venta_set.filter(
            fecha_venta__year=hoy.year,
            fecha_venta__month=hoy.month
        )
        
        total_utilidad = 0
        
        for venta in ventas:
            # Calculamos la utilidad estimada para cada venta
            utilidad_venta = float(venta.valor_venta) * (float(venta.interes) / 100)
            total_utilidad += utilidad_venta
            
        return total_utilidad

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

    def utilidad_estimada_ano(self):
            hoy = date.today()
            ventas = self.venta_set.filter(
                fecha_venta__year=hoy.year
            )
            
            total_utilidad = 0
            
            for venta in ventas:
                # Calculamos la utilidad estimada para cada venta
                utilidad_venta = float(venta.valor_venta) * (float(venta.interes) / 100)
                total_utilidad += utilidad_venta
                
            return total_utilidad
        
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
        ('Pendiente Pago','Pendiente Pago'),
        ('Pre-activada','Pre-activada'),
    )
    tienda = models.OneToOneField(Tienda, on_delete=models.CASCADE,
                                    null=False, blank=False)
    membresia = models.ForeignKey(Membresia, on_delete=models.CASCADE)
    fecha_activacion = models.DateField()
    fecha_vencimiento = models.DateField()
    estado = models.CharField(max_length=50, choices=estado_choices, default='Activa')
    pre_activada_hasta = models.DateField(null=True, blank=True)

    def __str__(self):
        return str(self.tienda) + ' - ' + str(self.membresia)


def _generar_codigo_solicitud(plan):
    prefijo = 'MM' if plan == 'Mensual' else 'AA'
    while True:
        codigo = f"{prefijo}-{secrets.token_hex(2).upper()}"
        if not SolicitudPago.objects.filter(codigo=codigo).exists():
            return codigo


class SolicitudPago(models.Model):
    ESTADOS = [
        ('pendiente', 'Esperando comprobante'),
        ('pendiente_confirmacion', 'Pre-activada — esperando confirmación'),
        ('procesando', 'Validando'),
        ('aprobada', 'Aprobada'),
        ('confirmada', 'Confirmada'),
        ('pre_aprobada', 'Pre-aprobada (revisión manual)'),
        ('rechazada', 'Rechazada'),
        ('expirada', 'Expirada'),
    ]
    tienda = models.ForeignKey(Tienda, on_delete=models.CASCADE, related_name='solicitudes_pago')
    membresia = models.ForeignKey(Membresia, on_delete=models.PROTECT)
    codigo = models.CharField(max_length=12, unique=True, db_index=True)
    estado = models.CharField(max_length=25, choices=ESTADOS, default='pendiente')

    # Comprobante subido por el usuario en la app
    comprobante = models.ImageField(upload_to='comprobantes/%Y/%m/', null=True, blank=True)
    solicitada_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='solicitudes_pago_creadas'
    )

    # Revisión vía Telegram / panel admin
    telegram_message_id = models.CharField(max_length=50, blank=True)
    revisada_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='solicitudes_pago_revisadas'
    )

    wa_from_number = models.CharField(max_length=30, blank=True)
    wa_message_id = models.CharField(max_length=100, blank=True)
    extraccion_ia = models.JSONField(default=dict, blank=True)
    confianza_ia = models.FloatField(null=True, blank=True)
    referencia_bancaria = models.CharField(max_length=100, blank=True, db_index=True)
    monto_detectado = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    motivo_rechazo = models.TextField(blank=True)

    creada = models.DateTimeField(auto_now_add=True)
    procesada = models.DateTimeField(null=True, blank=True)
    expira = models.DateTimeField()

    def __str__(self):
        return f"{self.codigo} - {self.tienda} ({self.estado})"

    def esta_vigente(self):
        return self.estado == 'pendiente' and timezone.now() < self.expira
