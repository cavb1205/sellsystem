"""Throttling para los endpoints públicos sensibles a fuerza bruta/abuso.

Las tasas viven en settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'].
Son por-IP del cliente real.

Importante: detrás de nginx, REMOTE_ADDR es siempre 127.0.0.1, así que un
throttle por REMOTE_ADDR sería global (bloquearía a todos los usuarios juntos).
nginx envía la IP real en `X-Real-IP` (y la sobrescribe con $remote_addr, por
lo que no es falsificable mientras el tráfico entre por nginx, que es el único
camino: gunicorn escucha en 127.0.0.1). Usamos esa cabecera como identificador.
"""
from rest_framework.throttling import AnonRateThrottle


class _RealIPThrottle(AnonRateThrottle):
    def get_ident(self, request):
        real_ip = request.META.get('HTTP_X_REAL_IP')
        if real_ip:
            return real_ip.strip()
        return super().get_ident(request)


class LoginRateThrottle(_RealIPThrottle):
    """Limita intentos de login por IP (anti fuerza bruta de contraseñas)."""
    scope = 'login'


class RegisterRateThrottle(_RealIPThrottle):
    """Limita creación de cuentas/rutas por IP (anti abuso de registro)."""
    scope = 'register'
