"""Configuración sensible del servicio. No modifica finanzas de las rutas."""
from .models import CambioConfiguracionRoot

CAMPOS_CUENTA = ('banco', 'numero', 'titular', 'tipo')


def snapshot_cuenta(cuenta):
    return {**{key: getattr(cuenta, key) for key in CAMPOS_CUENTA}, 'version': cuenta.version}


def cuenta_completa(cuenta):
    return all(isinstance(getattr(cuenta, key), str) and getattr(cuenta, key).strip()
        for key in CAMPOS_CUENTA)


def motivo_configuracion(data):
    motivo = data.get('motivo', 'Actualización desde el panel root')
    if not isinstance(motivo, str) or not 5 <= len(motivo.strip()) <= 500:
        raise ValueError('Escribe un motivo de entre 5 y 500 caracteres.')
    return motivo.strip()


def registrar_configuracion(tipo, anterior, nuevo, user, motivo):
    return CambioConfiguracionRoot.objects.create(tipo=tipo, anterior=anterior, nuevo=nuevo,
        usuario=user, usuario_nombre=user.username, motivo=motivo)


def historial_configuracion(tipo):
    return list(CambioConfiguracionRoot.objects.filter(tipo=tipo).order_by('-fecha', '-pk')
        .values('id', 'fecha', 'usuario_nombre', 'motivo', 'anterior', 'nuevo')[:20])
