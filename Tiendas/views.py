from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

import datetime

from Tiendas.models import Tienda, Cierre_Caja, Tienda_Membresia, Membresia
from Tiendas.serializers import TiendaSerializer, CajaSerializer, TiendaMembresiaSerializer, TiendaCreateSerializer


### VIEWS FOR TIENDA  ####

@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def list_all_tiendas(request):
    '''obtenemos todas las tiendas'''

    user = request.user
    if user.username == 'root':
        tiendas = Tienda_Membresia.objects.all().order_by('fecha_vencimiento')
        if tiendas:
            serializer = TiendaMembresiaSerializer(tiendas, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response({'message': 'No se han creado tiendas'}, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No tiene permisos para acceder a esta vista'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def list_tiendas_admin(request):
    '''return list stores for a admin user'''

    user = request.user
    if user.is_superuser:
        tiendas = Tienda.objects.filter(administrador=user)
        if tiendas:
            serializer = TiendaSerializer(tiendas, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontraron tiendas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def list_tiendas(request):
    '''obtenemos todas las tiendas'''

    user = request.user
    tiendas = Tienda.objects.filter(id=user.perfil.tienda.id)

    if tiendas:
        serializer = TiendaSerializer(tiendas, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado tiendas'}, status=status.HTTP_200_OK)


@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def get_tienda(request):

    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if tienda:
        serialize = TiendaSerializer(tienda, many=False)
        return Response(serialize.data)
    else:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_tienda(request, pk):
    tienda = Tienda.objects.filter(id=pk).first()
    if tienda:
        serialize = TiendaSerializer(tienda, data=request.data)
        if serialize.is_valid():
            serialize.save()
            return Response(serialize.data, status=status.HTTP_200_OK)
        return Response(serialize.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def post_tienda(request):
    '''creamos una tienda'''
    if request.method == 'POST':
        serialize = TiendaCreateSerializer(data=request.data)
        if serialize.is_valid():
            tienda = serialize.save()
            Cierre_Caja.objects.create(tienda=tienda, valor=tienda.caja_inicial, fecha_cierre=(
                datetime.date.today() - datetime.timedelta(days=1)))
            Tienda_Membresia.objects.create(
                tienda=tienda,
                membresia=Membresia.objects.get(nombre='Prueba'),
                fecha_activacion=datetime.date.today(),
                fecha_vencimiento=(datetime.date.today() +
                                   datetime.timedelta(days=7)),
                estado='Activa'
            )
            return Response(serialize.data, status=status.HTTP_200_OK)
        return Response(serialize.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
def delete_tienda(request, pk):
    tienda = Tienda.objects.filter(id=pk).first()
    if tienda:
        tienda.delete()
        return Response({'message': 'Tienda eliminada correctamente'}, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)

### END VIEWS FOR TIENDA  ####

#### CIERRES DE CAJA#######


@api_view(['GET'])
def get_cierres_caja(request, tienda_id=None):
    """obtenemos la lista de los cierres de caja"""
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(
            id=request.user.perfil.tienda.id).first()
    cierres_caja = Cierre_Caja.objects.filter(tienda=tienda.id).order_by('-id')
    if cierres_caja:
        serializer = CajaSerializer(cierres_caja, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontraron registros'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_caja_anterior(request, fecha, tienda_id=None):
    """obtenemos el valor de la caja del dia anterior a la fecha que recibimos como parametro"""
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(
            id=request.user.perfil.tienda.id).first()
    fecha = datetime.datetime.strptime(fecha, '%Y-%m-%d')
    dia_anterior = fecha - (datetime.timedelta(days=1))
    caja_anterior = Cierre_Caja.objects.filter(
        tienda=tienda.id, fecha_cierre=dia_anterior).first()
    if caja_anterior:
        serializer = CajaSerializer(caja_anterior, many=False)
        return Response(serializer.data, status=status.HTTP_200_OK)
    else:
        print('no hay datos de caja anterior')

    return Response({'message': 'No se encontraron registros'}, status=status.HTTP_200_OK)


@api_view(['POST'])
def post_cierre_caja(request, fecha, tienda_id=None):
    """guardamos el cierre de caja con la fecha, valor y tienda"""

    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    fecha = datetime.datetime.strptime(fecha, '%Y-%m-%d')
    cierre_caja = Cierre_Caja.objects.create(
        fecha_cierre=fecha, valor=tienda.caja_inicial, tienda=tienda)
    return Response({'message': 'Se creo el registro'}, status=status.HTTP_200_OK)


@api_view(['DELETE'])
def delete_cierre_caja(request, pk):
    '''eliminamos un registro de cierre de caja'''

    cierre_caja = Cierre_Caja.objects.get(id=pk)
    cierre_caja.delete()
    return Response({'message': 'Cierre Caja Eliminado'}, status=status.HTTP_200_OK)


####### MEMBRESIAS  ##########

@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def get_tienda_membresia(request):
    '''get info of store and info the acount store'''

    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    tienda_membresia = Tienda_Membresia.objects.get(tienda=tienda.id)
    if tienda_membresia:
        serialize = TiendaMembresiaSerializer(tienda_membresia, many=False)
        return Response(serialize.data)
    else:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def get_tienda_membresia_admin(request, pk):
    '''get info of store and info the acount store for a admin user'''
    
    tienda = Tienda.objects.filter(id=pk).first()
    tienda_membresia = Tienda_Membresia.objects.get(tienda=tienda.id)
    if tienda_membresia:
        serialize = TiendaMembresiaSerializer(tienda_membresia, many=False)
        return Response(serialize.data)
    else:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
def activar_membresia_mensual(request, pk):
    '''get store and activate membershi for a mounth + 30 days'''
    print('ingresa a activar por messsss')
    tienda = Tienda_Membresia.objects.get(id=pk)
    if tienda:
        tienda.estado = 'Activa'
        tienda.membresia = Membresia.objects.get(nombre='Mensual')
        tienda.fecha_activacion = datetime.date.today()
        tienda.fecha_vencimiento = tienda.fecha_activacion + datetime.timedelta(days=30)
        tienda.save()
        return Response({'message':'Suscripción Mensual Activa'}, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)
    
@api_view(['GET'])
def activar_membresia_ano(request, pk):
    '''get store and activate membershi for a year + 365 days'''
    tienda = Tienda_Membresia.objects.get(id=pk)
    if tienda:
        tienda.estado = 'Activa'
        tienda.membresia = Membresia.objects.get(nombre='Anual')
        tienda.fecha_activacion = datetime.date.today()
        tienda.fecha_vencimiento = tienda.fecha_activacion + datetime.timedelta(days=365)
        tienda.save()
        return Response({'message':'Suscripción Anual Activa'}, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)

def comprobar_estado_membresia(tienda_id):
    '''verificamos el estado de la membresia'''
    print('ingresa a comprobar membresia ')
    print(tienda_id)
    suscripcion_tienda = Tienda_Membresia.objects.get(tienda=tienda_id)
    pendiente_pago = suscripcion_tienda.fecha_vencimiento + \
        datetime.timedelta(days=1)
    vencida = pendiente_pago + datetime.timedelta(days=2)
    if suscripcion_tienda.estado == 'Activa' and datetime.date.today() >= pendiente_pago:
        print('es activa')
        suscripcion_tienda.estado = 'Pendiente Pago'
        suscripcion_tienda.save()

    if suscripcion_tienda.estado == 'Pendiente Pago' and datetime.date.today() >= vencida:
        print('espendiente pago')
        suscripcion_tienda.estado = 'Vencida'
        suscripcion_tienda.save()

