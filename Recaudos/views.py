from itertools import count
from Ventas.models import Venta
from rest_framework.decorators import api_view
from rest_framework import generics
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import LimitOffsetPagination

from Recaudos.models import Recaudo, Visita_Blanco
from Recaudos.serializers import RecaudoSerializer, Visitas_BlancoSerializer, RecaudoDetailSerializer,RecaudoUpdateSerializer
from Tiendas.models import Tienda
from Tiendas.views import comprobar_estado_membresia
from Ventas.models import Venta

from datetime import datetime
from django.utils import timezone

@api_view(['GET'])
def list_recaudos(request):
    '''obtenemos todas las recaudos'''
    user = request.user
    tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    recaudos = Recaudo.objects.filter(tienda=tienda.id)
    if recaudos:
        recaudo_serializer = RecaudoDetailSerializer(recaudos, many=True)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado recaudos'}, status=status.HTTP_200_OK)

@api_view(['GET'])
def list_recaudos_fecha(request, date, tienda_id=None):
    '''obtenemos todas las recaudos'''

    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    comprobar_estado_membresia(tienda.id)
    recaudos = Recaudo.objects.filter(tienda=tienda.id).filter(fecha_recaudo=date)
    if recaudos:
        recaudo_serializer = RecaudoDetailSerializer(recaudos, many=True)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado recaudos'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def calcular_sueldo_trabajador(request, date1, date2, porcentaje=None, tienda_id=None):
    '''
    Calcula el sueldo del trabajador basado en un porcentaje de los recaudos 
    en un rango de fechas específico.
    '''
    print('ingresa a calcular sueldo')
    print(date1, date2, porcentaje, tienda_id)
    
    try:
        # Determinar la tienda
        if tienda_id:
            print(f"Buscando tienda con ID: {tienda_id}")
            try:
                tienda_id_int = int(tienda_id)
                tienda = Tienda.objects.filter(id=tienda_id_int).first()
                print(f"Tienda encontrada: {tienda}")
            except ValueError:
                return Response({'error': 'ID de tienda debe ser un número'}, 
                               status=status.HTTP_400_BAD_REQUEST)
        else:
            user = request.user
            print(f"Usuario: {user}")
            if hasattr(user, 'perfil') and hasattr(user.perfil, 'tienda'):
                tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
                print(f"Tienda del usuario: {tienda}")
            else:
                return Response({'error': 'Usuario no tiene tienda asociada'}, 
                               status=status.HTTP_400_BAD_REQUEST)
        
        if not tienda:
            print("Tienda no encontrada")
            return Response({'error': 'Tienda no encontrada'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Convertir strings a objetos date
        try:
            fecha_inicio_obj = datetime.strptime(date1, '%Y-%m-%d').date()
            fecha_fin_obj = datetime.strptime(date2, '%Y-%m-%d').date()
            print(f"Fechas convertidas: {fecha_inicio_obj} a {fecha_fin_obj}")
        except ValueError:
            return Response({'error': 'Formato de fecha incorrecto. Use YYYY-MM-DD'}, 
                           status=status.HTTP_400_BAD_REQUEST)
        
        # Validar que fecha_inicio no sea mayor que fecha_fin
        if fecha_inicio_obj > fecha_fin_obj:
            return Response({'error': 'fecha_inicio no puede ser mayor que fecha_fin'}, 
                           status=status.HTTP_400_BAD_REQUEST)
        
        # Establecer porcentaje por defecto si no se proporciona
        if porcentaje is None:
            porcentaje_valor = 3.0
        else:
            try:
                porcentaje_valor = float(porcentaje)
                print(f"Porcentaje convertido: {porcentaje_valor}")
            except ValueError:
                return Response({'error': 'Porcentaje debe ser un número válido'}, 
                               status=status.HTTP_400_BAD_REQUEST)
        
        # Filtrar recaudos para el rango de fechas
        print(f"Filtrando recaudos para tienda {tienda.id} entre {fecha_inicio_obj} y {fecha_fin_obj}")
        recaudos = Recaudo.objects.filter(
            tienda=tienda.id,
            fecha_recaudo__range=[fecha_inicio_obj, fecha_fin_obj]
        )
        print(f"Número de recaudos encontrados: {recaudos.count()}")
        
        # Calcular total recaudado usando aggregate para evitar problemas con None
        from django.db.models import Sum
        total_recaudado = recaudos.aggregate(total=Sum('valor_recaudo'))['total'] or 0
        print(f'Total recaudado: {total_recaudado}')
        
        sueldo = total_recaudado * (porcentaje_valor / 100)
        print(f'Sueldo calculado: {sueldo}')
        
        return Response({
            'fecha_inicio': fecha_inicio_obj,
            'fecha_fin': fecha_fin_obj,
            'total_recaudado': float(total_recaudado),
            'porcentaje_aplicado': porcentaje_valor,
            'sueldo_calculado': float(sueldo),
            'cantidad_recaudos': recaudos.count(),
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        print(f"Error inesperado: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({'error': 'Error interno del servidor'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
@api_view(['GET'])
def list_recaudos_venta(request, venta_id):
    '''obtenemos los recaudos pertenecientes a una venta en particular'''

    recaudos = Recaudo.objects.filter(venta=venta_id).order_by('-id')
    
    if recaudos:
        recaudo_serializer = RecaudoDetailSerializer(recaudos, many=True)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    return Response({'message':'No se han creado recaudos'}, status=status.HTTP_200_OK)

    

@api_view(['GET'])
def get_recaudo(request, pk):
    recaudo = Recaudo.objects.filter(id=pk).first()
    if recaudo:
        recaudo_serializer = RecaudoDetailSerializer(recaudo, many=False)
        return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message':'No se encontro el recaudo'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
def put_recaudo(request, pk, tienda_id=None):
    recaudo = Recaudo.objects.filter(id=pk).first()
    if tienda_id:
        tienda = Tienda.objects.get(id=tienda_id)
    else:
        tienda = Tienda.objects.get(id=recaudo.tienda.id)
    venta = Venta.objects.get(id=recaudo.venta.id)
    if recaudo:
        recaudo_serializer = RecaudoUpdateSerializer(recaudo, data=request.data)
        if recaudo_serializer.is_valid():
            if recaudo_serializer.validated_data['valor_recaudo'] != recaudo.valor_recaudo:
                venta.saldo_actual = venta.saldo_actual + recaudo.valor_recaudo
                venta.saldo_actual = venta.saldo_actual - recaudo_serializer.validated_data['valor_recaudo']
                tienda.caja_inicial = tienda.caja_inicial - recaudo.valor_recaudo
                tienda.caja_inicial = tienda.caja_inicial + recaudo_serializer.validated_data['valor_recaudo']
                recaudo_serializer.save()
                recaudos = Recaudo.objects.filter(venta=venta.id)
                if venta.promedio_pago() >= venta.valor_cuota():
                    venta.estado_venta = 'Vigente'
                if venta.promedio_pago() < venta.valor_cuota():
                    venta.estado_venta = 'Atrasado'
                if venta.cuotas < recaudos.count():
                    venta.estado_venta = 'Vencido'
                if venta.saldo_actual <= 0:
                    venta.estado_venta = 'Pagado'
                tienda.save()
                venta.save()
            else:
                recaudo_serializer.save()
            return Response(recaudo_serializer.data,status=status.HTTP_200_OK)
        return Response(recaudo_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message':'No se encontró el recaudo'}, status=status.HTTP_400_BAD_REQUEST)
        

@api_view(['POST'])
def post_recaudo(request, tienda_id=None):
    '''creamos una recaudo'''
    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    new_data = request.data
    new_data['tienda']=tienda.id
    
    venta = Venta.objects.get(id = new_data['venta'])
    
    if request.method == 'POST':
        recaudo_serializer = RecaudoSerializer(data = new_data)
        if recaudo_serializer.is_valid():
            recaudo_serializer.save()
            tienda.caja_inicial = tienda.caja_inicial + recaudo_serializer.validated_data['valor_recaudo']
            venta.saldo_actual = venta.saldo_actual - recaudo_serializer.validated_data['valor_recaudo']
            recaudos = Recaudo.objects.filter(venta=venta.id)
            if venta.promedio_pago() >= venta.valor_cuota():
                venta.estado_venta = 'Vigente'
            if venta.promedio_pago() < venta.valor_cuota():
                venta.estado_venta = 'Atrasado'
            if venta.cuotas < recaudos.count():
                venta.estado_venta = 'Vencido'
            if venta.saldo_actual <= 0:
                venta.estado_venta = 'Pagado'
            tienda.save()
            venta.save()
            return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
        return Response(recaudo_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

@api_view(['POST'])
def post_recaudo_no_pay(request, tienda_id=None):
    '''creamos una recaudo'''
    user = request.user
    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    new_data = request.data
    visita_blanco = new_data['visita_blanco']
    
    new_data['tienda']=tienda.id
    venta = Venta.objects.get(id = new_data['venta'])
    
    if request.method == 'POST':
        visita_blanco_serializer = Visitas_BlancoSerializer(data=visita_blanco)
        if visita_blanco_serializer.is_valid():
            visita_blanco = visita_blanco_serializer.save()
            new_data['visita_blanco']=visita_blanco.id
            
            recaudo_serializer = RecaudoSerializer(data = new_data)
            
            if recaudo_serializer.is_valid():
                
                recaudo_serializer.save()
                tienda.caja_inicial = tienda.caja_inicial + recaudo_serializer.validated_data['valor_recaudo']
                venta.saldo_actual = venta.saldo_actual - recaudo_serializer.validated_data['valor_recaudo']
                recaudos = Recaudo.objects.filter(venta=venta.id)
                
                if venta.promedio_pago() >= venta.valor_cuota():
                    venta.estado_venta = 'Vigente'
                if venta.promedio_pago() < venta.valor_cuota():
                    venta.estado_venta = 'Atrasado'
                if venta.cuotas < recaudos.count():
                    venta.estado_venta = 'Vencido'
                if venta.saldo_actual <= 0:
                    venta.estado_venta = 'Pagado'
                tienda.save()
                venta.save()
                return Response(recaudo_serializer.data, status=status.HTTP_200_OK)
        return Response(recaudo_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['DELETE'])
def delete_recaudo(request, pk):
    recaudo = Recaudo.objects.filter(id=pk).first()
    tienda = Tienda.objects.get(id=recaudo.tienda.id)
    venta = Venta.objects.get(id=recaudo.venta.id)
    if recaudo:
        tienda.caja_inicial = tienda.caja_inicial - recaudo.valor_recaudo
        venta.saldo_actual = venta.saldo_actual + recaudo.valor_recaudo
        recaudos = Recaudo.objects.filter(venta=venta.id)
        recaudo.delete()        
        if venta.promedio_pago() >= venta.valor_cuota():
            venta.estado_venta = 'Vigente'
        elif venta.promedio_pago() < venta.valor_cuota():
            venta.estado_venta = 'Atrasado'
        elif venta.cuotas < recaudos.count():
            venta.estado_venta = 'Vencido'
        elif venta.saldo_actual <= 0:
            venta.estado_venta = 'Pagado'
        tienda.save()
        venta.save()
        return Response({'message':'Recaudo eliminado correctamente'},status=status.HTTP_200_OK)
    return Response({'message':'No se encontró el recaudo'}, status=status.HTTP_400_BAD_REQUEST)

