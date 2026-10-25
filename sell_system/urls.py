

from django.contrib import admin
from django.urls import path, include

### setings for staticsssss
from django.conf.urls.static import static
from django.conf import settings


from Trabajadores.views import Login, register_user
from Clientes.telegram_webhook import webhook as telegram_clientes_webhook

from Trabajadores.token_views import TokenObtainSeguroView, TokenRefreshSeguroView


urlpatterns = [
    path('admin/', admin.site.urls),
    path('token/', TokenObtainSeguroView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshSeguroView.as_view(), name='token_refresh'),

    path('login/', Login.as_view(), name="login"),
    path('register/',register_user , name="register"),

    path('tiendas/', include('Tiendas.urls')),
    path('trabajadores/', include('Trabajadores.urls')),
    path('clientes/', include('Clientes.urls')),
    path('telegram/clientes/webhook/', telegram_clientes_webhook, name='telegram_clientes_webhook'),
    path('aportes/', include('Aportes.urls')),
    path('gastos/', include('Gastos.urls')),
    path('utilidades/', include('Utilidades.urls')),
    
    path('ventas/', include('Ventas.urls')),
    path('recaudos/', include('Recaudos.urls')),
    path('publicidad/', include('Publicidad.urls')),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

