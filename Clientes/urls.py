from django.urls import path
from . import views
from .views import ClientesListAPIView

urlpatterns = [

    
    ###CLIENTES###
    path('', views.list_clientes , name='list_clientes'),
    path('disponibles/', views.list_clientes_disponibles , name='list_clientes_disponibles'),
    path('activos/', views.list_clientes_activos , name='list_clientes_activos'),
    path('<int:pk>/', views.get_cliente, name='detail_cliente'),
    path('create/', views.post_cliente, name='create_cliente'),
    path('<int:pk>/update/', views.put_cliente, name='update_cliente'),
    path('<int:pk>/delete/', views.delete_cliente, name='delete_cliente'),

]