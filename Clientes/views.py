from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.pagination import LimitOffsetPagination
from collections import defaultdict
from datetime import timedelta

from Clientes.models import Cliente
from Tiendas.models import Tienda
from Ventas.models import Venta
from Ventas.riesgo import UMBRALES_DSA, dias_completos_sin_abono
from Recaudos.models import Recaudo
from django.utils import timezone

from Clientes.serializers import ClienteSerializer, ClienteCreateSerializer
from Tiendas.permissions import requiere_acceso_tienda, usuario_puede_acceder_tienda, respuesta_sin_permiso


def _calcular_score_desde_datos(cliente_id, tienda, ventas, recaudos,
                                ids_ventas_renovadas, hoy=None,
                                recaudos_exitosos_override=None):
    """Calcula el score con datos ya cargados, sin consultas por cliente.

    Esta función conserva las reglas del score individual. La separación entre
    carga de datos y cálculo permite que el endpoint bulk lea la ruta completa
    en pocas consultas y luego procese los clientes en memoria.
    """
    hoy = hoy or timezone.localdate()
    recaudos_por_venta = defaultdict(list)
    for recaudo in recaudos:
        recaudos_por_venta[recaudo['venta_id']].append(recaudo)

    # Solo recaudos reales — excluye los generados al renovar.
    recaudos_reales = [r for r in recaudos if not r['es_renovacion']]
    visitas_recientes = sorted(
        recaudos_reales,
        key=lambda r: (r['fecha_recaudo'], r['id']),
        reverse=True,
    )[:20]
    pagos = sum(1 for r in recaudos_reales if r['visita_blanco_id'] is None)
    no_pagos = len(recaudos_reales) - pagos
    total_visitas = len(recaudos_reales)
    pagos_recientes = sum(1 for r in visitas_recientes if r['visita_blanco_id'] is None)
    tasa_reciente = pagos_recientes / len(visitas_recientes) if visitas_recientes else None
    racha_fallas = 0
    for recaudo in visitas_recientes:
        if recaudo['visita_blanco_id'] is not None:
            racha_fallas += 1
        else:
            break
    tasa_historica = pagos / total_visitas if total_visitas > 0 else None

    comp_reciente = round(tasa_reciente * 30, 1) if tasa_reciente is not None else 15.0
    comp_historico = round(tasa_historica * 15, 1) if tasa_historica is not None else 7.5

    vencidos = sum(1 for v in ventas if v['estado_venta'] == 'Vencido')
    atrasados = sum(1 for v in ventas if v['estado_venta'] == 'Atrasado')
    renovaciones = sum(1 for v in ventas if v['id'] in ids_ventas_renovadas)
    activas = [
        v for v in ventas
        if v['estado_venta'] in ['Vigente', 'Atrasado', 'Vencido']
    ]

    dias_sin_abono_max = 0
    peor_bucket = 0
    for venta in activas:
        pagos_reales = [
            r for r in recaudos_por_venta.get(venta['id'], [])
            if r['valor_recaudo'] > 0 and not r['es_renovacion']
        ]
        ultimo_abono = max(
            (r['fecha_recaudo'] for r in pagos_reales),
            default=None,
        )
        referencia = ultimo_abono or venta['fecha_venta']
        dsa = dias_completos_sin_abono(referencia, hoy)
        sano, leve, grave = UMBRALES_DSA.get(venta['plazo'], UMBRALES_DSA['Diario'])
        bucket = 0 if dsa <= sano else 1 if dsa <= leve else 2 if dsa <= grave else 3
        dias_sin_abono_max = max(dias_sin_abono_max, dsa)
        peor_bucket = max(peor_bucket, bucket)

    if (vencidos + renovaciones) > 0 or peor_bucket == 3:
        comp_activos = 0
    elif peor_bucket == 2:
        comp_activos = 10
    elif peor_bucket == 1 or atrasados > 0:
        comp_activos = 18
    else:
        comp_activos = 25

    total_creditos = len(ventas)
    perdidos = sum(1 for v in ventas if v['estado_venta'] == 'Perdida')
    tasa_perdidos = perdidos / total_creditos if total_creditos > 0 else 0
    comp_perdidos = round((1 - tasa_perdidos) * 20, 1)
    liquidados = sum(
        1 for v in ventas
        if v['estado_venta'] == 'Pagado' and v['id'] not in ids_ventas_renovadas
    )
    comp_historial = round(min(liquidados / 5, 1) * 10, 1)

    score = max(0, min(100, round(
        comp_reciente + comp_historico + comp_activos + comp_perdidos + comp_historial)))

    senales = []
    if perdidos > 0:
        score = min(score, 30)
        senales.append(f'{perdidos} crédito(s) perdido(s)')
    if peor_bucket == 3 and activas:
        score = min(score, 40)
        senales.append(f'{dias_sin_abono_max} días sin abono en crédito vigente')
    if racha_fallas >= 5:
        score = min(score, 50)
        senales.append(f'{racha_fallas} fallas consecutivas')
    renovacion_reciente = any(
        r['es_renovacion'] and r['fecha_recaudo'] >= hoy - timedelta(days=90)
        for r in recaudos
    )
    if renovacion_reciente:
        score = min(score, 55)
        senales.append('Renovación de deuda en los últimos 90 días')
    deterioro_tendencia = (
        tasa_reciente is not None and tasa_historica is not None
        and (tasa_historica - tasa_reciente) >= 0.15
    )
    if deterioro_tendencia:
        senales.append('Tasa de pago reciente muy por debajo de su historial')

    if score >= 80:
        nivel = 'Excelente'
    elif score >= 60:
        nivel = 'Bueno'
    elif score >= 40:
        nivel = 'Regular'
    else:
        nivel = 'Riesgo'

    try:
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
        creditos_pagados = [
            v for v in ventas
            if v['estado_venta'] == 'Pagado' and v['id'] not in ids_ventas_renovadas
        ]
        recaudos_exitosos = recaudos_exitosos_override or sorted(
            [r for r in recaudos_reales if r['visita_blanco_id'] is None],
            key=lambda r: r['fecha_recaudo'],
            reverse=True,
        )[:90]
        promedio_pago_real = (
            sum(float(r['valor_recaudo']) for r in recaudos_exitosos) / len(recaudos_exitosos)
            if recaudos_exitosos else 0
        )

        cuotas_pagadas = [v['cuotas'] for v in creditos_pagados]
        cuotas_todas = [v['cuotas'] for v in ventas]
        cuotas_avg = (
            sum(cuotas_pagadas) / len(cuotas_pagadas) if cuotas_pagadas
            else sum(cuotas_todas) / len(cuotas_todas) if cuotas_todas
            else 30
        )
        capacidad_cuota = promedio_pago_real * float(cuotas_avg)

        monto_max_pagado_valor = max(
            (v['valor_venta'] for v in creditos_pagados),
            default=0,
        )
        monto_max_pagados = float(monto_max_pagado_valor)
        ultimo_pagado = max(
            creditos_pagados,
            key=lambda v: v['fecha_venta'],
            default=None,
        )
        ultimo_monto = float(ultimo_pagado['valor_venta']) if ultimo_pagado else 0.0

        if ultimo_pagado:
            base_historica = max(monto_max_pagados, ultimo_monto * 1.2)
            base = min(base_historica, capacidad_cuota) if capacidad_cuota > 0 else base_historica
        else:
            base_historica = cupo_minimo
            base = cupo_minimo

        if score >= 80:
            factor_score = 1.25
        elif score >= 60:
            factor_score = 1.00
        elif score >= 40:
            factor_score = 0.70
        else:
            factor_score = 0.40

        factor_tendencia = 0.60 if deterioro_tendencia else 1.00
        ultima_fecha = max(
            (r['fecha_recaudo'] for r in recaudos_exitosos),
            default=None,
        )
        if ultima_fecha is None and ultimo_pagado:
            ultima_fecha = ultimo_pagado['fecha_venta']

        if ultima_fecha:
            dias = (hoy - ultima_fecha).days
            if dias < 90:
                factor_recencia = 1.00
            elif dias < 180:
                factor_recencia = 0.85
            elif dias < 365:
                factor_recencia = 0.70
            else:
                factor_recencia = 0.50
        else:
            dias = 0
            factor_recencia = 1.00

        factor_vigente = 0.60 if atrasados > 0 else 1.00
        cupo_calculado = base * factor_score * factor_recencia * factor_vigente * factor_tendencia
        piso = ultimo_monto * 0.5 if (ultimo_monto > 0 and score >= 60) else 0
        if ultimo_monto > 0:
            techo = min(ultimo_monto * 1.5, monto_max_pagados * 2)
        else:
            techo = cupo_minimo * 1.5
        cupo_bruto = max(piso, min(techo, cupo_calculado))

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
            'monto_maximo_pagado': int(monto_max_pagados),
            'capacidad_cuota': int(capacidad_cuota),
            'promedio_pago_real': int(promedio_pago_real),
            'cuotas_tipicas': int(cuotas_avg),
            'factor_score': factor_score,
            'factor_recencia': factor_recencia,
            'factor_vigente': factor_vigente,
            'factor_tendencia': factor_tendencia,
            'dias_desde_ultima_actividad': dias,
            'bloqueado': False,
            'razon': f'Basado en {liquidados} crédito(s) pagado(s). Score {nivel} ({score}/100).',
        }

    saldo_vigente = sum(float(v['saldo_actual'] or 0) for v in activas)
    saldo_vencido = sum(
        float(v['saldo_actual'] or 0) for v in activas
        if v['estado_venta'] == 'Vencido'
    )
    saldo_atrasado = sum(
        float(v['saldo_actual'] or 0) for v in activas
        if v['estado_venta'] == 'Atrasado'
    )
    capital_perdido = sum(
        float(v['saldo_actual'] or 0) for v in ventas
        if v['estado_venta'] == 'Perdida'
    )
    cupo_disponible = max(0, cupo_recomendado - int(round(saldo_vigente)))

    return {
        'score': score,
        'nivel': nivel,
        'sin_historial': total_visitas == 0 and total_creditos == 0,
        'cupo_recomendado': cupo_recomendado,
        'cupo_disponible': cupo_disponible,
        'saldo_vigente': int(round(saldo_vigente)),
        'saldo_vencido': int(round(saldo_vencido)),
        'saldo_atrasado': int(round(saldo_atrasado)),
        'capital_perdido': int(round(capital_perdido)),
        'senales': senales,
        'justificacion': justificacion,
        'detalle': {
            'comp_reciente': comp_reciente,
            'comp_historico': comp_historico,
            'comp_activos': comp_activos,
            'comp_perdidos': comp_perdidos,
            'comp_historial': comp_historial,
            'tasa_reciente': round(tasa_reciente * 100) if tasa_reciente is not None else None,
            'tasa_historica': round(tasa_historica * 100) if tasa_historica is not None else None,
            'racha_fallas': racha_fallas,
            'dias_sin_abono_max': dias_sin_abono_max,
            'pagos': pagos,
            'no_pagos': no_pagos,
            'total_creditos': total_creditos,
            'perdidos': perdidos,
            'liquidados': liquidados,
        },
    }


