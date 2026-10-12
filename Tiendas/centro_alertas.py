"""Excepciones administrativas en lectura; no sincroniza ni envía mensajes."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
import unicodedata

from django.db.models import F
from django.utils import timezone

from Tiendas.alertas_operativas import TIPOS_ALERTA_CARTERA
from Tiendas.fecha_operativa import fecha_operativa
from Tiendas.models import AlertaOperativa, Cierre_Caja, MovimientoCaja
from Ventas.models import Venta

PRIORIDADES = {'critica': 3, 'alta': 2, 'media': 1}


def normalizar(texto):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(texto or '').lower())
        if not unicodedata.combining(c))


def fecha_cierre_alerta(alerta):
    try:
        return date.fromisoformat((alerta.datos or {}).get('fecha', ''))
    except (ValueError, TypeError):
        return None


def abiertas_vigentes(rutas):
    """No muestra como pendientes alertas cuya condición ya desapareció.

    La resolución durable se hace en las escrituras autorizadas/mantenimiento,
    nunca al abrir esta vista. Las señales de originación conservan su revisión.
    """
    ids = [r.id for r in rutas]
    cerradas = Venta.objects.filter(tienda_id__in=ids,
        estado_venta__in=('Pagado', 'Perdida')).values_list('id', flat=True)
    base = AlertaOperativa.objects.filter(tienda_id_ref__in=ids,
        estado__in=('nueva', 'revisada')).exclude(
        tipo__in=TIPOS_ALERTA_CARTERA, venta_id_ref__in=cerradas)
    cierres = set(Cierre_Caja.objects.filter(tienda_id__in=ids)
        .values_list('tienda_id', 'fecha_cierre'))
    obsoletas = [a.id for a in base.filter(tipo='CIERRE_AUSENTE')
        if (a.tienda_id_ref, fecha_cierre_alerta(a)) in cierres]
    return base.exclude(id__in=obsoletas)


def controles_caja(rutas):
    """Compara la cadena auditada y su saldo actual, no cierres date-only.

    Una sola consulta captura movimientos y caja actual en el mismo snapshot.
    No reconstruye desembolsos a partir de ventas o fechas retroactivas.
    """
    rutas_map = {r.id: r for r in rutas}
    movimientos = MovimientoCaja.objects.filter(tienda_id__in=rutas_map).annotate(
        caja_actual=F('tienda__caja_inicial')).order_by('creado_en', 'id').values(
        'id', 'tienda_id', 'delta', 'saldo_anterior', 'saldo_posterior',
        'caja_actual', 'creado_en')
    previos, errores, ultimos = {}, defaultdict(list), {}
    for m in movimientos:
        tid = m['tienda_id']
        if abs(m['saldo_anterior'] + m['delta'] - m['saldo_posterior']) > Decimal('0.01'):
            errores[tid].append(f'Movimiento #{m["id"]}: el cambio no coincide con sus saldos.')
        if tid in previos and abs(previos[tid] - m['saldo_anterior']) > Decimal('0.01'):
            errores[tid].append(f'Movimiento #{m["id"]}: la cadena de saldos tiene una discontinuidad.')
        previos[tid] = m['saldo_posterior']
        ultimos[tid] = m
    casos = []
    for tid, ultimo in ultimos.items():
        diferencia = ultimo['caja_actual'] - ultimo['saldo_posterior']
        if abs(diferencia) > Decimal('0.01'):
            errores[tid].append('La caja actual no coincide con el último movimiento auditado.')
        if not errores[tid]:
            continue
        casos.append({
            'key': f'caja:{tid}', 'origen': 'caja', 'tipo': 'INTEGRIDAD_CAJA',
            'severidad': 'critica', 'estado': 'nueva', 'titulo': 'Registros de caja para revisar',
            'detalle': ' '.join(errores[tid][:5]), 'monto': float(abs(diferencia)),
            'monto_etiqueta': 'Diferencia entre registros',
            'tienda': {'id': tid, 'nombre': rutas_map[tid].nombre},
            'fecha': ultimo['creado_en'].isoformat(), 'venta_id': None, 'cliente': None,
            'alerta_id': None, 'revisiones': [],
            'ayuda': 'Contrasta los movimientos auditados. Este control no confirma el efectivo físico ni demuestra fraude.',
        })
    return casos, [r.nombre for r in rutas if r.id not in ultimos]


def pendientes(rutas):
    # Imports locales: las vistas históricas reutilizan estas funciones.
    from Tiendas.views import casos_inconsistencias, _serializar_alerta_operativa
    from Clientes.models import Cliente
    from Ventas.views import _anotar_ventas_lista
    from Ventas.serializers import VentaListaSerializer

    rutas = list(rutas)
    ruta_map = {r.id: r for r in rutas}
    alertas = list(abiertas_vigentes(rutas).select_related('trabajador'))
    ids_riesgo = [a.venta_id_ref for a in alertas if a.tipo in TIPOS_ALERTA_CARTERA]
    # Una consulta agregada por ruta: no hay consultas por cada crédito.
    ventas, perfiles = {}, {}
    serializer = VentaListaSerializer()
    for ruta in rutas:
        consulta = fecha_operativa(ruta)
        qs = Venta.objects.filter(tienda_id=ruta.id, id__in=ids_riesgo).exclude(
            estado_venta__in=('Pagado', 'Perdida'))
        for venta in _anotar_ventas_lista(qs, consulta=consulta):
            ventas[venta.id] = venta
            perfiles[venta.id] = serializer.get_riesgo_cartera(venta)
    clientes = {c.id: c for c in Cliente.objects.filter(
        id__in=[a.cliente_id_ref for a in alertas if a.cliente_id_ref], tienda_id__in=ruta_map)}
    casos, cierres = [], defaultdict(list)
    for alerta in alertas:
        if alerta.tipo == 'CIERRE_AUSENTE':
            cierres[alerta.tienda_id_ref].append(alerta)
            continue
        if alerta.tipo == 'RESUMEN_OPERATIVO':
            continue
        data = _serializar_alerta_operativa(alerta, ruta_map, clientes)
        venta = ventas.get(alerta.venta_id_ref)
        monto = None
        if alerta.tipo in TIPOS_ALERTA_CARTERA:
            perfil = perfiles.get(alerta.venta_id_ref)
            if not perfil or perfil['nivel_cobranza'] < 2:
                continue  # La cobranza habitual permanece en las vistas operativas.
            data['severidad'] = 'critica' if perfil['nivel_cobranza'] >= 3 else 'alta'
            data['titulo'] = 'Crédito con atraso importante'
            data['detalle'] = (f'{perfil["cuotas_atrasadas"]} cuotas atrasadas · '
                f'{perfil["dias_sin_abono"]} jornadas completas sin abono. {perfil["motivo"]}.')
            monto = float(venta.saldo_actual or 0)
        casos.append({**data, 'key': f'alerta:{alerta.id}', 'origen': 'alerta',
            'alerta_id': alerta.id, 'monto': monto, 'monto_etiqueta': 'Saldo pendiente',
            'fecha': alerta.actualizada.isoformat(),
            'revisiones': (alerta.datos or {}).get('revisiones', []),
            'ayuda': ('Verifica cuotas, pagos y gestiones. El saldo no equivale a pérdida definitiva.'
                if alerta.tipo in TIPOS_ALERTA_CARTERA else
                'Contrasta la autorización del crédito y las señales detectadas. Una coincidencia puede tener una explicación válida.'),
        })
    for tid, alertas_cierre in cierres.items():
        fechas = sorted({fecha_cierre_alerta(a) for a in alertas_cierre
            if fecha_cierre_alerta(a)})
        casos.append({
            'key': f'cierres:{tid}', 'origen': 'cierres', 'tipo': 'CIERRE_AUSENTE',
            'severidad': 'alta', 'estado': 'revisada' if all(a.estado == 'revisada' for a in alertas_cierre) else 'nueva',
            'titulo': f'{len(alertas_cierre)} cierre(s) pendiente(s)',
            'detalle': f'Desde {fechas[0]} hasta {fechas[-1]}.' if fechas else 'Fechas pendientes de verificar.',
            'tienda': {'id': tid, 'nombre': ruta_map[tid].nombre},
            'monto': None, 'fecha': max(a.actualizada for a in alertas_cierre).isoformat(),
            'cliente': None, 'venta_id': None, 'alerta_id': None, 'revisiones': [],
            'fechas_pendientes': [str(f) for f in fechas],
            'ayuda': 'Revisa las fechas en Cierre de caja. No completes cierres antiguos sin comprobar sus registros.',
        })
    auditoria = casos_inconsistencias(list(ruta_map))
    for caso in auditoria['casos']:
        casos.append({**caso, 'key': f'integridad:{caso["venta_id"]}',
            'origen': 'integridad', 'tipo': 'INCONSISTENCIA', 'estado': 'nueva',
            'monto': caso['monto_a_revisar'], 'monto_etiqueta': 'Monto a revisar',
            'fecha': auditoria['actualizado'], 'alerta_id': None, 'revisiones': [],
            'ayuda': 'Compara crédito y recaudos. La señal desaparece al corregir su causa; no cambia el saldo por sí sola.',
        })
    caja, sin_historial = controles_caja(rutas)
    casos.extend(caja)
    casos.sort(key=lambda c: (-PRIORIDADES.get(c['severidad'], 0),
        -(c.get('monto') or 0), c['key']))
    return casos, sin_historial


def registrar_revision(alerta, estado, *, usuario=None, nota='', origen='web', actor=None):
    """Llamar con la alerta bloqueada dentro de la transacción del solicitante."""
    datos = dict(alerta.datos or {})
    historial = list(datos.get('revisiones') or [])
    historial.append({'fecha': timezone.now().isoformat(),
        'anterior': alerta.estado, 'nuevo': estado, 'nota': nota,
        'usuario_id': usuario.id if usuario else None,
        'actor': actor or (usuario.get_full_name() or usuario.username if usuario else 'Administrador'),
        'origen': origen})
    datos['revisiones'] = historial
    alerta.estado = estado
    alerta.datos = datos
    alerta.save(update_fields=['estado', 'datos', 'actualizada'])
