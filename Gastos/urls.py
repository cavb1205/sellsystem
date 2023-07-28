from django.urls import path
from . import views


urlpatterns = [

    
    ### TIPO GASTOS ####
    path('tipo/', views.list_tipo_gastos, name='list_tipo_gastos'),
    path('tipo/<int:pk>/', views.get_tipo_gasto, name='detail_tipo_gasto'),
    path('tipo/create/', views.post_tipo_gasto, name='create_tipo_gasto'),
    path('tipo/<int:pk>/update/', views.put_tipo_gasto, name='update_tipo_gasto'),
    path('tipo/<int:pk>/delete/', views.delete_tipo_gasto, name='delete_tipo_gasto'),
    
    ### GASTOS ####
    path('', views.list_gastos, name='list_gastos'),
    path('t/<str:tienda_id>/', views.list_gastos, name='list_gastos_admin'),
    path('list/<str:date>/', views.list_gastos_x_fecha, name='list_gastos_x_fecha'),
    path('list/<str:date>/t/<str:tienda_id>/', views.list_gastos_x_fecha, name='list_gastos_x_fecha_admin'),
    path('list/<str:date1>/<str:date2>/', views.list_gastos_x_fecha_range, name='list_gastos_x_fecha_range'),
    path('list/<str:date1>/<str:date2>/t/<str:tienda_id>/', views.list_gastos_x_fecha_range, name='list_gastos_x_fecha_range_admin'),
    path('<int:pk>/', views.get_gasto, name='detail_gasto'),
    path('create/', views.post_gasto, name='create_gasto'),
    path('create/t/<str:tienda_id>/', views.post_gasto, name='create_gasto_admin'),
    path('<int:pk>/update/', views.put_gasto, name='update_gasto'),
    path('<int:pk>/update/t/<str:tienda_id>/', views.put_gasto, name='update_gasto_admin'),
    path('<int:pk>/delete/', views.delete_gasto, name='delete_gasto'),
    path('<int:pk>/delete/t/<str:tienda_id>/', views.delete_gasto, name='delete_gasto_admin'),
]