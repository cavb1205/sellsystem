from django.urls import path
from . import views

urlpatterns = [

    
    ###TIENDA###
    path('', views.list_trabajadores , name='list_trabajadores'),
    path('t/<str:tienda_id>/', views.list_trabajadores , name='list_trabajadores_admin'),
    path('<int:pk>/', views.get_trabajador, name='detail_trabajador'),
    path('create/', views.post_trabajador, name='create_trabajador'),
    path('create/t/<str:tienda_id>/', views.post_trabajador, name='create_trabajador_admin'),
    path('<int:pk>/update/', views.put_trabajador, name='update_trabajador'),
    path('<int:pk>/delete/', views.delete_trabajador, name='delete_trabajador'),    
    path('password/<int:pk>/', views.update_password, name='update_password'),

   

]


