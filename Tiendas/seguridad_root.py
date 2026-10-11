"""Política única de eliminación: ante historial, archivar en vez de borrar."""
from django.db.models import Exists, OuterRef
from .models import AlertaOperativa, Tienda

RELACIONES_ESTRUCTURALES = {'Tiendas.Tienda_Membresia', 'Tiendas.Tienda_Administrador'}


def relaciones_protegidas():
    for relacion in Tienda._meta.related_objects:
        if relacion.related_model._meta.label not in RELACIONES_ESTRUCTURALES:
            yield relacion.related_model, relacion.field.name


def anotar_historial_ruta(queryset):
    condicion = Exists(AlertaOperativa.objects.filter(tienda_id_ref=OuterRef('tienda_id')))
    for model, field in relaciones_protegidas():
        condicion = condicion | Exists(model._default_manager.filter(**{field: OuterRef('tienda_id')}))
    return queryset.annotate(_historial_protegido=condicion)


def puede_eliminar_desde_lista(tm):
    # Solo la lista anotada puede ofrecer el botón; no inferir desde clientes/ventas.
    return (getattr(tm, '_historial_protegido', True) is False
        and tm.tienda.caja_inicial == 0 and tm.version == 0)


def tiene_historial_protegido(tienda):
    if tienda.caja_inicial != 0:
        return True
    if hasattr(tienda, 'tienda_membresia') and tienda.tienda_membresia.version > 0:
        return True
    if AlertaOperativa.objects.filter(tienda_id_ref=tienda.pk).exists():
        return True
    return any(model._default_manager.filter(**{field: tienda}).exists()
        for model, field in relaciones_protegidas())
