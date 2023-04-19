from django.urls import path
from . import views


urlpatterns = [
    
    ### UTILIDADES ####
    path('', views.list_utilidades, name='list_utilidades'),
    path('t/<str:tienda_id>/', views.list_utilidades, name='list_utilidades_admin'),
    path('list/<str:date>/', views.list_utilidades_x_fecha, name='list_utilidades_fecha'),
    path('list/<str:date>/t/<str:tienda_id>/', views.list_utilidades_x_fecha, name='list_utilidades_fecha_admin'),
    path('<int:pk>/', views.get_utilidad, name='detail_utilidad'),
    path('create/', views.post_utilidad, name='create_utilidad'),
    path('create/t/<str:tienda_id>/', views.post_utilidad, name='create_utilidad_admin'),
    path('<int:pk>/update/', views.put_utilidad, name='update_utilidad'),
    path('<int:pk>/update/t/<str:tienda_id>/', views.put_utilidad, name='update_utilidad_admin'),
    path('<int:pk>/delete/', views.delete_utilidad, name='delete_utilidad'),
    path('<int:pk>/delete/t/<str:tienda_id>/', views.delete_utilidad, name='delete_utilidad_admin'),
]