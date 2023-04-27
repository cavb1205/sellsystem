

from django.contrib import admin
from django.urls import path, include

### setings for staticsssss
from django.conf.urls.static import static
from django.conf import settings


from Trabajadores.views import Login, register_user

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)


urlpatterns = [
    path('admin/', admin.site.urls),
    path('token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    path('login/', Login.as_view(), name="login"),
    path('register/',register_user , name="register"),

    path('tiendas/', include('Tiendas.urls')),
    path('trabajadores/', include('Trabajadores.urls')),
    path('clientes/', include('Clientes.urls')),
    path('aportes/', include('Aportes.urls')),
    path('gastos/', include('Gastos.urls')),
    path('utilidades/', include('Utilidades.urls')),
    
    path('ventas/', include('Ventas.urls')),
    path('recaudos/', include('Recaudos.urls')),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)


