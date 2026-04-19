from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.pagination import LimitOffsetPagination

from Clientes.models import Cliente
from Tiendas.models import Tienda
from Ventas.models import Venta
from Recaudos.models import Recaudo

from Clientes.serializers import ClienteSerializer, ClienteCreateSerializer


def _calcular_score(cliente_id, tienda_id):
    """Calcula el score crediticio (0-100) de un cliente basado en su historial."""
    ventas = Venta.objects.filter(cliente_id=cliente_id, tienda_id=tienda_id)
    recaudos = Recaudo.objects.filter(venta__in=ventas)

    pagos = recaudos.filter(visita_blanco__isnull=True).count()
    no_pagos = recaudos.filter(visita_blanco__isnull=False).count()
    total_visitas = pagos + no_pagos

    # Componente 1 — tasa de pago (40 pts)
    # Sin historial de visitas → puntaje neutral 20
    tasa_pago = pagos / total_visitas if total_visitas > 0 else None
    comp_pago = round(tasa_pago * 40, 1) if tasa_pago is not None else 20.0

    # Componente 2 — salud de créditos activos (25 pts)
    vencidos = ventas.filter(estado_venta='Vencido').count()
    atrasados = ventas.filter(estado_venta='Atrasado').count()
    if vencidos > 0:
        comp_activos = 0
    elif atrasados > 0:
        comp_activos = 12
    else:
        comp_activos = 25

    # Componente 3 — sin créditos perdidos (20 pts)
    total_creditos = ventas.count()
    perdidos = ventas.filter(estado_venta='Perdida').count()
    tasa_perdidos = perdidos / total_creditos if total_creditos > 0 else 0
    comp_perdidos = round((1 - tasa_perdidos) * 20, 1)

    # Componente 4 — historial positivo (15 pts)
    liquidados = ventas.filter(estado_venta='Pagado').count()
    comp_historial = round(min(liquidados / 5, 1) * 15, 1)

    score = max(0, min(100, round(comp_pago + comp_activos + comp_perdidos + comp_historial)))
    if perdidos > 0:
        score = min(score, 30)

    if score >= 80:
        nivel = 'Excelente'
    elif score >= 60:
        nivel = 'Bueno'
    elif score >= 40:
        nivel = 'Regular'
    else:
        nivel = 'Riesgo'

    return {
        'score': score,
        'nivel': nivel,
        'sin_historial': total_visitas == 0 and total_creditos == 0,
        'detalle': {
            'comp_pago': comp_pago,
            'comp_activos': comp_activos,
            'comp_perdidos': comp_perdidos,
            'comp_historial': comp_historial,
            'pagos': pagos,
            'no_pagos': no_pagos,
            'total_creditos': total_creditos,
            'perdidos': perdidos,
            'liquidados': liquidados,
        },
    }


@api_view(['GET'])
def list_clientes(request, tienda_id=None):
    '''obtenemos todos los clientes'''

    print('ingresa a list clientessssss')
    user = request.user

    print(tienda_id)
    if tienda_id:
        print('list con el cliente id......')
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        print('else clientes')
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    clientes = Cliente.objects.filter(tienda=tienda.id).order_by('nombres')
    if clientes:
        clientes_serializer = ClienteSerializer(clientes, many=True)
        return Response(clientes_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado clientes'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def list_clientes_activos(request, tienda_id=None):
    '''obtenemos todos los clientes activos'''
    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    clientes = Cliente.objects.filter(tienda=tienda.id).filter(
        estado_cliente='Activo').order_by('nombres')
    if clientes:
        clientes_serializer = ClienteSerializer(clientes, many=True)
        return Response(clientes_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado clientes'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def list_clientes_disponibles(request, tienda_id=None):
    '''obtenemos todos los clientes sin ventas activas'''

    clientes = []
    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    ventas_activas = Venta.objects.filter(tienda=tienda.id).exclude(
        estado_venta='Pagado').exclude(estado_venta='Perdida')

    for venta in ventas_activas:
        clientes.append(venta.cliente.id)

    clientes_disponibles = Cliente.objects.filter(tienda=tienda.id).filter(
        estado_cliente='Activo').exclude(id__in=clientes)

    if clientes_disponibles:
        clientes_serializer = ClienteSerializer(
            clientes_disponibles, many=True)
        return Response(clientes_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontraron clientes disponibles'}, status=status.HTTP_200_OK)


class ClientesListAPIView(ListAPIView):
    serializer_class = ClienteSerializer
    # pagination_class = LimitOffsetPagination

    def get_queryset(self):
        user = self.request.user
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
        queryset = Cliente.objects.filter(tienda=tienda.id)
        return queryset


@api_view(['GET'])
def get_cliente(request, pk):
    cliente = Cliente.objects.filter(id=pk).first()
    if cliente:
        cliente_serializer = ClienteSerializer(cliente, many=False)
        return Response(cliente_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontró el cliente'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def post_cliente(request, tienda_id=None):
    '''creamos un cliente'''
    if request.method == 'POST':
        if tienda_id:
            tienda = Tienda.objects.filter(id=tienda_id).first()
        else:
            tienda = Tienda.objects.filter(
                id=request.user.perfil.tienda.id).first()
        new_data = request.data
        new_data['tienda'] = tienda.id
        cliente_serializer = ClienteCreateSerializer(data=new_data)
        if cliente_serializer.is_valid():
            cliente_serializer.save()
            return Response(cliente_serializer.data, status=status.HTTP_200_OK)
        return Response(cliente_serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_cliente(request, pk):
    cliente = Cliente.objects.filter(id=pk).first()
    if cliente:
        cliente_serializer = ClienteSerializer(cliente, data=request.data)
        if cliente_serializer.is_valid():
            cliente_serializer.save()
            return Response(cliente_serializer.data, status=status.HTTP_200_OK)
        return Response(cliente_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message': 'No existe el cliente'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
def delete_cliente(request, pk):
    cliente = Cliente.objects.filter(id=pk).first()
    ventas = Venta.objects.filter(cliente=cliente.id)
    if cliente:
        if ventas:
            return Response({'message': 'No se puede eliminar el cliente ya que tiene ventas activas'}, status=status.HTTP_202_ACCEPTED)
        cliente.delete()
        return Response({'message': 'Cliente eliminado correctamente'}, status=status.HTTP_200_OK)
    return Response({'message': 'Cliente no existe!'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
def score_cliente(request, pk, tienda_id):
    """Score crediticio individual de un cliente."""
    cliente = Cliente.objects.filter(id=pk, tienda_id=tienda_id).first()
    if not cliente:
        return Response({'message': 'Cliente no encontrado'}, status=status.HTTP_404_NOT_FOUND)
    return Response(_calcular_score(pk, tienda_id), status=status.HTTP_200_OK)


@api_view(['GET'])
def scores_tienda(request, tienda_id):
    """Score crediticio de todos los clientes de una tienda (bulk)."""
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    clientes = Cliente.objects.filter(tienda=tienda_id).values_list('id', flat=True)
    result = {cid: _calcular_score(cid, tienda_id) for cid in clientes}
    return Response(result, status=status.HTTP_200_OK)
