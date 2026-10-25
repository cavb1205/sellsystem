"""Controles comunes para emitir y renovar sesiones, sin registrar tokens."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import F
from django.utils.crypto import constant_time_compare
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.utils import get_md5_hash_password

from Trabajadores.models import AsignacionTrabajador, Perfil


def perfil_login_permitido(user):
    perfil = Perfil.objects.select_related('tienda').filter(trabajador=user).first()
    if perfil is None:
        raise ValidationError({'error': 'Tu usuario no tiene un perfil de ruta asociado. Contacta al soporte.'})
    if not (user.is_staff or user.is_superuser):
        asignaciones = AsignacionTrabajador.objects.filter(perfil=perfil)
        activa = asignaciones.filter(
            activa=True, tienda__estado=True,
            tienda__administrador_id=F('perfil__tienda__administrador_id'),
        ).exists()
        if not activa and (asignaciones.exists() or not perfil.tienda.estado):
            raise PermissionDenied({'error': 'Tu usuario no tiene rutas activas asignadas. Contacta al administrador.'})
    return perfil


class TokenRefreshSeguroSerializer(TokenRefreshSerializer):
    """La renovación verifica también la contraseña vigente y los permisos."""
    def validate(self, attrs):
        refresh = self.token_class(attrs['refresh'])
        user_id = refresh.get(api_settings.USER_ID_CLAIM)
        if not isinstance(user_id, (str, int)) or isinstance(user_id, bool):
            raise AuthenticationFailed('La sesión no es válida.', code='token_not_valid')
        User = get_user_model()
        try:
            user = User.objects.get(**{api_settings.USER_ID_FIELD: user_id})
        except (User.DoesNotExist, DjangoValidationError, ValueError, TypeError):
            raise AuthenticationFailed('La sesión no es válida.', code='token_not_valid')
        if not user.is_active:
            raise AuthenticationFailed('Tu cuenta está desactivada.', code='user_inactive')
        fingerprint = refresh.get(api_settings.REVOKE_TOKEN_CLAIM)
        if not isinstance(fingerprint, str) or not constant_time_compare(
            fingerprint, get_md5_hash_password(user.password),
        ):
            raise AuthenticationFailed('La contraseña cambió. Inicia sesión de nuevo.', code='password_changed')
        perfil_login_permitido(user)
        return super().validate(attrs)
