from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from Trabajadores.serializers import UserTokenLoginObtainSerializer
from Trabajadores.sesiones import TokenRefreshSeguroSerializer
from Trabajadores.throttles import LoginRateThrottle, RefreshRateThrottle


class TokenObtainSeguroView(TokenObtainPairView):
    serializer_class = UserTokenLoginObtainSerializer
    throttle_classes = [LoginRateThrottle]


class TokenRefreshSeguroView(TokenRefreshView):
    serializer_class = TokenRefreshSeguroSerializer
    throttle_classes = [RefreshRateThrottle]
