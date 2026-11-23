"""API de solo lectura para el asistente personal del administrador (Jarvis).

- Autenticación propia: ``Authorization: Bearer <AGENTE_JARVIS_TOKEN>``. El token
  vive en ``sell_system/variables.py`` del VPS; sin token configurado la API
  queda cerrada.
- Alcance: rutas activas administradas por el usuario de alertas
  (``TELEGRAM_ALERTAS_USERNAME``) según ``Tienda_Administrador``, igual que el
  reporte diario de Telegram.
- Solo lectura garantizada: cada consulta corre en una transacción que siempre
  se revierte y, en PostgreSQL, se declara ``READ ONLY`` (cualquier escritura falla).
- Datos mínimos de clientes: nombre, montos y estados. No se exponen teléfonos,
  documentos ni direcciones.
- Los importes no se suman entre rutas: pueden estar en monedas distintas.
"""
import hashlib
import hmac
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Exists, Max, OuterRef, Q, Sum
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from Recaudos.models import Recaudo
from Tiendas.fecha_operativa import fecha_operativa
from Tiendas.models import AlertaOperativa, Cierre_Caja, Tienda
from Trabajadores.models import Perfil
from Ventas.models import Venta

LIMITE_LISTA = 100
LIMITE_BUSQUEDA = 20


class AgenteThrottle(SimpleRateThrottle):
    """Límite propio: no comparte cuota con los usuarios de la plataforma."""
    scope = 'agente_jarvis'
    rate = '120/min'

    def get_cache_key(self, request, view):
        return 'throttle_agente_jarvis'


def _token_valido(request):
    esperado = getattr(settings, 'AGENTE_JARVIS_TOKEN', '') or ''
    if len(esperado) < 32:
        return False
    cabecera = request.headers.get('Authorization', '')
    if not cabecera.lower().startswith('bearer '):
        return False
    recibido = cabecera[7:].strip()
    # Se comparan hashes para no filtrar la longitud del token esperado.
    return hmac.compare_digest(hashlib.sha256(recibido.encode()).digest(),
                               hashlib.sha256(esperado.encode()).digest())


def solo_lectura(funcion):
    """Ejecuta ``funcion`` sin posibilidad de persistir cambios."""
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
        try:
            return funcion()
        finally:
            transaction.set_rollback(True)


def endpoint_agente(vista):
    """Decoradores comunes: sin sesión de usuario, token propio y solo GET."""
    def envoltura(request, *args, **kwargs):
        if not _token_valido(request):
            return Response({'error': 'No autorizado'}, status=status.HTTP_401_UNAUTHORIZED)
        respuesta = solo_lectura(lambda: vista(request, *args, **kwargs))
        respuesta['Cache-Control'] = 'no-store'
        return respuesta
    envoltura.__name__ = vista.__name__
    envoltura.__doc__ = vista.__doc__
    return api_view(['GET'])(
        authentication_classes([])(permission_classes([AllowAny])(
            throttle_classes([AgenteThrottle])(envoltura))))


def _n(valor):
    """Decimal → número JSON (entero si no tiene centavos)."""
    monto = Decimal(str(valor or 0))
    return int(monto) if monto == monto.to_integral_value() else float(monto)


def _alcance():
    from Tiendas.alertas_operativas import rutas_del_usuario, usuario_alertas_configurado
    return rutas_del_usuario(usuario_alertas_configurado()).order_by('nombre')


def _filtrar_rutas(request):
    """Filtro opcional ``ruta`` por id exacto o parte del nombre."""
    rutas = _alcance()
    filtro = (request.query_params.get('ruta') or '').strip()
    if not filtro:
        return rutas
    if filtro.isdigit():
        return rutas.filter(id=int(filtro))
    return rutas.filter(nombre__icontains=filtro)


def _fecha_ruta(tienda, valor):
    """Resuelve ``hoy``/``ayer``/YYYY-MM-DD en la zona horaria de la ruta."""
    hoy = fecha_operativa(tienda)
    texto = (valor or 'hoy').strip().lower()
    if texto == 'hoy':
        return hoy, None
    if texto == 'ayer':
        return hoy - timedelta(days=1), None
    try:
        fecha = date.fromisoformat(texto)
    except ValueError:
        return None, 'Fecha inválida: usa hoy, ayer o YYYY-MM-DD.'
    if fecha > hoy:
        return None, 'No hay datos de fechas futuras.'
    return fecha, None


def _trabajadores(tienda):
    perfiles = Perfil.objects.filter(tienda=tienda).select_related('trabajador')
    nombres = []
    for perfil in perfiles:
        usuario = perfil.trabajador
        nombres.append((usuario.get_full_name() or usuario.username).strip())
    return sorted(set(n for n in nombres if n))


