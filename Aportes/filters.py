from dataclasses import fields
from django_filters import rest_framework as filters
from .models import Aporte

class AporteFilter(filters.FilterSet):

    class Meta:
        model = Aporte
        fields = ['id','fecha','trabajador']