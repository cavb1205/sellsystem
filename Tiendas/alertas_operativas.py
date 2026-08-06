"""Reglas operativas y persistencia de alertas para las rutas.

Las reglas son informativas: nunca bloquean la creación de un crédito. Cada
venta que tenga varias señales produce un único mensaje agrupado en Telegram.
Las alertas se guardan antes de notificar para poder deduplicarlas y
consultarlas después desde el asistente privado.
"""
import html
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db import OperationalError
from django.db.models import Exists, OuterRef, Q, Sum
from django.utils import timezone

from Clientes.models import Cliente
from Recaudos.models import Recaudo
from Tiendas import telegram_bot
from Tiendas.models import (
    AlertaOperativa,
    Cierre_Caja,
    Tienda,
    Tienda_Administrador,
)
from Ventas.models import Venta
from Ventas.riesgo import calcular_riesgo_venta


ESTADOS_ACTIVOS = ('Vigente', 'Atrasado', 'Vencido')
RANGO_SEVERIDAD = {'media': 1, 'alta': 2, 'critica': 3}
TIPOS_ALERTA_CARTERA = (
    'RIESGO_CARTERA',
    'SEGUIMIENTO_PREVENTIVO',
    'SIN_PRIMER_ABONO',
)
ESTADOS_ALERTA_ABIERTA = ('nueva', 'revisada')


def _dinero(valor):
    return f'${int(round(float(valor or 0))):,}'.replace(',', '.')


def _escape(valor):
    return html.escape(str(valor or '—'))


def _normalizar(valor):
    return ''.join(ch for ch in str(valor or '').lower() if ch.isalnum())


def _nombre_usuario(usuario):
    if not usuario:
        return 'No identificado'
    return usuario.get_full_name() or usuario.username or 'No identificado'


def usuario_alertas_configurado():
    """Usuario cuyo alcance alimenta el monitoreo diario del bot."""
    username = getattr(
        settings,
        'TELEGRAM_ALERTAS_USERNAME',
        getattr(settings, 'TELEGRAM_ASSISTANT_USERNAME', 'cavb1205'),
    )
    return User.objects.filter(username=username).first()


def rutas_del_usuario(usuario):
    """Devuelve las rutas activas vinculadas al usuario en Tienda_Administrador.

    Para el monitoreo operativo, ``Tienda_Administrador`` es la fuente de
    verdad: una ruta propiedad histórica del usuario no entra al alcance si
    ya no conserva ese vínculo. Así se evita notificar sobre cartera que ya
    no administra.
    """
    if not usuario:
        return Tienda.objects.none()
    try:
        ruta_ids = Tienda_Administrador.objects.filter(
            administrador_id=usuario.id,
        ).values_list('tienda_id', flat=True)
    except OperationalError:
        # Si no existe la tabla, se omite el monitoreo para no ampliar el
        # alcance a rutas históricas por accidente.
        return Tienda.objects.none()
    return Tienda.objects.filter(id__in=ruta_ids, estado=True).distinct()


def venta_en_alcance_alertas(venta):
    """Evita crear alertas fuera del usuario configurado para este monitoreo."""
    return tienda_en_alcance_alertas(venta.tienda_id)


def tienda_en_alcance_alertas(tienda_id):
    """Comprueba el alcance justo antes de crear o enviar una alerta."""
    usuario = usuario_alertas_configurado()
    return bool(
        usuario
        and tienda_id
        and rutas_del_usuario(usuario).filter(id=tienda_id).exists()
    )


def _rutas_activas_del_monitoreo():
    """Alcance único usado para detectar doble financiación entre rutas."""
    return rutas_del_usuario(usuario_alertas_configurado())


def _programar_telegram(alerta):
    """Envía después del commit y nunca hace fallar la operación de negocio."""
    if not tienda_en_alcance_alertas(alerta.tienda_id_ref):
        return

    def enviar():
        # El alcance puede cambiar entre la creación y el commit. Revalidar
        # aquí evita que una ruta desvinculada alcance el chat por una alerta
        # que ya estaba pendiente de notificación.
        if not tienda_en_alcance_alertas(alerta.tienda_id_ref):
            return
        message_id = telegram_bot.notificar_alerta_operativa(
            alerta.detalle,
            alerta_id=alerta.pk,
        )
        if message_id:
            AlertaOperativa.objects.filter(pk=alerta.pk).update(
                telegram_message_id=message_id,
                ultima_notificacion=timezone.now(),
            )

    transaction.on_commit(enviar)


