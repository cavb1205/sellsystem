from django.urls import path
from . import views

urlpatterns = [
    path('list/<str:date>/t/<str:tienda_id>/', views.list_publicidad_fecha, name='list_publicidad_fecha'),
    path('worker/<str:date>/t/<str:tienda_id>/', views.list_publicidad_fecha_worker, name='list_publicidad_fecha_worker'),
    path('create/t/<str:tienda_id>/', views.create_publicidad, name='create_publicidad'),
    path('<int:pk>/delete/', views.delete_publicidad, name='delete_publicidad'),
]