def _score_data_queryset(cliente_id, tienda_id):
    """Carga los datos de un cliente para el endpoint individual."""
    ventas = list(Venta.objects.filter(
        cliente_id=cliente_id,
        tienda_id=tienda_id,
    ).values(
        'id', 'cliente_id', 'fecha_venta', 'valor_venta', 'cuotas', 'plazo',
        'estado_venta', 'saldo_actual', 'origen_renovacion_id',
    ))
    venta_ids = [v['id'] for v in ventas]
    recaudos = list(Recaudo.objects.filter(venta_id__in=venta_ids).values(
        'id', 'venta_id', 'fecha_recaudo', 'valor_recaudo',
        'visita_blanco_id', 'es_renovacion',
    )) if venta_ids else []
    renovadas = set(Venta.objects.filter(
        origen_renovacion_id__in=venta_ids,
    ).values_list('origen_renovacion_id', flat=True)) if venta_ids else set()
    tienda = Tienda.objects.filter(id=tienda_id).first()
    return tienda, ventas, recaudos, renovadas


def _calcular_score(cliente_id, tienda_id):
    """Score v2 (0-100) y cupo recomendado, estilo behavioral scoring:
    el comportamiento RECIENTE domina sobre el histórico, y las señales de
    deterioro (días sin abono, rachas de fallas, renovación reciente) aplican
    topes duros al score aunque el historial antiguo sea bueno.

    Renovaciones: cuando un crédito vencido se renueva, se genera un Recaudo
    con es_renovacion=True (excluido del conteo de pagos reales) y la venta
    nueva queda vinculada a la vieja via origen_renovacion. La venta vieja
    cierra como 'Pagado' pero NO cuenta como liquidada; se trata como un
    vencido para el componente de salud de créditos activos.
    """
    tienda, ventas, recaudos, renovadas = _score_data_queryset(cliente_id, tienda_id)
    return _calcular_score_desde_datos(
        cliente_id, tienda, ventas, recaudos, renovadas,
    )


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
    """Score crediticio de todos los clientes de una tienda (bulk).

    Se cargan una sola vez las ventas, recaudos y relaciones de renovación de
    la ruta. El formato de respuesta no cambia para el frontend.
    """
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    cliente_ids = list(
        Cliente.objects.filter(tienda_id=tienda_id)
        .order_by('id')
        .values_list('id', flat=True)
    )
    ventas = list(Venta.objects.filter(tienda_id=tienda_id).values(
        'id', 'cliente_id', 'fecha_venta', 'valor_venta', 'cuotas', 'plazo',
        'estado_venta', 'saldo_actual', 'origen_renovacion_id',
    ))
    venta_ids = [v['id'] for v in ventas]
    recaudos = list(Recaudo.objects.filter(venta_id__in=venta_ids).values(
        'id', 'venta_id', 'fecha_recaudo', 'valor_recaudo',
        'visita_blanco_id', 'es_renovacion',
    )) if venta_ids else []
    ids_ventas_renovadas = set(Venta.objects.filter(
        origen_renovacion_id__in=venta_ids,
    ).values_list('origen_renovacion_id', flat=True)) if venta_ids else set()

    ventas_por_cliente = defaultdict(list)
    for venta in ventas:
        ventas_por_cliente[venta['cliente_id']].append(venta)
    recaudos_por_venta = defaultdict(list)
    for recaudo in recaudos:
        recaudos_por_venta[recaudo['venta_id']].append(recaudo)

    hoy = timezone.localdate()
    result = {}
    for cliente_id in cliente_ids:
        ventas_cliente = ventas_por_cliente.get(cliente_id, [])
        recaudos_cliente = [
            recaudo
            for venta in ventas_cliente
            for recaudo in recaudos_por_venta.get(venta['id'], [])
        ]
        exitosos_preordenados = sorted(
            [r for r in recaudos_cliente
             if not r['es_renovacion'] and r['visita_blanco_id'] is None],
            key=lambda r: r['fecha_recaudo'],
            reverse=True,
        )
        recaudos_exitosos_override = None
        # El endpoint histórico no definía el desempate cuando 90 recaudos
        # compartían fecha. Solo en ese caso repetimos su consulta exacta para
        # no alterar el cupo calculado mientras migramos a la carga bulk.
        if len(exitosos_preordenados) > 90 and (
            exitosos_preordenados[89]['fecha_recaudo']
            == exitosos_preordenados[90]['fecha_recaudo']
        ):
            ventas_qs_cliente = Venta.objects.filter(
                cliente_id=cliente_id,
                tienda_id=tienda_id,
            )
            recaudos_exitosos_override = list(
                Recaudo.objects.filter(
                    venta__in=ventas_qs_cliente,
                    visita_blanco__isnull=True,
                    es_renovacion=False,
                )
                .order_by('-fecha_recaudo')[:90]
                .values(
                    'id', 'venta_id', 'fecha_recaudo', 'valor_recaudo',
                    'visita_blanco_id', 'es_renovacion',
                )
            )
        result[cliente_id] = _calcular_score_desde_datos(
            cliente_id,
            tienda,
            ventas_cliente,
            recaudos_cliente,
            ids_ventas_renovadas,
            hoy=hoy,
            recaudos_exitosos_override=recaudos_exitosos_override,
        )
    return Response(result, status=status.HTTP_200_OK)
