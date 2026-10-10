"""Bloqueo operativo administrativo. No altera los vencimientos ordinarios.

Se aplica tras la autenticación DRF, incluso en sesiones ya abiertas; no cierra
la cuenta ni impide seleccionar otra ruta o tramitar una renovación.
"""
from django.apps import apps
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated


class IsAuthenticatedOperativo(IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.user.is_superuser:
            return True
        modulo = view.__class__.__module__.split('.')[0]
        nombre = view.__class__.__name__
        modelos = {'Clientes': 'Cliente', 'Ventas': 'Venta', 'Recaudos': 'Recaudo',
            'Gastos': 'Gasto', 'Aportes': 'Aporte', 'Utilidades': 'Utilidad',
            'Publicidad': 'Publicidad', 'Trabajadores': 'Perfil'}
        if modulo == 'Tiendas':
            # Los detalles/listados de membresía y las solicitudes siguen
            # disponibles para consultar el estado y renovar.
            operativas = ('dashboard_movimientos', 'cierre_resumen_movimientos',
                'comparativo_mensual', 'reporte_utilidad_diario', 'get_cierres_caja',
                'list_movimientos_caja', 'get_caja_anterior', 'post_cierre_caja',
                'delete_cierre_caja', 'auditoria_inconsistencias_tienda', 'recorrido_ruta',
                'put_tienda', 'patch_tienda_settings', 'delete_tienda')
            if nombre not in operativas:
                return True
        elif modulo == 'Trabajadores':
            if nombre not in ('list_trabajadores', 'get_trabajador', 'put_trabajador',
                'post_trabajador', 'delete_trabajador', 'candidatos_asignacion',
                'asignar_trabajador_ruta'):
                return True
        elif modulo not in modelos:
            return True
        kwargs = view.kwargs
        ids = set()

        def agregar(value):
            try:
                if value is not None and not isinstance(value, bool):
                    ids.add(int(value))
            except (TypeError, ValueError):
                pass  # El endpoint conserva la validación habitual.

        agregar(kwargs.get('tienda_id'))
        venta_id = kwargs.get('venta_id')
        if venta_id is not None:
            agregar(apps.get_model('Ventas', 'Venta').objects.filter(pk=venta_id)
                .values_list('tienda_id', flat=True).first())
        pk = kwargs.get('pk')
        if pk is not None:
            if modulo == 'Tiendas' and nombre in ('put_tienda', 'patch_tienda_settings', 'delete_tienda'):
                agregar(pk)
            else:
                app = modulo
                modelo = modelos.get(modulo)
                if modulo == 'Tiendas':
                    modelo = 'Cierre_Caja'
                elif modulo == 'Ventas' and nombre == 'list_ventas_activas_cliente':
                    app, modelo = 'Clientes', 'Cliente'
                elif modulo == 'Gastos' and 'tipo_gasto' in nombre:
                    modelo = None  # Los tipos de gasto son un catálogo común.
                if modelo:
                    agregar(apps.get_model(app, modelo).objects.filter(pk=pk)
                        .values_list('tienda_id', flat=True).first())
        if not ids:
            agregar(apps.get_model('Trabajadores', 'Perfil').objects.filter(trabajador_id=request.user.pk)
                .values_list('tienda_id', flat=True).first())
        if ids and apps.get_model('Tiendas', 'Tienda_Membresia').objects.filter(
            tienda_id__in=ids, vencida_manualmente=True,
        ).exists():
            raise PermissionDenied({'error': 'Esta ruta está vencida por una corrección administrativa. Puedes revisar tu membresía o seleccionar otra ruta.',
                'code': 'membresia_vencida_manual'})
        return True