def _guardar_alerta(*, tipo, severidad, clave, titulo, detalle,
                    tienda_id=None, cliente_id=None, venta_id=None,
                    trabajador=None, datos=None, notificar=True):
    if tienda_id is not None and not tienda_en_alcance_alertas(tienda_id):
        return None, False
    alerta, creada = AlertaOperativa.objects.get_or_create(
        clave_dedupe=clave,
        defaults={
            'tipo': tipo,
            'severidad': severidad,
            'titulo': titulo,
            'detalle': detalle,
            'tienda_id_ref': tienda_id,
            'cliente_id_ref': cliente_id,
            'venta_id_ref': venta_id,
            'trabajador': trabajador,
            'datos': datos or {},
        },
    )
    if not creada:
        AlertaOperativa.objects.filter(pk=alerta.pk).update(
            ocurrencias=alerta.ocurrencias + 1,
            actualizada=timezone.now(),
        )
    elif notificar:
        _programar_telegram(alerta)
    return alerta, creada


def _senal(codigo, severidad, texto, **datos):
    return {
        'codigo': codigo,
        'severidad': severidad,
        'texto': texto,
        'datos': datos,
    }


def _clientes_posibles_en_otras_rutas(cliente, rutas):
    """Busca coincidencias fuertes por documento o teléfono normalizados."""
    doc = _normalizar(cliente.identificacion)
    telefono = _normalizar(cliente.telefono_principal)
    candidatos = Cliente.objects.filter(
        tienda_id__in=rutas.values_list('id', flat=True)
    ).exclude(id=cliente.id).select_related('tienda')
    encontrados = []
    for candidato in candidatos:
        mismo_doc = bool(doc) and _normalizar(candidato.identificacion) == doc
        mismo_telefono = bool(telefono) and _normalizar(candidato.telefono_principal) == telefono
        if mismo_doc or mismo_telefono:
            encontrados.append((candidato, mismo_doc, mismo_telefono))
    return encontrados


