from django.urls import path
from . import views


urlpatterns = [
    
    ### VENTAS ####
    path('', views.list_ventas_all, name='list_ventas_all'),
    path('activas/', views.list_ventas_activas, name='list_ventas_activas'),
    path('activas/liquidar/<str:date>/', views.list_ventas_a_liquidar, name='list_ventas_activas_liquidar'),
    path('activas/<int:pk>/', views.list_ventas_activas_cliente, name='list_ventas_activas_cliente'),
    path('pagas/', views.list_ventas_pagas, name='list_ventas_pagas'),
    path('<int:pk>/', views.get_venta, name='detail_venta'),
    path('create/', views.post_venta, name='create_venta'),
    path('<int:pk>/update/', views.put_venta, name='update_venta'),
    path('<int:pk>/delete/', views.delete_venta, name='delete_venta'),
]