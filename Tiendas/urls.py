from django.urls import path
from . import views

urlpatterns = [

    
    ###TIENDA###
    path('list/', views.list_all_tiendas , name='list_tiendas'),
    path('list/admin/', views.list_tiendas_admin, name='list_tiendas_admin'),
    path('detail/', views.get_tienda_membresia, name='detail_tienda'),
    path('detail/admin/<str:pk>/', views.get_tienda_membresia_admin, name='tienda_membresia_admin'),
    path('create/', views.post_tienda, name='create_tienda'),
    path('<int:pk>/update/', views.put_tienda, name='update_tienda'),
    path('<int:pk>/settings/', views.patch_tienda_settings, name='patch_tienda_settings'),
    path('<int:pk>/delete/', views.delete_tienda, name='delete_tienda'),
    path('<int:pk>/admin/delete/', views.delete_tienda_root, name='delete_tienda_root'),
    path('<int:pk>/admin/remove/', views.remove_tienda_admin, name='remove_tienda_admin'),
    path('list/tiendas/admin/', views.get_tiendas_admin, name='get_tiendas_admin'),

    path('cierres/', views.get_cierres_caja, name='get_cierres_caja'),
    path('cierres/t/<str:tienda_id>/', views.get_cierres_caja, name='get_cierres_caja_admin'),
    path('cierre/<str:fecha>/', views.get_caja_anterior , name='get_caja_anterior'),
    path('cierre/<str:fecha>/t/<str:tienda_id>/', views.get_caja_anterior , name='get_caja_anterior_admin'),
    path('cierre/post/<str:fecha>/', views.post_cierre_caja, name='post_cierre_caja'),
    path('cierre/post/<str:fecha>/t/<str:tienda_id>/', views.post_cierre_caja, name='post_cierre_caja_admin'),
    path('cierre/delete/<int:pk>/', views.delete_cierre_caja, name='delete_cierre_caja'),

    path('activate/mounth/<str:pk>/', views.activar_membresia_mensual, name='activar_membresia_mensual'),
    path('activate/year/<str:pk>/', views.activar_membresia_ano, name='activar_membresia_ano'),

    # Membresías — solicitud de pago + bot de Telegram
    path('solicitar-pago/', views.solicitar_pago, name='solicitar_pago'),
    path('solicitud-pago/<str:codigo>/', views.consultar_solicitud, name='consultar_solicitud'),
    path('solicitud-pago/<str:codigo>/comprobante/', views.adjuntar_comprobante, name='adjuntar_comprobante'),
    path('telegram/webhook/', views.telegram_webhook, name='telegram_webhook'),
    path('solicitudes/revision/', views.listar_solicitudes_revision, name='listar_solicitudes_revision'),
    path('solicitud/<str:codigo>/revisar/', views.revisar_solicitud_admin, name='revisar_solicitud_admin'),
    path('solicitud/<str:codigo>/comprobante/ver/', views.ver_comprobante, name='ver_comprobante'),
    path('cuenta-destino/', views.cuenta_destino, name='cuenta_destino'),

    # Informe de ingresos por membresías (solo root)
    path('ingresos/', views.ingresos_membresias, name='ingresos_membresias'),

    # Planes de membresía y precios (solo root)
    path('planes/', views.planes, name='planes'),

    # Panel de administración del root — KPIs globales
    path('admin/resumen/', views.admin_resumen, name='admin_resumen'),

    # Archivar / desarchivar una ruta (solo root)
    path('<int:pk>/archivar/', views.archivar_ruta, name='archivar_ruta'),
]