def evaluar_venta_nueva(venta, score_previo=None, usuario=None):
    """Devuelve las señales de una venta recién creada, sin bloquearla."""
    cliente = venta.cliente
    señales = []

    activas_misma_ruta = list(
        Venta.objects.filter(
            tienda_id=venta.tienda_id,
            cliente_id=venta.cliente_id,
            estado_venta__in=ESTADOS_ACTIVOS,
        ).exclude(id=venta.id).order_by('-id')
    )
    if activas_misma_ruta:
        resumen = ', '.join(
            f'#{v.id} ({_dinero(v.saldo_actual)})' for v in activas_misma_ruta[:3]
        )
        señales.append(_senal(
            'ACTIVO_MISMA_RUTA', 'alta',
            f'Ya tenía {len(activas_misma_ruta)} crédito(s) activo(s) en esta ruta: {resumen}',
            cantidad=len(activas_misma_ruta),
            ventas=[v.id for v in activas_misma_ruta],
        ))

    rutas_admin = _rutas_activas_del_monitoreo()
    otras_rutas = rutas_admin.exclude(id=venta.tienda_id)
    posibles = _clientes_posibles_en_otras_rutas(cliente, otras_rutas)
    for otro_cliente, mismo_doc, mismo_telefono in posibles:
        ventas_otras = list(
            Venta.objects.filter(
                cliente_id=otro_cliente.id,
                estado_venta__in=ESTADOS_ACTIVOS,
            ).select_related('tienda').order_by('-id')
        )
        if not ventas_otras:
            continue
        motivo = 'misma identificación' if mismo_doc else 'mismo teléfono'
        rutas = ', '.join(sorted({v.tienda.nombre for v in ventas_otras}))
        resumen = ', '.join(
            f'#{v.id} en {v.tienda.nombre} ({_dinero(v.saldo_actual)})'
            for v in ventas_otras[:3]
        )
        señales.append(_senal(
            'ACTIVO_OTRA_RUTA', 'critica' if mismo_doc else 'alta',
            f'Posible doble financiación por {motivo}: {resumen}',
            cliente_id=otro_cliente.id,
            motivo=motivo,
            rutas=rutas,
            ventas=[v.id for v in ventas_otras],
        ))

    if cliente.estado_cliente == 'Bloqueado':
        señales.append(_senal(
            'CLIENTE_BLOQUEADO', 'critica',
            'El cliente está marcado como Bloqueado',
        ))

    if score_previo:
        cupo = score_previo.get('cupo_disponible')
        if cupo is not None and Decimal(str(venta.valor_venta)) > Decimal(str(cupo)):
            exceso = Decimal(str(venta.valor_venta)) - Decimal(str(cupo))
            señales.append(_senal(
                'EXCESO_CUPO', 'critica' if exceso > Decimal(str(cupo or 0)) * Decimal('0.2') else 'alta',
                f'Excede el cupo disponible en {_dinero(exceso)} '
                f'(cupo {_dinero(cupo)}; solicitado {_dinero(venta.valor_venta)})',
                cupo_disponible=int(round(float(cupo))),
                exceso=int(round(float(exceso))),
            ))
        senales_riesgo = score_previo.get('senales') or []
        if score_previo.get('score', 100) < 60 or senales_riesgo:
            señales.append(_senal(
                'RIESGO_ACTIVO', 'alta',
                f'Score {score_previo.get("score", "—")}/100 '
                f'({score_previo.get("nivel", "—")})'
                + (f' · {"; ".join(senales_riesgo)}' if senales_riesgo else ''),
                score=score_previo.get('score'),
                senales=senales_riesgo,
            ))

    return señales


def _detalle_venta(venta, señales, score_previo=None, usuario=None):
    max_severidad = max((RANGO_SEVERIDAD[s['severidad']] for s in señales), default=1)
    icono = '🔴' if max_severidad == 3 else '🟠' if max_severidad == 2 else '🟡'
    cliente = f'{venta.cliente.nombres} {venta.cliente.apellidos}'.strip()
    actor = getattr(venta, 'creado_por', None) or usuario
    lineas = [
        f'{icono} <b>Nuevo crédito para revisar</b>\n',
        f'🏪 Ruta: <b>{_escape(venta.tienda.nombre)}</b> (#{venta.tienda_id})',
        f'👤 Cliente: <b>{_escape(cliente)}</b> · ficha #{venta.cliente_id}',
        f'🧑‍💼 Trabajador: <b>{_escape(_nombre_usuario(actor))}</b>',
        f'💳 Venta: <b>#{venta.id}</b> · {_dinero(venta.valor_venta)}',
    ]
    if score_previo:
        lineas.append(
            f'📈 Score previo: <b>{score_previo.get("score", "—")}/100</b> · '
            f'Cupo disponible: <b>{_dinero(score_previo.get("cupo_disponible"))}</b>'
        )
    lineas.append('\n⚠️ <b>Señales detectadas</b>')
    lineas.extend(f'• {_escape(s["texto"])}' for s in señales)
    lineas.append('\n✅ Crédito permitido · requiere revisión administrativa')
    return '\n'.join(lineas)


def registrar_alerta_venta(venta, score_previo=None, usuario=None):
    """Persiste y notifica las señales de una venta, agrupadas en un mensaje."""
    if not venta_en_alcance_alertas(venta):
        return None
    señales = evaluar_venta_nueva(venta, score_previo=score_previo, usuario=usuario)
    if not señales:
        return None
    severidad = max(
        señales,
        key=lambda s: RANGO_SEVERIDAD[s['severidad']],
    )['severidad']
    detalle = _detalle_venta(venta, señales, score_previo, usuario)
    datos = {
        'senales': señales,
        'score_previo': score_previo or {},
    }
    alerta, _ = _guardar_alerta(
        tipo='VENTA_CON_ALERTAS',
        severidad=severidad,
        clave=f'venta:{venta.id}:operativa',
        titulo=f'Venta #{venta.id} con señales operativas',
        detalle=detalle,
        tienda_id=venta.tienda_id,
        cliente_id=venta.cliente_id,
        venta_id=venta.id,
        trabajador=getattr(venta, 'creado_por', None) or usuario,
        datos=datos,
    )
    return alerta


