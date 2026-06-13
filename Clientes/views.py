from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.pagination import LimitOffsetPagination
from django.db.models import Avg, Max
from datetime import date

from Clientes.models import Cliente
from Tiendas.models import Tienda
from Ventas.models import Venta
from Recaudos.models import Recaudo

from Clientes.serializers import ClienteSerializer, ClienteCreateSerializer
from Tiendas.permissions import requiere_acceso_tienda, usuario_puede_acceder_tienda, respuesta_sin_permiso


def _calcular_score(cliente_id, tienda_id):
    """Calcula el score crediticio (0-100) y el cupo recomendado para un cliente.

    Renovaciones: cuando un crédito vencido se renueva, se genera un Recaudo
    con es_renovacion=True (excluido del conteo de pagos reales) y la venta
    nueva queda vinculada a la vieja via origen_renovacion. La venta vieja
    cierra como 'Pagado' pero NO cuenta como liquidada; se trata como un
    vencido para el componente de salud de créditos activos.
    """
    ventas = Venta.objects.filter(cliente_id=cliente_id, tienda_id=tienda_id)
    # Solo recaudos reales — excluye los generados al renovar
    recaudos = Recaudo.objects.filter(venta__in=ventas, es_renovacion=False)

    pagos = recaudos.filter(visita_blanco__isnull=True).count()
    no_pagos = recaudos.filter(visita_blanco__isnull=False).count()
    total_visitas = pagos + no_pagos

    # Componente 1 — tasa de pago (40 pts)
    tasa_pago = pagos / total_visitas if total_visitas > 0 else None
    comp_pago = round(tasa_pago * 40, 1) if tasa_pago is not None else 20.0

    # Componente 2 — salud de créditos activos (25 pts)
    # Las ventas renovadas (cerradas con un Recaudo de renovación) cuentan
    # como vencidos: el cliente no pagó, le rolaron la deuda.
    vencidos = ventas.filter(estado_venta='Vencido').count()
    atrasados = ventas.filter(estado_venta='Atrasado').count()
    renovaciones = ventas.filter(renovacion__isnull=False).distinct().count()
    if (vencidos + renovaciones) > 0:
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
    # Excluye créditos cerrados-por-renovación: no son una liquidación real.
    liquidados = ventas.filter(estado_venta='Pagado', renovacion__isnull=True).count()
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

    # ── Cupo recomendado ──────────────────────────────────────────────────────
    try:
        tienda = Tienda.objects.get(id=tienda_id)
        cupo_minimo = float(tienda.cupo_minimo_nuevo)
    except Exception:
        cupo_minimo = 100000.0

    cupo_recomendado = 0
    justificacion = {}

    if perdidos > 0:
        cupo_recomendado = 0
        justificacion = {'razon': 'Cliente con créditos perdidos — cupo bloqueado', 'bloqueado': True}
    elif vencidos > 0:
        cupo_recomendado = 0
        justificacion = {'razon': 'Debe liquidar el crédito vencido antes de recibir nuevo cupo', 'bloqueado': True}
    elif total_creditos == 0:
        cupo_recomendado = int(cupo_minimo)
        justificacion = {'razon': 'Cliente nuevo — cupo inicial configurado por la tienda', 'bloqueado': False}
    else:
        creditos_pagados = ventas.filter(estado_venta='Pagado')

        # Capacidad de pago: promedio de los últimos 90 recaudos reales
        # (excluye renovaciones — su monto es el saldo total, no un pago real)
        recaudos_exitosos = list(
            Recaudo.objects.filter(
                venta__in=ventas, visita_blanco__isnull=True, es_renovacion=False
            )
            .order_by('-fecha_recaudo')[:90]
        )
        promedio_pago_real = (
            sum(float(r.valor_recaudo) for r in recaudos_exitosos) / len(recaudos_exitosos)
            if recaudos_exitosos else 0
        )

        # Cuotas típicas: preferir créditos pagados, sino todos
        cuotas_avg = (
            creditos_pagados.aggregate(Avg('cuotas'))['cuotas__avg']
            or ventas.aggregate(Avg('cuotas'))['cuotas__avg']
            or 30
        )
        capacidad_cuota = promedio_pago_real * float(cuotas_avg)

        # Base histórica: todos los créditos (no solo pagados)
        monto_max_todos = ventas.aggregate(Max('valor_venta'))['valor_venta__max'] or 0
        monto_max_pagados = creditos_pagados.aggregate(Max('valor_venta'))['valor_venta__max'] or 0
        # Para el techo usamos pagados si existen, sino todos
        monto_max = monto_max_pagados if monto_max_pagados else monto_max_todos

        ultimo_pagado = creditos_pagados.order_by('-fecha_venta').first()
        ultimo_monto = float(ultimo_pagado.valor_venta) if ultimo_pagado else float(monto_max_todos)

        base_historica = max(float(monto_max_todos), ultimo_monto * 1.25)

        # Solo limitar por capacidad_cuota si el cliente tiene créditos pagados
        # (suficiente historial real). Sin pagados, usar base histórica directamente.
        if capacidad_cuota > 0 and creditos_pagados.exists():
            base = min(base_historica, capacidad_cuota) if base_historica > 0 else capacidad_cuota
        else:
            base = base_historica if base_historica > 0 else cupo_minimo

        # Factor por score
        if score >= 80:   factor_score = 1.25
        elif score >= 60: factor_score = 1.00
        elif score >= 40: factor_score = 0.70
        else:             factor_score = 0.40

        # Factor por recencia (excluye renovaciones)
        ultima_fecha = (
            Recaudo.objects.filter(
                venta__in=ventas, visita_blanco__isnull=True, es_renovacion=False
            )
            .order_by('-fecha_recaudo')
            .values_list('fecha_recaudo', flat=True)
            .first()
        )
        if ultima_fecha is None and ultimo_pagado:
            ultima_fecha = ultimo_pagado.fecha_venta

        if ultima_fecha:
            dias = (date.today() - ultima_fecha).days
            if dias < 90:    factor_recencia = 1.00
            elif dias < 180: factor_recencia = 0.85
            elif dias < 365: factor_recencia = 0.70
            else:            factor_recencia = 0.50
        else:
            dias = 0
            factor_recencia = 1.00

        # Factor por crédito vigente atrasado
        factor_vigente = 0.60 if atrasados > 0 else 1.00

        cupo_calculado = base * factor_score * factor_recencia * factor_vigente

        # Piso: último crédito pagado (trayectoria reciente), no el máximo histórico
        piso = float(ultimo_monto) * 0.5 if ultimo_monto > 0 else 0
        techo_ref = monto_max if monto_max else monto_max_todos
        techo = float(techo_ref) * 2 if techo_ref else cupo_minimo * 3

        cupo_bruto = max(piso, min(techo, cupo_calculado))

        # Redondeo proporcional a la escala del crédito
        if cupo_bruto >= 100000:
            unidad = 1000
        elif cupo_bruto >= 10000:
            unidad = 100
        elif cupo_bruto >= 1000:
            unidad = 10
        else:
            unidad = 1
        cupo_recomendado = int(round(cupo_bruto / unidad) * unidad)

        justificacion = {
            'base_historica': int(base_historica),
            'monto_maximo_pagado': int(float(monto_max)),
            'capacidad_cuota': int(capacidad_cuota),
            'promedio_pago_real': int(promedio_pago_real),
            'cuotas_tipicas': int(cuotas_avg),
            'factor_score': factor_score,
            'factor_recencia': factor_recencia,
            'factor_vigente': factor_vigente,
            'dias_desde_ultima_actividad': dias,
            'bloqueado': False,
            'razon': f'Basado en {liquidados} crédito(s) pagado(s). Score {nivel} ({score}/100).',
        }

    return {
        'score': score,
        'nivel': nivel,
        'sin_historial': total_visitas == 0 and total_creditos == 0,
        'cupo_recomendado': cupo_recomendado,
        'justificacion': justificacion,
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
@requiere_acceso_tienda
def list_clientes(request, tienda_id=None):
    '''obtenemos todos los clientes'''

    user = request.user

    if tienda_id:
        tienda = Tienda.objects.filter(id=tienda_id).first()
    else:
        tienda = Tienda.objects.filter(id=user.perfil.tienda.id).first()
    clientes = Cliente.objects.filter(tienda=tienda.id).order_by('nombres')
    if clientes:
        clientes_serializer = ClienteSerializer(clientes, many=True)
        return Response(clientes_serializer.data, status=status.HTTP_200_OK)
    return Response({'message': 'No se han creado clientes'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
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
@requiere_acceso_tienda
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
        if not usuario_puede_acceder_tienda(request.user, cliente.tienda_id):
            return respuesta_sin_permiso()
        cliente_serializer = ClienteSerializer(cliente, many=False)
        return Response(cliente_serializer.data, status=status.HTTP_200_OK)
    else:
        return Response({'message': 'No se encontró el cliente'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@requiere_acceso_tienda
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
        if not usuario_puede_acceder_tienda(request.user, cliente.tienda_id):
            return respuesta_sin_permiso()
        cliente_serializer = ClienteSerializer(cliente, data=request.data)
        if cliente_serializer.is_valid():
            cliente_serializer.save()
            return Response(cliente_serializer.data, status=status.HTTP_200_OK)
        return Response(cliente_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    return Response({'message': 'No existe el cliente'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
def delete_cliente(request, pk):
    cliente = Cliente.objects.filter(id=pk).first()
    if cliente and not usuario_puede_acceder_tienda(request.user, cliente.tienda_id):
        return respuesta_sin_permiso()
    ventas = Venta.objects.filter(cliente=cliente.id)
    if cliente:
        if ventas:
            return Response({'message': 'No se puede eliminar el cliente ya que tiene ventas activas'}, status=status.HTTP_202_ACCEPTED)
        cliente.delete()
        return Response({'message': 'Cliente eliminado correctamente'}, status=status.HTTP_200_OK)
    return Response({'message': 'Cliente no existe!'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@requiere_acceso_tienda
def buscar_cliente_por_doc(request, doc, tienda_id):
    """Busca un cliente por documento dentro de las rutas del mismo administrador.
    Solo devuelve datos si el cliente pertenece a una ruta propia (no de otro admin).
    """
    tienda_destino = Tienda.objects.filter(id=tienda_id).first()
    if not tienda_destino:
        return Response({'found': False}, status=status.HTTP_200_OK)

    cliente = (
        Cliente.objects
        .filter(identificacion=doc, tienda__administrador=request.user)
        .exclude(tienda_id=tienda_id)
        .select_related('tienda')
        .first()
    )
    if not cliente:
        return Response({'found': False}, status=status.HTTP_200_OK)

    return Response({
        'found': True,
        'ruta_origen': cliente.tienda.nombre,
        'nombres': cliente.nombres,
        'apellidos': cliente.apellidos,
        'telefono_principal': cliente.telefono_principal,
        'direccion': cliente.direccion,
        'nombre_local': cliente.nombre_local,
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def score_cliente(request, pk, tienda_id):
    """Score crediticio individual de un cliente."""
    cliente = Cliente.objects.filter(id=pk, tienda_id=tienda_id).first()
    if not cliente:
        return Response({'message': 'Cliente no encontrado'}, status=status.HTTP_404_NOT_FOUND)
    return Response(_calcular_score(pk, tienda_id), status=status.HTTP_200_OK)


@api_view(['GET'])
@requiere_acceso_tienda
def scores_tienda(request, tienda_id):
    """Score crediticio de todos los clientes de una tienda (bulk)."""
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    clientes = Cliente.objects.filter(tienda=tienda_id).values_list('id', flat=True)
    result = {cid: _calcular_score(cid, tienda_id) for cid in clientes}
    return Response(result, status=status.HTTP_200_OK)
