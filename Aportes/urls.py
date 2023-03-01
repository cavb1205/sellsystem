from django.urls import path
from . import views


urlpatterns = [
    path('', views.list_aportes, name='list_aportes'),
    path('list/<str:date>/', views.list_aportes_fecha, name='list_aportes_fecha'),
    path('<int:pk>/', views.get_aporte, name='detail_aporte'),
    path('create/', views.post_aporte, name='create_aporte'),
    path('<int:pk>/update/', views.put_aporte, name='update_aporte'),
    path('<int:pk>/delete/', views.delete_aporte, name='delete_aporte'),
]