def _perfil_riesgo(venta):
    return calcular_riesgo_venta(
        plazo=venta.plazo,
        estado_venta=venta.estado_venta,
        dias_sin_abono=venta.dias_sin_abono(),
        dias_atrasados=venta.dias_atrasados(),
        total_abonado=venta.total_abonado(),
    )


def _nivel_riesgo(venta):
    """Nivel operativo: incluye créditos aún Vigentes sin primer abono."""
    return _perfil_riesgo(venta)['nivel_cobranza']


def _tiene_abono_real(venta):
    return Recaudo.objects.filter(
        venta_id=venta.id,
        valor_recaudo__gt=0,
        es_renovacion=False,
    ).exists()


def _tipo_alerta_cartera(venta, perfil, tiene_abono_real):
    es_preventivo = (
        tiene_abono_real
        and perfil['nivel_cobranza'] == 1
        and perfil['cuotas_atrasadas'] <= 0
        and venta.estado_venta == 'Vigente'
    )
    if not tiene_abono_real:
        return 'SIN_PRIMER_ABONO'
    return 'SEGUIMIENTO_PREVENTIVO' if es_preventivo else 'RIESGO_CARTERA'


def _datos_alerta_cartera(venta, perfil, tipo):
    nivel = perfil['nivel_cobranza']
    return {
        'nivel': nivel,
        'nivel_cobranza': nivel,
        'nivel_deterioro': perfil['nivel_deterioro'],
        'dias_sin_abono': perfil['dias_sin_abono'],
        'cuotas_atrasadas': perfil['cuotas_atrasadas'],
        'plazo': venta.plazo,
        'umbral_alerta': perfil['umbral_alerta'],
        'clasificacion': {
            'SEGUIMIENTO_PREVENTIVO': 'seguimiento_preventivo',
            'SIN_PRIMER_ABONO': 'sin_primer_abono',
        }.get(tipo, 'riesgo_cartera'),
        'estado_venta': venta.estado_venta,
        'actualizado_en': timezone.now().isoformat(),
    }


