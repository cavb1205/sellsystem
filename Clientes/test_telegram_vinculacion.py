from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from Clientes.models import Cliente
from Clientes.telegram_models import TelegramEvento, TelegramInvitacion
from Clientes.telegram_vinculacion import (
    aceptar_invitacion, cliente_autorizado, confirmar_vinculo,
    crear_invitacion, revocar_vinculo,
)
from Tiendas.models import Tienda, Tienda_Administrador
from Trabajadores.models import Perfil


class VinculacionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create(username='cavb1205', is_staff=True)
        self.otro = User.objects.create(username='otro', is_staff=True)
        self.clientes = []
        for nombre in ('R1', 'R2'):
            ruta = Tienda.objects.create(nombre=nombre, administrador=self.admin)
            Tienda_Administrador.objects.create(tienda=ruta, administrador=self.admin)
            self.clientes.append(Cliente.objects.create(
                tienda=ruta, identificacion='123', nombres='Camilo',
                apellidos='Prueba', telefono_principal='123', direccion='Prueba'))

    def vincular(self, cliente, chat=12345):
        token, _ = crear_invitacion(cliente.pk, self.admin)
        return aceptar_invitacion(token, chat, consentimiento=True)

    def test_mismo_chat_dos_rutas_requiere_dos_vinculaciones(self):
        self.vincular(self.clientes[0])
        with self.assertRaises(PermissionDenied):
            cliente_autorizado(12345, self.clientes[1].pk)
        self.vincular(self.clientes[1])
        for cliente in self.clientes:
            self.assertEqual(cliente_autorizado(12345, cliente.pk), cliente)

    def test_hash_consentimiento_activacion_inmediata_y_un_solo_uso(self):
        cliente = self.clientes[0]
        token, _ = crear_invitacion(cliente.pk, self.admin)
        self.assertNotEqual(TelegramInvitacion.objects.get().token_hash, token)
        with self.assertRaises(ValidationError):
            aceptar_invitacion(token, 12345)
        vinculo = aceptar_invitacion(token, 12345, True)
        self.assertTrue(vinculo.activo)
        self.assertIsNotNone(vinculo.consentimiento_en)
        self.assertIsNotNone(vinculo.confirmado_en)
        self.assertIsNone(vinculo.confirmado_por_id)
        self.assertEqual(cliente_autorizado(12345, cliente.pk), cliente)
        with self.assertRaises(ValidationError):
            aceptar_invitacion(token, 99999, True)
        with self.assertRaises(PermissionDenied):
            cliente_autorizado(99999, cliente.pk)

    def test_admin_puede_resolver_vinculo_pendiente_legacy(self):
        cliente = self.clientes[0]
        token, _ = crear_invitacion(cliente.pk, self.admin)
        vinculo = aceptar_invitacion(token, 12345, True)
        # Compatibilidad con vinculaciones pendientes creadas por la versión
        # anterior, que sí requería aprobación manual.
        vinculo.activo = False
        vinculo.confirmado_en = None
        vinculo.save(update_fields=['activo', 'confirmado_en'])
        confirmar_vinculo(cliente.pk, self.admin, 12345, vinculo.version)
        self.assertEqual(cliente_autorizado(12345, cliente.pk), cliente)

    def test_expiracion_y_rotacion(self):
        token, _ = crear_invitacion(self.clientes[0].pk, self.admin)
        nuevo, _ = crear_invitacion(self.clientes[0].pk, self.admin)
        with self.assertRaises(ValidationError):
            aceptar_invitacion(token, 12345, True)
        TelegramInvitacion.objects.update(expira_en=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(ValidationError):
            aceptar_invitacion(nuevo, 12345, True)

    def test_ajeno_y_grupos_no_autorizados(self):
        with self.assertRaises(PermissionDenied):
            crear_invitacion(self.clientes[0].pk, self.otro)
        token, _ = crear_invitacion(self.clientes[0].pk, self.admin)
        with self.assertRaises(ValidationError):
            aceptar_invitacion(token, -12345, True)

    def test_admin_de_otra_ruta_puede_vincular_su_cliente(self):
        ruta = Tienda.objects.create(nombre='Ruta Otro Admin', administrador=self.otro)
        Tienda_Administrador.objects.create(tienda=ruta, administrador=self.otro)
        cliente = Cliente.objects.create(
            tienda=ruta, identificacion='456', nombres='Otro',
            apellidos='Administrador', telefono_principal='456', direccion='Prueba')

        token, _ = crear_invitacion(cliente.pk, self.otro)
        aceptar_invitacion(token, 54321, consentimiento=True)

        self.assertEqual(cliente_autorizado(54321, cliente.pk), cliente)

    def test_trabajador_de_la_ruta_puede_gestionar_vinculo(self):
        trabajador = User.objects.create(username='cobrador')
        Perfil.objects.create(
            trabajador=trabajador, tienda=self.clientes[0].tienda,
            identificacion='789', telefono='789')

        token, _ = crear_invitacion(self.clientes[0].pk, trabajador)
        aceptar_invitacion(token, 67890, consentimiento=True)
        self.assertEqual(cliente_autorizado(67890, self.clientes[0].pk), self.clientes[0])

        revocar_vinculo(self.clientes[0].pk, trabajador)
        with self.assertRaises(PermissionDenied):
            cliente_autorizado(67890, self.clientes[0].pk)

        with self.assertRaises(PermissionDenied):
            crear_invitacion(self.clientes[1].pk, trabajador)

    def test_revocacion_cancela_eventos_y_exige_nueva_aceptacion(self):
        cliente = self.clientes[0]
        vinculo = self.vincular(cliente)
        evento = TelegramEvento.objects.create(vinculo=vinculo,
            version_vinculo=vinculo.version, clave='pago:1', tipo='pago', credito_id_ref=1)
        revocar_vinculo(cliente.pk, self.admin)
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'cancelado')
        with self.assertRaises(PermissionDenied):
            cliente_autorizado(12345, cliente.pk)
        token, _ = crear_invitacion(cliente.pk, self.admin)
        nuevo = aceptar_invitacion(token, 88888, True)
        self.assertEqual(cliente_autorizado(88888, cliente.pk), cliente)
        with self.assertRaises(ValidationError):
            confirmar_vinculo(cliente.pk, self.admin, 12345, vinculo.version)
        self.assertNotEqual(vinculo.version, nuevo.version)

    def test_desvincular_ruta_restringe_consultas(self):
        cliente = self.clientes[0]
        self.vincular(cliente)
        Tienda_Administrador.objects.filter(tienda=cliente.tienda).delete()
        with self.assertRaises(PermissionDenied):
            cliente_autorizado(12345, cliente.pk)
