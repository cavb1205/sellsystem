"""Control de acceso a nivel de objeto por tienda (fix C2).

Los endpoints reciben `tienda_id` en la URL y consultaban directamente sin
verificar que la ruta perteneciera al usuario, permitiendo que cualquier
usuario autenticado leyera/modificara datos de rutas ajenas cambiando el id
en la URL (IDOR). Aquí está la lógica central de propiedad y el decorador
que la aplica.

Un usuario puede acceder a una `tienda_id` si:
- es root (`is_superuser`) — necesario para la impersonación del panel admin;
- es el admin dueño de la ruta (`Tienda.administrador`);
- es un admin que maneja la ruta (`Tienda_Administrador`);
- es un trabajador asignado a esa ruta (`Perfil.tienda`).
"""
from functools import wraps

from rest_framework import status
from rest_framework.response import Response

from Tiendas.models import Tienda, Tienda_Administrador


def usuario_puede_acceder_tienda(user, tienda_id):
    """True si `user` tiene relación legítima con `tienda_id`."""
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        tid = int(tienda_id)
    except (TypeError, ValueError):
        return False
    if Tienda.objects.filter(id=tid, administrador=user).exists():
        return True
    if Tienda_Administrador.objects.filter(tienda_id=tid, administrador=user).exists():
        return True
    try:
        return user.perfil.tienda_id == tid
    except Exception:
        return False


def respuesta_sin_permiso():
    return Response(
        {'error': 'No tiene permiso para acceder a los datos de esta ruta.'},
        status=status.HTTP_403_FORBIDDEN,
    )


def requiere_acceso_tienda(view_func):
    """Decorador para vistas con `tienda_id` en la URL.

    Si la URL trae `tienda_id` y el usuario no tiene acceso, devuelve 403.
    Cuando no hay `tienda_id` (el usuario opera sobre su propia ruta vía
    perfil), no impone restricción. Va debajo de `@api_view` para que DRF
    ya haya autenticado al usuario.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        tienda_id = kwargs.get('tienda_id')
        if tienda_id is not None and not usuario_puede_acceder_tienda(request.user, tienda_id):
            return respuesta_sin_permiso()
        return view_func(request, *args, **kwargs)
    return wrapper
