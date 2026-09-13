"""Helpers for dates that belong to a route's operating day.

Financial records are stored as date-only values because a sale, collection or
cash closing belongs to a business day.  The server still stores timestamps in
UTC, but when a route needs "today" we resolve it in that route's configured
timezone instead of using the VPS timezone.
"""

from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.utils import timezone


DEFAULT_TIME_ZONE = getattr(settings, 'TIME_ZONE', 'America/Santiago')


def validar_zona_horaria(valor):
    """Return a valid IANA timezone name or ``None`` for invalid input."""
    if not isinstance(valor, str):
        return None
    nombre = valor.strip()
    if not nombre:
        return None
    try:
        ZoneInfo(nombre)
    except ZoneInfoNotFoundError:
        return None
    return nombre


def zona_horaria_tienda(tienda=None):
    """Return the route timezone, falling back safely for legacy rows."""
    nombre = validar_zona_horaria(getattr(tienda, 'zona_horaria', None))
    if nombre:
        return ZoneInfo(nombre)
    try:
        return ZoneInfo(DEFAULT_TIME_ZONE)
    except ZoneInfoNotFoundError:
        return timezone.get_current_timezone()


def fecha_operativa(tienda=None, ahora=None):
    """Return the current calendar date for a route.

    ``tienda`` can be a Tienda instance or ``None``.  ``ahora`` is injectable
    so the rule can be tested around midnight without changing system time.
    """
    ahora = ahora or timezone.now()
    if timezone.is_naive(ahora):
        ahora = timezone.make_aware(ahora, datetime_timezone.utc)
    return timezone.localtime(ahora, zona_horaria_tienda(tienda)).date()


def nombre_zona_horaria(tienda=None):
    """Return the configured IANA name for API/UI responses."""
    valor = getattr(tienda, 'zona_horaria', None)
    return validar_zona_horaria(valor) or DEFAULT_TIME_ZONE