def _texto_alerta_cartera(venta, perfil, tipo):
    nivel = perfil['nivel_cobranza']
    dias = perfil['dias_sin_abono']
    umbral = perfil['umbral_alerta']
    cuotas_atrasadas = perfil['cuotas_atrasadas']
    cliente = f'{venta.cliente.nombres} {venta.cliente.apellidos}'.strip()
    severidad = {1: 'media', 2: 'alta', 3: 'critica'}[nivel]

    if tipo == 'SEGUIMIENTO_PREVENTIVO':
        unidad = 'día' if umbral == 1 else 'días'
        titulo = f'Venta #{venta.id} requiere seguimiento preventivo'
        detalle = (
            f'🟡 <b>Seguimiento preventivo: ciclo {venta.plazo.lower()} pendiente</b>\n\n'
            f'🏪 Ruta: <b>{_escape(venta.tienda.nombre)}</b>\n'
            f'👤 Cliente: <b>{_escape(cliente)}</b> · venta #{venta.id}\n'
            f'💰 Saldo: <b>{_dinero(venta.saldo_actual)}</b>\n'
            f'📅 <b>{dias} días desde el último abono real</b> · plazo {venta.plazo}\n'
            f'📉 Cuotas atrasadas: <b>{cuotas_atrasadas:.1f}</b>\n'
            f'📌 No registra cuota vencida; pasó el intervalo esperado de {umbral} {unidad}. '
            f'Verificar si corresponde cobrar hoy.'
        )
    elif tipo == 'SIN_PRIMER_ABONO':
        titulo = f'Venta #{venta.id} sin primer abono · gestión {perfil["clave_cobranza"]}'
        detalle = (
            f'🟠 <b>Crédito sin primer abono</b>\n\n'
            f'🏪 Ruta: <b>{_escape(venta.tienda.nombre)}</b>\n'
            f'👤 Cliente: <b>{_escape(cliente)}</b> · venta #{venta.id}\n'
            f'💰 Saldo: <b>{_dinero(venta.saldo_actual)}</b>\n'
            f'📅 <b>{dias} días desde la venta sin un pago real</b> · plazo {venta.plazo}\n'
            f'📌 Primer umbral de gestión: {umbral} días · no renovar ni aumentar cupo hasta recaudar'
        )
    else:
        titulo = f'Venta #{venta.id} requiere gestión {perfil["clave_cobranza"]}'
        detalle = (
            f'⚠️ <b>Gestión de cartera: {perfil["clave_cobranza"]}</b>\n\n'
            f'🏪 Ruta: <b>{_escape(venta.tienda.nombre)}</b>\n'
            f'👤 Cliente: <b>{_escape(cliente)}</b> · venta #{venta.id}\n'
            f'💰 Saldo: <b>{_dinero(venta.saldo_actual)}</b>\n'
            f'📅 <b>{dias} días sin abono real</b> · plazo {venta.plazo}\n'
            f'📉 Cuotas atrasadas: <b>{cuotas_atrasadas:.1f}</b>\n'
            f'📌 Umbral de gestión cruzado: {umbral} días · {perfil["motivo"]}'
        )
    return {
        'severidad': severidad,
        'titulo': titulo,
        'detalle': detalle,
    }


def _resolver_alerta_cartera(alerta, motivo, perfil=None, consolidada_en=None):
    datos = dict(alerta.datos or {})
    datos['resolucion'] = {
        'automatica': True,
        'motivo': motivo,
        'fecha': timezone.now().isoformat(),
    }
    if perfil:
        datos['resolucion'].update({
            'nivel_actual': perfil['nivel_cobranza'],
            'dias_sin_abono_actuales': perfil['dias_sin_abono'],
            'cuotas_atrasadas_actuales': perfil['cuotas_atrasadas'],
        })
    if consolidada_en:
        datos['resolucion']['consolidada_en'] = consolidada_en
    alerta.estado = 'resuelta'
    alerta.datos = datos
    alerta.save(update_fields=['estado', 'datos', 'actualizada'])


