from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import api_view

from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.deletion import ProtectedError

from .models import *
from .serializers import GastoSerializer, TipoGastoSerializer, GastoUpdateSerializer, GastoDetailSerializer
from Tiendas.permissions import requiere_acceso_tienda, usuario_puede_acceder_tienda, respuesta_sin_permiso
from Tiendas.caja import registrar_movimiento_caja




####### TIPO GASTOS ######
@api_view(['GET'])
def list_tipo_gastos(request):
    '''obtenemos todos los tipo_gastos'''
    tipo_gastos = Tipo_Gasto.objects.all()
    if tipo_gastos:
        tipo_gasto_serializer = TipoGastoSerializer(tipo_gastos, many=True)
        return Response(tipo_gasto_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado tipos de gastos'}, status=status.HTTP_200_OK)
    
    

@api_view(['GET'])
def get_tipo_gasto(request, pk):
    tipo_gasto = Tipo_Gasto.objects.filter(id=pk).first()
    if tipo_gasto:
        tipo_gasto_serializer = TipoGastoSerializer(tipo_gasto, many=False)
        return Response(tipo_gasto_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message':'No se encontro el tipo de gasto'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_tipo_gasto(request, pk, tienda_id=None):
    if not request.user.is_superuser:
        return respuesta_sin_permiso()
    tipo_gasto = Tipo_Gasto.objects.filter(id=pk).first()
    if tipo_gasto:
        tipo_gasto_serializer = TipoGastoSerializer(tipo_gasto, data=request.data)
        if tipo_gasto_serializer.is_valid():
            tipo_gasto_serializer.save()
            return Response(tipo_gasto_serializer.data,status=status.HTTP_200_OK)
        return Response(tipo_gasto_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message':'No se encontró el tipo de gasto'}, status=status.HTTP_400_BAD_REQUEST)
        

@api_view(['POST'])
def post_tipo_gasto(request):
    '''creamos un tipo_gasto'''
    if not request.user.is_superuser:
        return respuesta_sin_permiso()
    if request.method == 'POST':        
        tipo_gasto_serializer = TipoGastoSerializer(data = request.data)
        if tipo_gasto_serializer.is_valid():
            tipo_gasto_serializer.save()
            return Response(tipo_gasto_serializer.data, status=status.HTTP_200_OK)
        return Response(tipo_gasto_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['DELETE'])
def delete_tipo_gasto(request, pk):
    if not request.user.is_superuser:
        return respuesta_sin_permiso()
    tipo_gasto = Tipo_Gasto.objects.filter(id=pk).first()
    if tipo_gasto:
        try:
            tipo_gasto.delete()
        except ProtectedError:
            return Response({'error': 'Esta categoría tiene gastos registrados y no se puede eliminar.'},
                            status=status.HTTP_409_CONFLICT)
        return Response({'message':'tipo de gasto eliminado correctamente'},status=status.HTTP_200_OK)
    return Response({'message':'No se encontró el tipo de gasto'}, status=status.HTTP_400_BAD_REQUEST)



######## GASTOS ######

@api_view(['GET'])
@requiere_acceso_tienda
def list_gastos(request, tienda_id=None):
    '''obtenemos todos los gastos'''
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    gastos = Gasto.objects.filter(tienda=tienda.id).select_related('tipo_gasto').order_by('-id')
    if 'page' in request.query_params:
        return _listado_paginado(request.query_params, gastos)
    # Sin "page": respuesta histórica (lista completa) para clientes antiguos.
    if gastos:
        gasto_serializer = GastoDetailSerializer(gastos, many=True)
        return Response(gasto_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado gastos'}, status=status.HTTP_200_OK)


def _fecha_param(valor):
    return datetime.strptime(valor, '%Y-%m-%d').date() if valor else None


def _listado_paginado(params, gastos):
    '''Una página de gastos + totales de todo el filtro (cantidad y monto).'''
    try:
        pagina = int(params.get('page', 1))
        limite = int(params.get('limit', 10))
        if pagina < 1 or not 1 <= limite <= 100:
            raise ValueError
        desde = _fecha_param(params.get('desde'))
        hasta = _fecha_param(params.get('hasta'))
        tipo = int(params['tipo']) if params.get('tipo') else None
    except (TypeError, ValueError):
        return Response({'error': 'Parámetros de consulta inválidos.'}, status=status.HTTP_400_BAD_REQUEST)
    if desde:
        gastos = gastos.filter(fecha__gte=desde)
    if hasta:
        gastos = gastos.filter(fecha__lte=hasta)
    if tipo:
        gastos = gastos.filter(tipo_gasto_id=tipo)
    totales = gastos.aggregate(cantidad=Count('id'), monto=Sum('valor'))
    total = totales['cantidad']
    paginas = max(1, -(-total // limite))
    pagina = min(pagina, paginas)
    filas = gastos[(pagina - 1) * limite:pagina * limite]
    return Response({
        'resultados': GastoDetailSerializer(filas, many=True).data,
        'total_registros': total,
        'total_monto': format(totales['monto'] or Decimal('0'), '.2f'),
        'pagina': pagina,
        'paginas': paginas,
        'limite': limite,
    }, status=status.HTTP_200_OK)
    
@api_view(['GET'])
@requiere_acceso_tienda
def list_gastos_x_fecha(request, date, tienda_id=None):
    '''obtenemos todos los gastos x fecha'''

    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    gastos = Gasto.objects.filter(tienda=tienda.id).select_related('tipo_gasto').filter(fecha=date).order_by('-id')
    if gastos:
        gasto_serializer = GastoDetailSerializer(gastos, many=True)
        return Response(gasto_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado gastos'}, status=status.HTTP_200_OK)

@api_view(['GET'])
@requiere_acceso_tienda
def list_gastos_x_fecha_range(request, date1, date2, tienda_id=None):
    '''obtenemos todos los gastos x rango de fechas'''
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    gastos = Gasto.objects.filter(tienda=tienda.id).select_related('tipo_gasto').filter(fecha__range=[date1, date2]).order_by('-id')
    if gastos:
        gasto_serializer = GastoDetailSerializer(gastos, many=True)
        return Response(gasto_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado gastos'}, status=status.HTTP_200_OK)

@api_view(['GET'])
def get_gasto(request, pk):
    gasto = Gasto.objects.filter(id=pk).first()
    if gasto:
        if not usuario_puede_acceder_tienda(request.user, gasto.tienda_id):
            return respuesta_sin_permiso()
        gasto_serializer = GastoSerializer(gasto, many=False)
        return Response(gasto_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message':'No se encontro el gasto'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_gasto(request, pk, tienda_id=None):
    gasto = Gasto.objects.filter(id=pk).first()
    if not gasto:
        return Response({'message':'No se encontró el gasto'}, status=status.HTTP_400_BAD_REQUEST)
    if not usuario_puede_acceder_tienda(request.user, gasto.tienda_id):
        return respuesta_sin_permiso()

    gasto_serializer = GastoUpdateSerializer(gasto, data=request.data)
    if not gasto_serializer.is_valid():
        return Response(gasto_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        # La ruta del gasto es la única fuente válida. La ruta recibida en la
        # URL no debe poder desviar el ajuste de caja a otra tienda.
        tienda = Tienda.objects.select_for_update().get(pk=gasto.tienda_id)
        gasto = Gasto.objects.select_for_update().get(pk=pk)
        gasto_valor = gasto.valor
        nuevo_valor = gasto_serializer.validated_data['valor']
        diferencia = nuevo_valor - gasto_valor

        gasto_serializer = GastoUpdateSerializer(gasto, data=request.data)
        gasto_serializer.is_valid(raise_exception=True)
        cambio_fecha = gasto_serializer.validated_data.get('fecha', gasto.fecha) != gasto.fecha
        gasto_serializer.save()
        if diferencia or cambio_fecha:
            registrar_movimiento_caja(
                tienda,
                -diferencia,
                tipo='GASTO',
                accion='CORRECCION',
                usuario=request.user,
                origen=gasto,
                detalle='Corrección del valor o la fecha del gasto',
            )

    return Response(gasto_serializer.data, status=status.HTTP_200_OK)
        

@api_view(['POST'])
@requiere_acceso_tienda
def post_gasto(request, tienda_id=None):
    '''creamos un gasto'''

    if request.method == 'POST':
        if tienda_id:
            tienda = Tienda.objects.filter(id=tienda_id).first()
        else:
            tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
        if not tienda:
            return Response({'error': 'Ruta no encontrada'}, status=status.HTTP_404_NOT_FOUND)

        new_data = request.data.copy()
        new_data['tienda']=tienda.id
        new_data['trabajador']=request.user.perfil.id
        gasto_serializer = GastoSerializer(data = new_data)
        if gasto_serializer.is_valid():
            with transaction.atomic():
                # Bloquear la fila evita que dos movimientos concurrentes
                # lean la misma caja y uno sobrescriba el descuento del otro.
                tienda = Tienda.objects.select_for_update().get(pk=tienda.pk)
                gasto = gasto_serializer.save()
                registrar_movimiento_caja(
                    tienda,
                    -gasto.valor,
                    tipo='GASTO',
                    usuario=request.user,
                    origen=gasto,
                    detalle=gasto.comentario or '',
                )
            return Response(gasto_serializer.data, status=status.HTTP_200_OK)
        return Response(gasto_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['DELETE'])
def delete_gasto(request, pk, tienda_id=None):
    gasto = Gasto.objects.filter(id=pk).first()
    if not gasto:
        return Response({'message':'No se encontró el gasto'}, status=status.HTTP_400_BAD_REQUEST)
    if not usuario_puede_acceder_tienda(request.user, gasto.tienda_id):
        return respuesta_sin_permiso()

    with transaction.atomic():
        tienda = Tienda.objects.select_for_update().get(pk=gasto.tienda_id)
        gasto = Gasto.objects.select_for_update().get(pk=pk)
        valor = gasto.valor
        origen_id = gasto.pk
        origen_tipo = gasto._meta.label_lower
        gasto.delete()
        registrar_movimiento_caja(
            tienda,
            valor,
            tipo='GASTO',
            accion='REVERSA',
            usuario=request.user,
            origen_tipo=origen_tipo,
            origen_id=origen_id,
            detalle='Reversa por eliminación del gasto',
        )
    return Response({'message':'gasto eliminado correctamente'},status=status.HTTP_200_OK)