def _credito_resumen(detalle):
    return {'cliente': detalle['cliente'], 'venta_id': detalle['venta_id'],
            'cuota': _n(detalle['cuota']), 'abonado': _n(detalle['abonado']),
            'pendiente': _n(detalle['pendiente'])}


@endpoint_agente
def rutas(request):
    """Rutas en alcance con su trabajador, créditos activos y cartera."""
    from Tiendas.alertas_operativas import ESTADOS_ACTIVOS
    datos = []
    for tienda in _alcance():
        activas = Venta.objects.filter(tienda=tienda, estado_venta__in=ESTADOS_ACTIVOS)
        datos.append({
            'id': tienda.id, 'nombre': tienda.nombre, 'zona_horaria': tienda.zona_horaria,
            'fecha_operativa': fecha_operativa(tienda).isoformat(),
            'trabajadores': _trabajadores(tienda),
            'creditos_activos': activas.count(),
            'cartera': _n(activas.aggregate(total=Sum('saldo_actual'))['total']),
        })
    return Response({'nota': 'Los importes de cada ruta están en su propia moneda; no se suman entre rutas.',
                     'total_rutas': len(datos), 'rutas': datos})


@endpoint_agente
def jornada(request):
    """Cumplimiento de una jornada por ruta: quién pagó, quién no y quién quedó sin registro."""
    from Tiendas.reporte_rutas import cumplimiento_dia
    detalle = request.query_params.get('detalle', '1') != '0'
    datos = []
    for tienda in _filtrar_rutas(request):
        fecha, error = _fecha_ruta(tienda, request.query_params.get('fecha'))
        if error:
            return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
        c = cumplimiento_dia(tienda, fecha)
        cierre = Cierre_Caja.objects.filter(tienda=tienda, fecha_cierre=fecha).first()
        hubo_actividad = (Venta.objects.filter(tienda=tienda, fecha_venta=fecha).exists()
                          or Recaudo.objects.filter(tienda=tienda, fecha_recaudo=fecha).exists())
        ruta = {
            'id': tienda.id, 'nombre': tienda.nombre, 'fecha': fecha.isoformat(),
            'trabajadores': _trabajadores(tienda),
            'cuotas_del_dia': _n(c['esperado']), 'recaudado': _n(c['recaudo']),
            'cuotas_cubiertas': _n(c['cobertura_recaudo']), 'cuotas_pendientes': _n(c['pendiente_recaudo']),
            'cobros_adicionales': _n(c['cobros_adicionales']),
            'creditos_programados': c['programadas'], 'con_abono': c['con_abono'],
            'no_pagaron': c['sin_abono'], 'sin_registro': c['sin_gestion'],
            'ventas_nuevas': Venta.objects.filter(tienda=tienda, fecha_venta=fecha).count(),
            'cierre_caja': {'registrado': cierre is not None, 'valor': _n(cierre.valor) if cierre else None,
                            'faltante': hubo_actividad and cierre is None},
        }
        if detalle:
            creditos = c['creditos_detalle']
            ruta['clientes_no_pagaron'] = [_credito_resumen(d) for d in creditos if d['estado'] == 'sin_abono'][:LIMITE_LISTA]
            ruta['clientes_sin_registro'] = [_credito_resumen(d) for d in creditos if d['estado'] == 'sin_registro'][:LIMITE_LISTA]
            ruta['clientes_abono_parcial'] = [_credito_resumen(d) for d in creditos
                                              if d['estado'] == 'con_abono' and Decimal(d['pendiente']) > 0][:LIMITE_LISTA]
        datos.append(ruta)
    return Response({
        'interpretacion': ('no_pagaron = visita registrada sin abono; sin_registro = cliente con cuota ese día '
                           'sin abono ni visita registrada (no prueba que no se visitó); los importes de cada '
                           'ruta están en su propia moneda.'),
        'rutas': datos})


