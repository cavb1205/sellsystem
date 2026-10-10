"""Orden operativo únicamente: no modifica clientes, créditos ni recaudos."""
from django.db import transaction
from django.db.models import Min
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from Clientes.models import Cliente
from Ventas.models import Venta
from Tiendas.models import Tienda, OrdenVisita, CambioOrdenVisita
from Tiendas.permissions import requiere_acceso_tienda


def clientes_activos(tienda_id):
    # El orden inicial reproduce la primera venta activa de cada cliente.
    return list(Venta.objects.filter(tienda_id=tienda_id, cliente__tienda_id=tienda_id)
                .exclude(estado_venta__in=['Pagado', 'Perdida'])
                .values('cliente_id').annotate(primera=Min('id'))
                .order_by('primera', 'cliente_id').values_list('cliente_id', flat=True))


def orden_activo(guardado, activos):
    conjunto = set(activos)
    presentes = [pk for pk in guardado if pk in conjunto]
    usados = set(presentes)
    return presentes + [pk for pk in activos if pk not in usados]


def combinar_orden(guardado, solicitado):
    """Reemplaza solo posiciones activas; los clientes ausentes conservan su lugar."""
    activos = set(solicitado)
    pendientes = iter(solicitado)
    resultado = [next(pendientes) if pk in activos else pk for pk in guardado]
    return resultado + list(pendientes)


def documento_orden(tienda_id, configuracion=None, activos=None):
    activos = clientes_activos(tienda_id) if activos is None else activos
    clientes = Cliente.objects.filter(tienda_id=tienda_id, id__in=activos).in_bulk()
    orden = orden_activo(configuracion.clientes if configuracion else [], activos)
    return {
        'tienda': int(tienda_id), 'configurado': configuracion is not None,
        'version': configuracion.version if configuracion else 0,
        'actualizado_en': configuracion.actualizado_en.isoformat() if configuracion else None,
        'actualizado_por': configuracion.actualizado_por_nombre if configuracion else '',
        'clientes': [{'id': pk, 'nombres': clientes[pk].nombres,
                      'apellidos': clientes[pk].apellidos, 'direccion': clientes[pk].direccion}
                     for pk in orden if pk in clientes],
    }


def ordenar_ventas_recorrido(ventas, tienda_id):
    configuracion = OrdenVisita.objects.filter(tienda_id=tienda_id).first()
    if configuracion is None:
        return ventas  # Sin configurar: conservar exactamente el listado anterior.
    orden = orden_activo(configuracion.clientes, clientes_activos(tienda_id))
    posiciones = {pk: indice for indice, pk in enumerate(orden)}
    # Estable: los créditos de un mismo cliente mantienen su orden por ID.
    return sorted(ventas, key=lambda venta: posiciones.get(venta.cliente_id, len(posiciones)))


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
@requiere_acceso_tienda
def recorrido_ruta(request, tienda_id):
    if request.method == 'GET':
        configuracion = OrdenVisita.objects.filter(tienda_id=tienda_id).first()
        return Response(documento_orden(tienda_id, configuracion), headers={'Cache-Control': 'no-store'})

    version = request.data.get('version')
    solicitado = request.data.get('clientes')
    if (type(version) is not int or version < 0 or not isinstance(solicitado, list)
            or any(type(pk) is not int or pk <= 0 for pk in solicitado)
            or len(solicitado) != len(set(solicitado))):
        return Response({'error': 'Envía una versión válida y una lista de clientes sin duplicados.'}, status=400)

    with transaction.atomic():
        # Bloquear la ruta también serializa dos primeros guardados sin configuración.
        Tienda.objects.select_for_update().get(pk=tienda_id)
        configuracion = OrdenVisita.objects.filter(tienda_id=tienda_id).first()
        actual = configuracion.version if configuracion else 0
        if version != actual:
            return Response({'error': 'Otra persona cambió el recorrido. Recarga el orden antes de guardar.'}, status=409)
        propios = set(Cliente.objects.filter(tienda_id=tienda_id, pk__in=solicitado).values_list('pk', flat=True))
        if propios != set(solicitado):
            return Response({'error': 'La lista contiene clientes que no pertenecen a esta ruta.'}, status=400)
        activos = clientes_activos(tienda_id)
        if set(solicitado) != set(activos):
            return Response({'error': 'Los clientes activos cambiaron. Recarga el orden antes de guardar.'}, status=409)
        anterior = configuracion.clientes if configuracion else []
        nuevo = combinar_orden(anterior, solicitado)
        if configuracion is None or nuevo != anterior:
            nombre = (request.user.get_full_name().strip() or request.user.username)[:200]
            configuracion, _ = OrdenVisita.objects.update_or_create(tienda_id=tienda_id, defaults={
                'clientes': nuevo, 'version': actual + 1,
                'actualizado_en': timezone.now(), 'actualizado_por_nombre': nombre})
            CambioOrdenVisita.objects.create(tienda_id=tienda_id, version=actual + 1,
                anterior=anterior, nuevo=nuevo, usuario=request.user, usuario_nombre=nombre)
        return Response(documento_orden(tienda_id, configuracion, activos), headers={'Cache-Control': 'no-store'})
