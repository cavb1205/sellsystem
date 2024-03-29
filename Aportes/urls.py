from django.urls import path
from . import views


urlpatterns = [
    path('', views.list_aportes, name='list_aportes'),
    path('t/<str:tienda_id>/', views.list_aportes, name='list_aportes_admin'),
    path('list/<str:date>/', views.list_aportes_fecha, name='list_aportes_fecha'),
    path('list/<str:date>/t/<str:tienda_id>/', views.list_aportes_fecha, name='list_aportes_fecha_admin'),
    path('list/<str:date1>/<str:date2>/', views.list_aportes_fecha_range, name='list_aportes_fecha_range'),
    path('list/<str:date1>/<str:date2>/t/<str:tienda_id>/', views.list_aportes_fecha_range, name='list_aportes_fecha_range_admin'),
    path('<int:pk>/', views.get_aporte, name='detail_aporte'),
    path('create/', views.post_aporte, name='create_aporte'),
    path('create/t/<str:tienda_id>/',
         views.post_aporte, name='create_aporte_admin'),
    path('<int:pk>/update/', views.put_aporte, name='update_aporte'),
    path('<int:pk>/update/t/<str:tienda_id>/', views.put_aporte, name='update_aporte_admin'),
    path('<int:pk>/delete/', views.delete_aporte, name='delete_aporte'),
    path('<int:pk>/delete/t/<str:tienda_id>/', views.delete_aporte, name='delete_aporte_admin'),
]
