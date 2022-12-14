from django.urls import path
from . import views


urlpatterns = [
    
    ### UTILIDADES ####
    path('', views.list_utilidades, name='list_utilidades'),
    path('<int:pk>/', views.get_utilidad, name='detail_utilidad'),
    path('create/', views.post_utilidad, name='create_utilidad'),
    path('<int:pk>/update/', views.put_utilidad, name='update_utilidad'),
    path('<int:pk>/delete/', views.delete_utilidad, name='delete_utilidad'),
]