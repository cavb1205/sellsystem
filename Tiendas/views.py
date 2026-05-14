from functools import wraps

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

import datetime
from itertools import chain

from django.conf import settings
from django.utils import timezone

from django.core.files.base import ContentFile

from Tiendas.models import Tienda, Cierre_Caja, Tienda_Membresia, Membresia, Tienda_Administrador, SolicitudPago, _generar_codigo_solicitud
from Tiendas.serializers import TiendaSerializer, CajaSerializer, TiendaMembresiaSerializer, TiendaCreateSerializer, TiendaAdminSerializer, SolicitudPagoSerializer
from Tiendas import telegram_bot


def require_n8n_token(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.headers.get('X-N8N-Token') != settings.N8N_SHARED_TOKEN:
            return Response({'error': 'forbidden'}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


def _actualizar_estados_membresias():
    """Recalcula en bulk los estados de todas las membresías según la fecha actual.
    Activa → Pendiente Pago cuando fecha_vencimiento ya pasó (1 día de gracia ya consumido).
    Pendiente Pago → Vencida cuando han pasado 3+ días desde el vencimiento.
    Pre-activada → Pendiente Pago cuando pre_activada_hasta ya pasó."""
    hoy = datetime.date.today()

    Tienda_Membresia.objects.filter(
        estado='Activa',
        fecha_vencimiento__lte=hoy - datetime.timedelta(days=1)
    ).update(estado='Pendiente Pago')

    Tienda_Membresia.objects.filter(
        estado='Pendiente Pago',
        fecha_vencimiento__lte=hoy - datetime.timedelta(days=3)
    ).update(estado='Vencida')

    Tienda_Membresia.objects.filter(
        estado='Pre-activada',
        pre_activada_hasta__lt=hoy
    ).update(estado='Pendiente Pago', pre_activada_hasta=None)


def extender_membresia(tienda_id, plan_nombre):
    """Extiende la membresía desde max(fecha_vencimiento, hoy). Retorna la Tienda_Membresia."""
    tm = Tienda_Membresia.objects.get(tienda_id=tienda_id)
    membresia = Membresia.objects.get(nombre=plan_nombre)
    dias = 30 if plan_nombre == 'Mensual' else 365
    base = max(tm.fecha_vencimiento, datetime.date.today())
    tm.membresia = membresia
    tm.fecha_activacion = datetime.date.today()
    tm.fecha_vencimiento = base + datetime.timedelta(days=dias)
    tm.estado = 'Activa'
    tm.pre_activada_hasta = None
    tm.save()
    return tm


def _confirmar_solicitud(solicitud, revisor=None):
    """Confirma una solicitud y extiende la membresía. Guarda el vencimiento previo
    para poder revertir con exactitud. revisor puede ser None (confirmado vía Telegram)."""
    tm = Tienda_Membresia.objects.filter(tienda=solicitud.tienda).first()
    solicitud.fecha_vencimiento_previa = tm.fecha_vencimiento if tm else None
    solicitud.estado = 'confirmada'
    solicitud.motivo_rechazo = ''
    solicitud.revisada_por = revisor
    solicitud.procesada = timezone.now()
    solicitud.save()
    return extender_membresia(solicitud.tienda_id, solicitud.membresia.nombre)


def _revertir_solicitud(solicitud, motivo, revisor=None):
    """Rechaza o revierte una solicitud. Si estaba confirmada, restaura el vencimiento previo."""
    tm = Tienda_Membresia.objects.filter(tienda=solicitud.tienda).first()
    era_confirmada = solicitud.estado == 'confirmada'
    solicitud.estado = 'rechazada'
    solicitud.motivo_rechazo = motivo
    solicitud.revisada_por = revisor
    solicitud.procesada = timezone.now()
    solicitud.save()
    if tm:
        if era_confirmada and solicitud.fecha_vencimiento_previa:
            tm.fecha_vencimiento = solicitud.fecha_vencimiento_previa
        if tm.estado in ('Pre-activada', 'Activa'):
            tm.estado = 'Pendiente Pago'
            tm.pre_activada_hasta = None
            tm.save()
        else:
            tm.save()
    return tm


### VIEWS FOR TIENDA  ####

@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def list_all_tiendas(request):
    '''obtenemos todas las tiendas'''

    user = request.user
    if user.username == 'root':
        # Recalcular estados antes de listar — rutas inactivas pasan a Pendiente/Vencida automáticamente
        _actualizar_estados_membresias()
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


@api_view(['PATCH'])
def patch_tienda_settings(request, pk):
    tienda = Tienda.objects.filter(id=pk).first()
    if not tienda:
        return Response({'message': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    allowed = ['prefijo_telefono', 'telefono', 'cupo_minimo_nuevo']
    for field in allowed:
        if field in request.data:
            setattr(tienda, field, request.data[field])
    tienda.save()
    return Response({'prefijo_telefono': tienda.prefijo_telefono}, status=status.HTTP_200_OK)


@api_view(['POST'])
def post_tienda(request):
    '''creamos una tienda'''
    if request.method == 'POST':
        # Verificar antes de guardar para que el flag sea correcto
        es_primera_tienda = not Tienda.objects.filter(administrador=request.user).exists()

        serialize = TiendaCreateSerializer(data=request.data)
        if serialize.is_valid():
            tienda = serialize.save()
            Cierre_Caja.objects.create(tienda=tienda, valor=tienda.caja_inicial, fecha_cierre=(
                datetime.date.today() - datetime.timedelta(days=1)))

            if es_primera_tienda:
                # Solo la primera ruta recibe trial de 7 días
                Tienda_Membresia.objects.create(
                    tienda=tienda,
                    membresia=Membresia.objects.get(nombre='Prueba'),
                    fecha_activacion=datetime.date.today(),
                    fecha_vencimiento=datetime.date.today() + datetime.timedelta(days=7),
                    estado='Activa'
                )
            else:
                # Rutas adicionales nacen en Pendiente Pago — sin trial
                Tienda_Membresia.objects.create(
                    tienda=tienda,
                    membresia=Membresia.objects.get(nombre='Prueba'),
                    fecha_activacion=datetime.date.today(),
                    fecha_vencimiento=datetime.date.today() - datetime.timedelta(days=1),
                    estado='Pendiente Pago'
                )

            Tienda_Administrador.objects.create(
                tienda=tienda, administrador=request.user)

            return Response(serialize.data, status=status.HTTP_200_OK)
        return Response(serialize.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
def delete_tienda(request, pk):
    tienda = Tienda.objects.filter(id=pk).first()
    if tienda:
        tienda.delete()
        return Response({'message': 'Tienda eliminada correctamente'}, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontró la tienda'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_tienda_root(request, pk):
    '''Elimina una tienda — solo root, solo si está vacía (0 clientes y 0 ventas).'''
    if request.user.username != 'root':
        return Response({'error': 'Solo el usuario root puede eliminar rutas'},
                        status=status.HTTP_403_FORBIDDEN)
    tienda = Tienda.objects.filter(id=pk).first()
    if not tienda:
        return Response({'error': 'No se encontró la ruta'}, status=status.HTTP_404_NOT_FOUND)
    clientes = tienda.cliente_set.count()
    ventas = tienda.venta_set.count()
    if clientes > 0 or ventas > 0:
        return Response({
            'error': f'La ruta tiene {clientes} clientes y {ventas} ventas. No se puede eliminar para proteger los datos.'
        }, status=status.HTTP_409_CONFLICT)
    nombre = tienda.nombre
    tienda.delete()
    return Response({'message': f'Ruta "{nombre}" eliminada'}, status=status.HTTP_200_OK)

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove_tienda_admin(request, pk):
    '''Remove the Tienda_Administrador link for the current user — does not delete the tienda itself'''
    rel = Tienda_Administrador.objects.filter(tienda_id=pk, administrador=request.user).first()
    if rel:
        rel.delete()
        return Response({'message': 'Tienda quitada de tu lista'}, status=status.HTTP_200_OK)
    return Response({'message': 'No se encontró la relación'}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
def get_tiendas_admin(request):
    '''return list stores for a admin user'''

    user = request.user
    print(user)
    if user.is_staff:
        tiendas = Tienda_Administrador.objects.filter(administrador=user)
        serializer = TiendaAdminSerializer(tiendas, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    return Response([], status=status.HTTP_200_OK)
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

####### SOLICITUDES DE PAGO (membresías automáticas vía WhatsApp) ##########

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def solicitar_pago(request):
    """Crea una SolicitudPago y devuelve el código + datos de cuenta para que el usuario pague."""
    membresia_id = request.data.get('membresia_id')
    plan_nombre = request.data.get('plan')

    if membresia_id:
        try:
            membresia = Membresia.objects.get(id=membresia_id)
        except Membresia.DoesNotExist:
            return Response({'error': 'Membresía no encontrada'}, status=status.HTTP_404_NOT_FOUND)
    elif plan_nombre in ('Mensual', 'Anual'):
        membresia = Membresia.objects.filter(nombre=plan_nombre).first()
        if not membresia:
            return Response({'error': f'Plan {plan_nombre} no configurado'}, status=status.HTTP_404_NOT_FOUND)
    else:
        return Response({'error': 'membresia_id o plan requerido'}, status=status.HTTP_400_BAD_REQUEST)

    tienda_id = request.data.get('tienda_id') or request.user.perfil.tienda.id
    tienda = Tienda.objects.filter(id=tienda_id).first()
    if not tienda:
        return Response({'error': 'Tienda no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    codigo = _generar_codigo_solicitud(membresia.nombre)
    expira = timezone.now() + datetime.timedelta(hours=24)

    solicitud = SolicitudPago.objects.create(
        tienda=tienda,
        membresia=membresia,
        codigo=codigo,
        expira=expira,
        solicitada_por=request.user,
    )

    numero_soporte = getattr(settings, 'WHATSAPP_SOPORTE_NUMERO', '')
    texto_wa = f"Hola, necesito ayuda con el pago de mi plan, tienda *{tienda.nombre}* (código {codigo})."
    wa_link = f"https://wa.me/{numero_soporte}?text={texto_wa.replace(' ', '%20')}" if numero_soporte else ''

    return Response({
        'codigo': codigo,
        'expira': expira,
        'monto': str(membresia.precio),
        'plan': membresia.nombre,
        'wa_link': wa_link,
        'cuenta': {
            'banco': getattr(settings, 'CUENTA_DESTINO_BANCO', ''),
            'numero': getattr(settings, 'CUENTA_DESTINO_NUMERO', ''),
            'titular': getattr(settings, 'CUENTA_DESTINO_TITULAR', ''),
            'tipo': getattr(settings, 'CUENTA_DESTINO_TIPO', ''),
        },
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def adjuntar_comprobante(request, codigo):
    """El usuario sube el comprobante → pre-activa la membresía al instante y notifica al admin por Telegram."""
    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        return Response({'error': 'Solicitud no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    if solicitud.estado in ('aprobada', 'confirmada', 'rechazada'):
        return Response({'error': 'Esta solicitud ya fue procesada'}, status=status.HTTP_409_CONFLICT)

    archivo = request.FILES.get('comprobante')
    comprobante_bytes = None
    if archivo:
        comprobante_bytes, nombre = telegram_bot.comprimir_imagen(archivo)
        if comprobante_bytes:
            solicitud.comprobante.save(nombre, ContentFile(comprobante_bytes), save=False)

    referencia = (request.data.get('referencia') or '').strip()
    if referencia:
        solicitud.referencia_bancaria = referencia

    # Pre-activación: acceso inmediato por 3 días mientras el admin confirma
    tm = Tienda_Membresia.objects.filter(tienda=solicitud.tienda).first()
    if tm:
        tm.estado = 'Pre-activada'
        tm.pre_activada_hasta = datetime.date.today() + datetime.timedelta(days=3)
        tm.save()

    solicitud.estado = 'pendiente_confirmacion'
    solicitud.save()

    # Notificar al admin por Telegram (no bloquea la respuesta si falla)
    message_id = telegram_bot.notificar_solicitud(solicitud, comprobante_bytes)
    if message_id:
        solicitud.telegram_message_id = message_id
        solicitud.save(update_fields=['telegram_message_id'])

    return Response({
        'estado': solicitud.estado,
        'mensaje': 'Tu acceso ya está activo. Confirmaremos tu pago en las próximas horas.',
        'pre_activada_hasta': str(tm.pre_activada_hasta) if tm else None,
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
def telegram_webhook(request):
    """Recibe los toques de botón del admin desde Telegram (confirmar / rechazar)."""
    secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if secret != settings.TELEGRAM_WEBHOOK_SECRET:
        return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

    callback = request.data.get('callback_query')
    if not callback:
        return Response({'ok': True})  # ignorar updates que no sean botones

    chat_id = str(callback.get('message', {}).get('chat', {}).get('id', ''))
    if chat_id != str(settings.TELEGRAM_ADMIN_CHAT_ID):
        return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

    callback_id = callback.get('id')
    data = callback.get('data', '')
    try:
        accion, codigo = data.split(':', 1)
    except ValueError:
        telegram_bot.responder_callback(callback_id, 'Acción inválida')
        return Response({'ok': True})

    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        telegram_bot.responder_callback(callback_id, 'Solicitud no encontrada')
        return Response({'ok': True})

    if solicitud.estado in ('aprobada', 'confirmada', 'rechazada'):
        telegram_bot.responder_callback(callback_id, 'Esta solicitud ya fue procesada')
        return Response({'ok': True})

    admin_tg = callback.get('from', {})
    admin_nombre = (
        f"{admin_tg.get('first_name', '')} {admin_tg.get('last_name', '')}".strip()
        or admin_tg.get('username', 'admin')
    )

    if accion == 'confirmar':
        _confirmar_solicitud(solicitud)
        solicitud.refresh_from_db()
        telegram_bot.marcar_procesada(solicitud, 'confirmar', admin_nombre)
        telegram_bot.responder_callback(callback_id, f'✅ {codigo} confirmado')
    elif accion == 'rechazar':
        _revertir_solicitud(solicitud, 'Rechazado en revisión')
        telegram_bot.marcar_procesada(solicitud, 'rechazar', admin_nombre)
        telegram_bot.responder_callback(callback_id, f'❌ {codigo} rechazado')
    else:
        telegram_bot.responder_callback(callback_id, 'Acción desconocida')

    return Response({'ok': True})


@api_view(['GET'])
def consultar_solicitud(request, codigo):
    """Devuelve el estado actual de una SolicitudPago. Accesible por JWT (app) o token n8n."""
    token_ok = request.headers.get('X-N8N-Token') == settings.N8N_SHARED_TOKEN
    user_ok = request.user and request.user.is_authenticated

    if not token_ok and not user_ok:
        return Response({'error': 'forbidden'}, status=403)

    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        return Response({'error': 'Solicitud no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    # Marcar como expirada si ya venció y sigue pendiente
    if solicitud.estado == 'pendiente' and timezone.now() >= solicitud.expira:
        solicitud.estado = 'expirada'
        solicitud.save(update_fields=['estado'])

    serializer = SolicitudPagoSerializer(solicitud)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['POST'])
@require_n8n_token
def activar_solicitud(request, codigo):
    """n8n llama este endpoint con el resultado del análisis del comprobante."""
    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        return Response({'error': 'Solicitud no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    if timezone.now() >= solicitud.expira and solicitud.estado == 'pendiente':
        solicitud.estado = 'expirada'
        solicitud.save(update_fields=['estado'])
        return Response({'error': 'Solicitud expirada'}, status=status.HTTP_410_GONE)

    if solicitud.estado in ('aprobada', 'rechazada'):
        return Response({'estado': solicitud.estado, 'message': 'Ya procesada'}, status=status.HTTP_409_CONFLICT)

    resultado = request.data.get('resultado')
    if resultado not in ('aprobada', 'pre_aprobada', 'rechazada'):
        return Response({'error': 'resultado inválido'}, status=status.HTTP_400_BAD_REQUEST)

    # Dedupe por referencia bancaria
    referencia = (request.data.get('extraccion') or {}).get('referencia', '') or ''
    if referencia and resultado == 'aprobada':
        duplicada = SolicitudPago.objects.filter(
            referencia_bancaria=referencia, estado='aprobada'
        ).exclude(id=solicitud.id).exists()
        if duplicada:
            resultado = 'rechazada'
            request.data._mutable = True if hasattr(request.data, '_mutable') else None
            motivo = 'Referencia bancaria ya utilizada'
        else:
            motivo = request.data.get('motivo', '')
    else:
        motivo = request.data.get('motivo', '')

    # Actualizar solicitud
    solicitud.estado = resultado
    solicitud.motivo_rechazo = motivo
    solicitud.extraccion_ia = request.data.get('extraccion') or {}
    solicitud.confianza_ia = request.data.get('confianza')
    solicitud.wa_from_number = request.data.get('wa_from_number', '')
    solicitud.wa_message_id = request.data.get('wa_message_id', '')
    if referencia:
        solicitud.referencia_bancaria = referencia
    solicitud.procesada = timezone.now()
    solicitud.save()

    # Actualizar membresía según resultado
    tm = Tienda_Membresia.objects.filter(tienda=solicitud.tienda).first()
    if resultado == 'aprobada':
        tm = extender_membresia(solicitud.tienda_id, solicitud.membresia.nombre)
    elif resultado == 'pre_aprobada':
        if tm:
            tm.estado = 'Pre-activada'
            tm.pre_activada_hasta = datetime.date.today() + datetime.timedelta(days=3)
            tm.save()

    response_data = {'estado': resultado, 'motivo': motivo}
    if tm:
        response_data['fecha_vencimiento'] = str(tm.fecha_vencimiento)

    return Response(response_data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def listar_solicitudes_revision(request):
    """Panel de conciliación (solo superusuarios): solicitudes esperando confirmación
    + confirmadas de los últimos 30 días para cruzar con el extracto bancario."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)

    corte = timezone.now() - datetime.timedelta(days=30)
    pendientes = SolicitudPago.objects.filter(
        estado='pendiente_confirmacion'
    ).select_related('tienda', 'membresia', 'solicitada_por').order_by('-creada')
    confirmadas = SolicitudPago.objects.filter(
        estado='confirmada', procesada__gte=corte
    ).select_related('tienda', 'membresia', 'solicitada_por', 'revisada_por').order_by('-procesada')

    return Response({
        'pendientes': SolicitudPagoSerializer(pendientes, many=True).data,
        'confirmadas': SolicitudPagoSerializer(confirmadas, many=True).data,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ver_comprobante(request, codigo):
    """Sirve la imagen del comprobante. Solo superusuarios (la carpeta media no es pública)."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)
    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud or not solicitud.comprobante:
        return Response({'error': 'Sin comprobante'}, status=status.HTTP_404_NOT_FOUND)
    from django.http import FileResponse
    return FileResponse(solicitud.comprobante.open('rb'), content_type='image/jpeg')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def revisar_solicitud_admin(request, codigo):
    """Confirmación / rechazo / reversión manual por el superusuario (web fallback de Telegram)."""
    if not request.user.is_superuser:
        return Response({'error': 'forbidden'}, status=403)

    solicitud = SolicitudPago.objects.filter(codigo=codigo).first()
    if not solicitud:
        return Response({'error': 'Solicitud no encontrada'}, status=status.HTTP_404_NOT_FOUND)

    if solicitud.estado in ('rechazada', 'expirada'):
        return Response({'estado': solicitud.estado, 'message': 'Ya procesada'}, status=status.HTTP_409_CONFLICT)

    resultado = request.data.get('resultado')
    if resultado not in ('confirmar', 'rechazar'):
        return Response({'error': 'resultado debe ser confirmar o rechazar'}, status=status.HTTP_400_BAD_REQUEST)

    admin_nombre = request.user.get_full_name() or request.user.username

    if resultado == 'confirmar':
        if solicitud.estado == 'confirmada':
            return Response({'estado': 'confirmada', 'message': 'Ya confirmada'}, status=status.HTTP_409_CONFLICT)
        tm = _confirmar_solicitud(solicitud, revisor=request.user)
        solicitud.refresh_from_db()
        telegram_bot.marcar_procesada(solicitud, 'confirmar', admin_nombre)
    else:  # rechazar — sirve también para revertir una confirmada en la conciliación
        motivo = request.data.get('motivo', 'Rechazado en revisión manual')
        tm = _revertir_solicitud(solicitud, motivo, revisor=request.user)
        telegram_bot.marcar_procesada(solicitud, 'rechazar', admin_nombre)

    response_data = {'estado': solicitud.estado}
    if tm:
        response_data['fecha_vencimiento'] = str(tm.fecha_vencimiento)
    return Response(response_data, status=status.HTTP_200_OK)


def comprobar_estado_membresia(tienda_id):
    '''verificamos el estado de la membresia'''
    suscripcion_tienda = Tienda_Membresia.objects.get(tienda=tienda_id)
    hoy = datetime.date.today()

    # Pre-activada: acceso temporal mientras se revisa el comprobante
    if suscripcion_tienda.estado == 'Pre-activada':
        if suscripcion_tienda.pre_activada_hasta and suscripcion_tienda.pre_activada_hasta >= hoy:
            return  # Período temporal vigente, no degradar
        # Período temporal expirado sin aprobación → degradar
        suscripcion_tienda.estado = 'Pendiente Pago'
        suscripcion_tienda.pre_activada_hasta = None
        suscripcion_tienda.save()
        return

    pendiente_pago = suscripcion_tienda.fecha_vencimiento + datetime.timedelta(days=1)
    vencida = pendiente_pago + datetime.timedelta(days=2)

    if suscripcion_tienda.estado == 'Activa' and hoy >= pendiente_pago:
        suscripcion_tienda.estado = 'Pendiente Pago'
        suscripcion_tienda.save()

    if suscripcion_tienda.estado == 'Pendiente Pago' and hoy >= vencida:
        suscripcion_tienda.estado = 'Vencida'
        suscripcion_tienda.save()

