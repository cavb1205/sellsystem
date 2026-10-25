"""La misma política JWT para producción y pruebas aisladas."""
from datetime import timedelta

JWT_SECURITY_SETTINGS = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(minutes=60),
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,
    'UPDATE_LAST_LOGIN': False,
    'CHECK_REVOKE_TOKEN': True,
    'CHECK_USER_IS_ACTIVE': True,
    'TOKEN_REFRESH_SERIALIZER': 'Trabajadores.sesiones.TokenRefreshSeguroSerializer',
}
