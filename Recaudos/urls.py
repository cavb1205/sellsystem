from django.urls import path
from . import views



urlpatterns = [
    
    ### RECAUDOS ####
    path('', views.list_recaudos, name='list_recaudos'),
    path('list/<int:venta_id>/', views.list_recaudos_venta, name='list_recaudos_venta'),
    path('list/<str:date>/', views.list_recaudos_fecha, name='list_recaudos_fecha'),
    path('list/<str:date>/t/<str:tienda_id>/', views.list_recaudos_fecha, name='list_recaudos_fecha_admin'),
    path('<int:pk>/', views.get_recaudo, name='detail_recaudo'),
    path('create/', views.post_recaudo, name='create_recaudo'),
    path('create/t/<str:tienda_id>/', views.post_recaudo, name='create_recaudo_admin'),
    path('create/nopay/', views.post_recaudo_no_pay, name='create_recaudo_no_pay'),
    path('create/nopay/t/<str:tienda_id>/', views.post_recaudo_no_pay, name='create_recaudo_no_pay_admin'),
    path('<int:pk>/update/', views.put_recaudo, name='update_recaudo'),
    path('<int:pk>/update/t/<str:tienda_id>/', views.put_recaudo, name='update_recaudo_admin'),
    path('<int:pk>/delete/', views.delete_recaudo, name='delete_recaudo'),
]