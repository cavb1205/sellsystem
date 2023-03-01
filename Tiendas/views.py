from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

import datetime

from Tiendas.models import Tienda, Cierre_Caja, Tienda_Membresia, Membresia
from Tiendas.serializers import TiendaSerializer, CajaSerializer, TiendaMembresiaSerializer


### VIEWS FOR TIENDA  ####

@api_view(['GET'])
#@permission_classes([IsAuthenticated])
def list_all_tiendas(request):
    '''obtenemos todas las tiendas'''

    user = request.user
    if user.is_superuser:    
        tiendas = Tienda.objects.all()
        if tiendas:
            serializer = TiendaSerializer(tiendas, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response({'message':'No se han creado tiendas'}, status=status.HTTP_200_OK)
    else:
        return Response({'message':'No tiene permisos para acceder a esta vista'},status=status.HTTP_200_OK)

@api_view(['GET'])
#@permission_classes([IsAuthenticated])
def list_tiendas(request):
    '''obtenemos todas las tiendas'''

    user = request.user
    tiendas = Tienda.objects.filter(id=user.perfil.tienda.id)
        
    if tiendas:
        serializer = TiendaSerializer(tiendas, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado tiendas'}, status=status.HTTP_200_OK)
    
    
@api_view(['GET'])
#@permission_classes([IsAuthenticated])
def get_tienda(request):
    print(request.user)
    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    if tienda:
        serialize = TiendaSerializer(tienda, many=False)
        return Response(serialize.data)
    else:
        return Response({'message':'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_tienda(request, pk):
    tienda = Tienda.objects.filter(id=pk).first()
    if tienda:
        serialize = TiendaSerializer(tienda, data=request.data)
        if serialize.is_valid():
            serialize.save()
            return Response(serialize.data,status=status.HTTP_200_OK)
        return Response(serialize.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message':'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)
        

@api_view(['POST'])
def post_tienda(request):
    '''creamos una tienda'''
    if request.method == 'POST':
        serialize = TiendaSerializer(data = request.data)
        if serialize.is_valid():
            serialize.save()
            return Response(serialize.data, status=status.HTTP_200_OK)
        return Response(serialize.errors, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['DELETE'])
def delete_tienda(request, pk):
    tienda = Tienda.objects.filter(id=pk).first()
    if tienda:
        tienda.delete()
        return Response({'message':'Tienda eliminada correctamente'},status=status.HTTP_200_OK)
    return Response({'message':'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)

### END VIEWS FOR TIENDA  ####

####CIERRES DE CAJA#######
@api_view(['GET'])
def get_cierres_caja(request):
    """obtenemos la lista de los cierres de caja"""

    tienda = Tienda.objects.filter(id= request.user.perfil.tienda.id).first()
    cierres_caja = Cierre_Caja.objects.filter(tienda=tienda.id).order_by('-id')
    if cierres_caja:
        serializer = CajaSerializer(cierres_caja, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message':'No se encontraron registros'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def get_caja_anterior(request,fecha):
    """obtenemos el valor de la caja del dia anterior a la fecha que recibimos como parametro"""

    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    
    fecha = datetime.datetime.strptime(fecha,'%Y-%m-%d')
    dia_anterior = fecha - (datetime.timedelta(days=1))
    caja_anterior = Cierre_Caja.objects.filter(tienda=tienda.id,fecha_cierre=dia_anterior).first()
    if caja_anterior:
        serializer = CajaSerializer(caja_anterior, many=False)
        return Response(serializer.data, status=status.HTTP_200_OK)
    else:
        print('no hay datos de caja anterior')
    
    return Response({'message':'No se encontraron registros'}, status=status.HTTP_200_OK)



@api_view(['POST'])
def post_cierre_caja(request,fecha):
    """guardamos el cierre de caja con la fecha, valor y tienda"""

    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    fecha = datetime.datetime.strptime(fecha,'%Y-%m-%d')
    cierre_caja = Cierre_Caja.objects.create(fecha_cierre=fecha, valor=tienda.caja_inicial, tienda=tienda)
    return Response({'message':'Se creo el registro'},status=status.HTTP_200_OK)


@api_view(['DELETE'])
def delete_cierre_caja(request,pk):
    '''eliminamos un registro de cierre de caja'''
    
    cierre_caja = Cierre_Caja.objects.get(id=pk)
    cierre_caja.delete()
    return Response({'message':'Cierre Caja Eliminado'},status=status.HTTP_200_OK)





####### MEMBRESIAS  ##########

@api_view(['GET'])
#@permission_classes([IsAuthenticated])
def get_tienda_membresia(request):
    
    tienda = Tienda.objects.filter(id=request.user.perfil.tienda.id).first()
    tienda_membresia = Tienda_Membresia.objects.get(tienda=tienda.id)
    
    if tienda_membresia:
        serialize = TiendaMembresiaSerializer(tienda_membresia, many=False)
        return Response(serialize.data)
    else:
        return Response({'message':'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)


def comprobar_estado_membresia(tienda_id):
    '''verificamos el estado de la membresia'''

    suscripcion_tienda = Tienda_Membresia.objects.get(tienda=tienda_id)
    pendiente_pago = suscripcion_tienda.fecha_vencimiento + datetime.timedelta(days=1)   
    vencida = pendiente_pago + datetime.timedelta(days=3)
    if suscripcion_tienda.estado == 'Activa' and datetime.date.today() >= pendiente_pago:
        suscripcion_tienda.estado = 'Pendiente Pago'
        suscripcion_tienda.save()
        return suscripcion_tienda.estado
    
    elif suscripcion_tienda.estado == 'Pendiente Pago' and datetime.date.today() >= vencida:
        suscripcion_tienda.estado = 'Vencida'
        suscripcion_tienda.save()
        return suscripcion_tienda.estado
    
    else:
        return suscripcion_tienda.estado

    
    