@endpoint_agente
def cartera(request):
    """Estado de cartera por ruta: atrasos, vencidos, sin primer abono y riesgo."""
    from Tiendas.alertas_operativas import resumen_operativo
    datos = []
    for tienda in _filtrar_rutas(request):
        hoy = fecha_operativa(tienda)
        resumen = resumen_operativo(hoy, rutas=Tienda.objects.filter(id=tienda.id))
        if not resumen:
            continue
        r = resumen[0]
        datos.append({
            'id': r['id'], 'nombre': r['nombre'], 'fecha_operativa': hoy.isoformat(),
            'caja': _n(r['caja']), 'cartera': _n(r['cartera']), 'creditos_activos': r['activas'],
            'atrasados': r['atrasados'], 'saldo_atrasado': _n(r['saldo_atrasado']),
            'vencidos': r['vencidos'], 'saldo_vencido': _n(r['saldo_vencido']),
            'sin_primer_abono': r['sin_primer_abono'], 'saldo_sin_primer_abono': _n(r['saldo_sin_primer_abono']),
            'en_riesgo': r['riesgo'], 'saldo_en_riesgo': _n(r['saldo_riesgo']),
            'alertas_abiertas': r['alertas_riesgo'], 'alertas_criticas': r['alertas_criticas'],
            'principales_riesgos': [{'severidad': x['severidad'], 'cliente': x['cliente'],
                                     'venta_id': x['venta_id'], 'saldo': _n(x['saldo'])}
                                    for x in r['riesgos_detalle']],
        })
    return Response({'nota': 'Los importes de cada ruta están en su propia moneda; no se suman entre rutas.',
                     'rutas': datos})


@endpoint_agente
def alertas(request):
    """Alertas abiertas de las rutas en alcance."""
    from Tiendas.alertas_operativas import ESTADOS_ALERTA_ABIERTA
    rutas_ids = {t.id: t.nombre for t in _filtrar_rutas(request)}
    consulta = AlertaOperativa.objects.filter(tienda_id_ref__in=list(rutas_ids), estado__in=ESTADOS_ALERTA_ABIERTA)
    severidad = (request.query_params.get('severidad') or '').strip().lower()
    if severidad:
        consulta = consulta.filter(severidad=severidad)
    alertas_lista = list(consulta.order_by('-actualizada', '-id')[:LIMITE_LISTA])
    ventas = {v.id: v for v in Venta.objects.filter(
        id__in=[a.venta_id_ref for a in alertas_lista if a.venta_id_ref]).select_related('cliente')}
    datos = []
    for a in alertas_lista:
        venta = ventas.get(a.venta_id_ref)
        datos.append({
            'id': a.id, 'tipo': a.tipo, 'severidad': a.severidad, 'estado': a.estado, 'titulo': a.titulo,
            'ruta': rutas_ids.get(a.tienda_id_ref),
            'cliente': f'{venta.cliente.nombres} {venta.cliente.apellidos}'.strip() if venta else None,
            'saldo': _n(venta.saldo_actual) if venta else None,
            'creada': a.creada.isoformat(), 'actualizada': a.actualizada.isoformat(),
        })
    return Response({'total': consulta.count(), 'alertas': datos})


@endpoint_agente
def cliente(request):
    """Créditos de un cliente por nombre, dentro de las rutas en alcance."""
    texto = ' '.join((request.query_params.get('buscar') or '').split())
    if len(texto) < 3:
        return Response({'error': 'Escribe al menos 3 letras del nombre del cliente.'}, status=status.HTTP_400_BAD_REQUEST)
    filtro = Q()
    for palabra in texto.split()[:4]:
        filtro &= Q(cliente__nombres__icontains=palabra) | Q(cliente__apellidos__icontains=palabra)
    abonos_reales = Recaudo.objects.filter(venta_id=OuterRef('pk'), valor_recaudo__gt=0, es_renovacion=False)
    ventas = (Venta.objects.filter(filtro, tienda__in=_filtrar_rutas(request))
              .exclude(estado_venta='Perdida').select_related('cliente', 'tienda')
              .annotate(ultimo_abono=Max('recaudo__fecha_recaudo',
                                         filter=Q(recaudo__valor_recaudo__gt=0, recaudo__es_renovacion=False)),
                        tiene_abono=Exists(abonos_reales))
              .order_by('cliente__nombres', '-fecha_venta')[:LIMITE_BUSQUEDA])
    datos = []
    for v in ventas:
        datos.append({
            'cliente': f'{v.cliente.nombres} {v.cliente.apellidos}'.strip(), 'ruta': v.tienda.nombre,
            'venta_id': v.id, 'estado': v.estado_venta, 'plazo': v.plazo,
            'fecha_venta': v.fecha_venta.isoformat() if v.fecha_venta else None,
            'valor_venta': _n(v.valor_venta), 'saldo_actual': _n(v.saldo_actual), 'cuotas': v.cuotas,
            'ultimo_abono': v.ultimo_abono.isoformat() if v.ultimo_abono else None,
            'dias_sin_abono': v.dias_sin_abono() if v.estado_venta not in ('Pagado',) else 0,
            'sin_primer_abono': not v.tiene_abono,
        })
    return Response({'buscar': texto, 'total': len(datos), 'creditos': datos})
