from django.urls import path
from . import views


urlpatterns = [
    
    ### VENTAS ####
    path('list/<str:date>/', views.list_ventas_x_fecha, name='list_ventas_x_fecha'),
    path('list/<str:date>/t/<str:tienda_id>/', views.list_ventas_x_fecha, name='list_ventas_x_fecha_admin'),
    path('list/<str:date1>/<str:date2>/', views.list_ventas_x_fecha_range, name='list_ventas_x_fecha_range'),
    path('list/<str:date1>/<str:date2>/t/<str:tienda_id>/', views.list_ventas_x_fecha_range, name='list_ventas_x_fecha_range_admin'),
    path('activas/', views.list_ventas_activas, name='list_ventas_activas'),
    path('activas/t/<str:tienda_id>/', views.list_ventas_activas, name='list_ventas_activas_admin'),
    path('activas/liquidar/<str:date>/', views.list_ventas_a_liquidar, name='list_ventas_activas_liquidar'),
    path('activas/liquidar/<str:date>/t/<str:tienda_id>/', views.list_ventas_a_liquidar, name='list_ventas_activas_liquidar'),
    path('activas/<int:pk>/', views.list_ventas_activas_cliente, name='list_ventas_activas_cliente'),
    path('activas/<int:pk>/t/<str:tienda_id>/', views.list_ventas_activas_cliente, name='list_ventas_activas_cliente_admin'),
    path('perdidas/', views.list_ventas_perdidas, name='list_ventas_perdidas'),
    path('perdidas/t/<str:tienda_id>/', views.list_ventas_perdidas, name='list_ventas_perdidas_admin'),
    path('<int:pk>/', views.get_venta, name='detail_venta'),
    path('create/', views.post_venta, name='create_venta'),
    path('create/t/<str:tienda_id>/', views.post_venta, name='create_venta_admin'),
    path('<int:pk>/update/', views.put_venta, name='update_venta'),
    path('<int:pk>/update/t/<str:tienda_id>/', views.put_venta, name='update_venta_admin'),
    path('<int:pk>/delete/', views.delete_venta, name='delete_venta'),
    path('<int:pk>/delete/t/<str:tienda_id>/', views.delete_venta, name='delete_venta_admin'),
    path('<int:pk>/perdida/', views.perdida_venta, name='perdida_venta'),
]