from django.urls import path
from . import views

urlpatterns = [

    
    ###TIENDA###
    path('list/', views.list_all_tiendas , name='list_tiendas'),
    path('detail/', views.get_tienda_membresia, name='detail_tienda'),
    # path('detail/tienda/', views.get_tienda_membresia, name='tienda_membresia'),
    path('create/', views.post_tienda, name='create_tienda'),
    path('<int:pk>/update/', views.put_tienda, name='update_tienda'),
    path('<int:pk>/delete/', views.delete_tienda, name='delete_tienda'),

    path('cierres/', views.get_cierres_caja, name='get_cierres_caja'),
    path('cierre/<str:fecha>/', views.get_caja_anterior , name='get_caja_anterior'),
    path('cierre/post/<str:fecha>/', views.post_cierre_caja, name='post_cierre_caja'),
    path('cierre/delete/<int:pk>/', views.delete_cierre_caja, name='delete_cierre_caja'),

 
]