def revisar_riesgo_venta(venta, notificar=True):
    """Sincroniza una única alerta activa de cartera para una venta."""
    if not venta_en_alcance_alertas(venta):
        return {'creada': False, 'resueltas': 0, 'nivel': None, 'omitida': True}

    perfil = _perfil_riesgo(venta)
    abiertas = list(
        AlertaOperativa.objects.filter(
            venta_id_ref=venta.id,
            tipo__in=TIPOS_ALERTA_CARTERA,
            estado__in=ESTADOS_ALERTA_ABIERTA,
        ).order_by('-id')
    )

    if perfil['nivel_cobranza'] <= 0:
        for alerta in abiertas:
            _resolver_alerta_cartera(alerta, 'Venta al día', perfil=perfil)
        return {'creada': False, 'resueltas': len(abiertas), 'nivel': 0}

    tiene_abono_real = _tiene_abono_real(venta)
    tipo = _tipo_alerta_cartera(venta, perfil, tiene_abono_real)
    texto = _texto_alerta_cartera(venta, perfil, tipo)
    datos_nuevos = _datos_alerta_cartera(venta, perfil, tipo)
    actual = abiertas[0] if abiertas else AlertaOperativa.objects.filter(
        venta_id_ref=venta.id,
        tipo__in=TIPOS_ALERTA_CARTERA,
    ).order_by('-id').first()
    estaba_abierta = actual is not None and actual.estado in ESTADOS_ALERTA_ABIERTA
    datos_anteriores = dict(actual.datos or {}) if actual else {}
    nivel_anterior = int(datos_anteriores.get('nivel_cobranza', datos_anteriores.get('nivel', 0)) or 0)
    tipo_anterior = actual.tipo if actual else None

    for duplicada in abiertas[1:]:
        _resolver_alerta_cartera(
            duplicada,
            'Alerta consolidada en la alerta actual de la venta',
            consolidada_en=actual.id if actual else None,
        )

    historial = datos_anteriores.get('historial', [])
    if not isinstance(historial, list):
        historial = []
    if not actual or not estaba_abierta or nivel_anterior != perfil['nivel_cobranza'] or tipo_anterior != tipo:
        historial.append({
            'fecha': timezone.now().isoformat(),
            'nivel_anterior': nivel_anterior if actual else None,
            'nivel_nuevo': perfil['nivel_cobranza'],
            'tipo_anterior': tipo_anterior,
            'tipo_nuevo': tipo,
        })
    datos_nuevos['historial'] = historial[-20:]

    debe_notificar = bool(
        notificar
        and (
            not estaba_abierta
            or perfil['nivel_cobranza'] > nivel_anterior
            or (tipo == 'RIESGO_CARTERA' and tipo_anterior != tipo)
        )
    )
    if actual:
        actual.tipo = tipo
        actual.severidad = texto['severidad']
        actual.titulo = texto['titulo']
        actual.detalle = texto['detalle']
        actual.tienda_id_ref = venta.tienda_id
        actual.cliente_id_ref = venta.cliente_id
        actual.venta_id_ref = venta.id
        actual.datos = datos_nuevos
        if not estaba_abierta:
            actual.estado = 'nueva'
        elif perfil['nivel_cobranza'] > nivel_anterior:
            actual.estado = 'nueva'
        if nivel_anterior != perfil['nivel_cobranza'] or tipo_anterior != tipo:
            actual.ocurrencias = (actual.ocurrencias or 1) + 1
        actual.save(update_fields=[
            'tipo', 'severidad', 'titulo', 'detalle', 'tienda_id_ref',
            'cliente_id_ref', 'venta_id_ref', 'datos', 'estado',
            'ocurrencias', 'actualizada',
        ])
        creada = not estaba_abierta
    else:
        actual, creada = _guardar_alerta(
            tipo=tipo,
            severidad=texto['severidad'],
            clave=f'cartera:venta:{venta.id}:actual',
            titulo=texto['titulo'],
            detalle=texto['detalle'],
            tienda_id=venta.tienda_id,
            cliente_id=venta.cliente_id,
            venta_id=venta.id,
            datos=datos_nuevos,
            notificar=False,
        )

    if debe_notificar:
        _programar_telegram(actual)
    return {
        'creada': bool(creada or debe_notificar),
        'resueltas': 0,
        'nivel': perfil['nivel_cobranza'],
    }


def revisar_riesgo_cartera(rutas=None, notificar=True):
    """Reconciliación diaria: actualiza o resuelve alertas sin duplicarlas."""
    rutas = rutas if rutas is not None else _rutas_activas_del_monitoreo()
    ruta_ids = rutas.values_list('id', flat=True)
    ventas_con_alerta = AlertaOperativa.objects.filter(
        tienda_id_ref__in=ruta_ids,
        venta_id_ref__isnull=False,
        tipo__in=TIPOS_ALERTA_CARTERA,
        estado__in=ESTADOS_ALERTA_ABIERTA,
    ).values_list('venta_id_ref', flat=True)
    ventas = Venta.objects.filter(
        tienda_id__in=ruta_ids,
    ).filter(
        Q(estado_venta__in=ESTADOS_ACTIVOS) | Q(id__in=ventas_con_alerta),
    ).select_related('cliente', 'tienda')
    creadas = 0
    for venta in ventas:
        resultado = revisar_riesgo_venta(venta, notificar=notificar)
        creadas += int(resultado['creada'])
    return creadas


