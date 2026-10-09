"""Registro transaccional de pérdidas, sin inventar cronología histórica."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from Clientes.models import Cliente
from Recaudos.models import Recaudo
from Tiendas.fecha_operativa import fecha_operativa, nombre_zona_horaria
from Tiendas.models import Tienda
from Tiendas.permissions import usuario_puede_acceder_tienda
from Ventas.models import Venta, DeclaracionPerdidaVenta


def desglose_pendiente(capital, interes, abonos):
    """Capital primero. Los ajustes negativos sí revierten pagos reales."""
    capital = Decimal(capital)
    contratado = capital * Decimal(interes) / Decimal('100')
    netos = Decimal(abonos or 0)
    capital_pendiente = max(capital - max(netos, Decimal('0')), Decimal('0'))
    interes_pendiente = max(contratado - max(netos - capital, Decimal('0')), Decimal('0'))
    return capital_pendiente, interes_pendiente


@transaction.atomic
def declarar_perdida(venta_id, usuario):
    referencia = Venta.objects.filter(pk=venta_id).values('tienda_id').first()
    if not referencia:
        raise NotFound('No se encontró la venta.')
    if not usuario_puede_acceder_tienda(usuario, referencia['tienda_id']):
        raise PermissionDenied('No tiene permiso para acceder a los datos de esta ruta.')
    # Mismo orden que creación, renovación y caja. Serializa los reintentos.
    tienda = Tienda.objects.select_for_update().get(pk=referencia['tienda_id'])
    venta = Venta.objects.select_for_update().filter(pk=venta_id).first()
    if not venta:
        raise NotFound('No se encontró la venta.')
    if venta.tienda_id != tienda.pk:
        raise ValidationError('La ruta del crédito cambió. Consulte nuevamente.')
    if not usuario_puede_acceder_tienda(usuario, tienda.pk):
        raise PermissionDenied('No tiene permiso para acceder a los datos de esta ruta.')
    if venta.estado_venta == 'Perdida':
        # También históricos: pulsar otra vez NO les asigna la fecha de hoy.
        return venta.declaraciones_perdida.order_by('-registrada_en', '-id').first(), False
    if venta.estado_venta == 'Pagado' or venta.saldo_actual is None or venta.saldo_actual <= 0:
        raise ValidationError('Solo se puede declarar pérdida de un crédito con saldo pendiente.')

    netos = Recaudo.objects.filter(venta=venta, es_renovacion=False).aggregate(
        total=Sum('valor_recaudo'),
    )['total'] or Decimal('0')
    capital, interes = desglose_pendiente(venta.valor_venta, venta.interes, netos)
    ahora = timezone.now()
    declaracion = DeclaracionPerdidaVenta.objects.create(
        venta=venta, tienda=tienda, usuario=usuario,
        venta_id_ref=venta.pk, tienda_id_ref=tienda.pk,
        tienda_nombre=tienda.nombre, usuario_nombre=usuario.get_username(),
        registrada_en=ahora, fecha_declaracion=fecha_operativa(tienda, ahora),
        zona_horaria=nombre_zona_horaria(tienda), fecha_venta_original=venta.fecha_venta,
        capital_original=venta.valor_venta,
        interes_contratado=venta.valor_venta * Decimal(venta.interes) / Decimal('100'),
        abonos_reales_netos=netos, saldo_declarado=venta.saldo_actual,
        capital_no_recuperado=capital, interes_no_cobrado=interes,
        saldo_consistente=venta.saldo_actual == capital + interes and netos >= 0,
    )
    cliente = Cliente.objects.select_for_update().get(pk=venta.cliente_id)
    cliente.estado_cliente = 'Bloqueado'
    cliente.save(update_fields=['estado_cliente'])
    venta.estado_venta = 'Perdida'
    venta.comentario = 'Venta en pérdida, cliente bloqueado'
    venta.save(update_fields=['estado_venta', 'comentario'])
    return declaracion, True
