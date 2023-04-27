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
    path('<int:pk>/delete/', views.delete_tienda, name='delete_tienda'),

    path('cierres/', views.get_cierres_caja, name='get_cierres_caja'),
    path('cierres/t/<str:tienda_id>/', views.get_cierres_caja, name='get_cierres_caja_admin'),
    path('cierre/<str:fecha>/', views.get_caja_anterior , name='get_caja_anterior'),
    path('cierre/<str:fecha>/t/<str:tienda_id>/', views.get_caja_anterior , name='get_caja_anterior_admin'),
    path('cierre/post/<str:fecha>/', views.post_cierre_caja, name='post_cierre_caja'),
    path('cierre/post/<str:fecha>/t/<str:tienda_id>/', views.post_cierre_caja, name='post_cierre_caja_admin'),
    path('cierre/delete/<int:pk>/', views.delete_cierre_caja, name='delete_cierre_caja'),

    path('activate/mounth/<str:pk>/', views.activar_membresia_mensual, name='activar_membresia_mensual'),
    path('activate/year/<str:pk>/', views.activar_membresia_ano, name='activar_membresia_ano'),
]