def revisar_cierres_ausentes(fecha, rutas=None):
    """Avisa si una ruta tuvo actividad en una fecha y no cerró caja."""
    creadas = 0
    alcance = rutas if rutas is not None else _rutas_activas_del_monitoreo()
    for tienda in alcance:
        tuvo_ventas = Venta.objects.filter(tienda=tienda, fecha_venta=fecha).exists()
        tuvo_recaudos = Recaudo.objects.filter(tienda=tienda, fecha_recaudo=fecha).exists()
        if not (tuvo_ventas or tuvo_recaudos):
            continue
        if Cierre_Caja.objects.filter(tienda=tienda, fecha_cierre=fecha).exists():
            continue
        detalle = (
            f'🟠 <b>Cierre de caja ausente</b>\n\n'
            f'🏪 Ruta: <b>{_escape(tienda.nombre)}</b> (#{tienda.id})\n'
            f'📅 Fecha operativa: <b>{fecha:%d/%m/%Y}</b>\n'
            f'📌 La ruta registró ventas o recaudos, pero no tiene cierre de caja.'
        )
        _, creada = _guardar_alerta(
            tipo='CIERRE_AUSENTE',
            severidad='alta',
            clave=f'cierre:tienda:{tienda.id}:fecha:{fecha.isoformat()}',
            titulo=f'Cierre ausente · {tienda.nombre} · {fecha:%d/%m/%Y}',
            detalle=detalle,
            tienda_id=tienda.id,
            datos={'fecha': fecha.isoformat()},
        )
        creadas += int(creada)
    return creadas


def resumen_operativo(fecha, rutas=None):
    """KPI operativo de la fecha para el resumen diario de Telegram."""
    resumen = []
    alcance = rutas if rutas is not None else _rutas_activas_del_monitoreo()
    for tienda in alcance.order_by('nombre'):
        activas = Venta.objects.filter(tienda=tienda, estado_venta__in=ESTADOS_ACTIVOS)
        atrasadas = activas.filter(estado_venta='Atrasado')
        vencidas = activas.filter(estado_venta='Vencido')
        abonos_reales = Recaudo.objects.filter(
            venta_id=OuterRef('pk'),
            valor_recaudo__gt=0,
            es_renovacion=False,
        )
        sin_primer_abono = activas.annotate(
            tiene_abono_real=Exists(abonos_reales),
        ).filter(tiene_abono_real=False)
        cartera = activas.aggregate(total=Sum('saldo_actual'))['total'] or Decimal('0')
        nuevas = Venta.objects.filter(tienda=tienda, fecha_venta=fecha).count()
        recaudo = Recaudo.objects.filter(
            tienda=tienda,
            fecha_recaudo=fecha,
            visita_blanco__isnull=True,
            es_renovacion=False,
        ).aggregate(total=Sum('valor_recaudo'))['total'] or Decimal('0')
        blancos = Recaudo.objects.filter(
            tienda=tienda, fecha_recaudo=fecha, visita_blanco__isnull=False,
        ).count()
        riesgo = 0
        saldo_riesgo = Decimal('0')
        for venta in activas.select_related('cliente'):
            if _nivel_riesgo(venta):
                riesgo += 1
                saldo_riesgo += venta.saldo_actual or Decimal('0')
        cierre = Cierre_Caja.objects.filter(tienda=tienda, fecha_cierre=fecha).exists()
        if activas.exists() or nuevas or recaudo or blancos:
            resumen.append({
                'nombre': tienda.nombre,
                'id': tienda.id,
                'caja': tienda.caja_inicial or Decimal('0'),
                'cartera': cartera,
                'activas': activas.count(),
                'atrasados': atrasadas.count(),
                'saldo_atrasado': atrasadas.aggregate(total=Sum('saldo_actual'))['total'] or Decimal('0'),
                'vencidos': vencidas.count(),
                'saldo_vencido': vencidas.aggregate(total=Sum('saldo_actual'))['total'] or Decimal('0'),
                'sin_primer_abono': sin_primer_abono.count(),
                'saldo_sin_primer_abono': sin_primer_abono.aggregate(total=Sum('saldo_actual'))['total'] or Decimal('0'),
                'nuevas': nuevas,
                'recaudo': recaudo,
                'blancos': blancos,
                'riesgo': riesgo,
                'saldo_riesgo': saldo_riesgo,
                'cierre': cierre,
            })
    return resumen
