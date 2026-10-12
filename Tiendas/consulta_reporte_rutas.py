"""Consulta paginada de la foto enviada; verifica propietario, chat y ruta."""
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from Tiendas.models import ReporteRutasTelegram
from Tiendas.reporte_rutas import dinero, escape, bloque_ruta

POR_PAGINA = 8
GRUPOS = {'p': 'Cuotas pendientes', 's': 'Sin registro', 'n': 'Registrados sin abono', 't': 'Todos los créditos con cuota'}


def autorizado(usuario, rutas, chat_id, reporte_id):
    if str(chat_id) != str(getattr(settings, 'TELEGRAM_ASSISTANT_CHAT_ID', '')):
        return None
    return ReporteRutasTelegram.objects.filter(pk=reporte_id, usuario=usuario, chat_ref=str(chat_id)).first()


def menu_reporte(usuario, rutas, chat_id, reporte_id=None, pagina=0):
    if str(chat_id) != str(getattr(settings, 'TELEGRAM_ASSISTANT_CHAT_ID', '')):
        return 'Reporte no autorizado.', []
    if reporte_id is None:
        reporte = ReporteRutasTelegram.objects.filter(usuario=usuario, chat_ref=str(chat_id)).order_by('-fecha').first()
    else:
        reporte = autorizado(usuario, rutas, chat_id, reporte_id)
    if not reporte:
        return 'Todavía no hay un reporte guardado para consultar.', []
    ids = set(rutas.values_list('id', flat=True))
    filas = [r for r in reporte.rutas if r['id'] in ids]
    paginas = max(1, (len(filas) + POR_PAGINA - 1) // POR_PAGINA)
    if pagina < 0 or pagina >= paginas:
        return 'Página no disponible.', []
    texto = (f'📊 <b>Detalle del reporte · {reporte.fecha:%d/%m/%Y}</b>\n'
        'Selecciona una ruta. Las cifras conservan el corte del reporte.\n'
        f'Rutas: {len(filas)} · página {pagina + 1}/{paginas}')
    teclado = [[{'text': str(r['nombre'])[:42], 'callback_data': f'asst:rep:{reporte.pk}:{r["id"]}:p:0'}]
        for r in filas[pagina * POR_PAGINA:(pagina + 1) * POR_PAGINA]]
    navegacion = []
    for destino, titulo in ((pagina - 1, 'Anterior'), (pagina + 1, 'Siguiente')):
        if 0 <= destino < paginas:
            navegacion.append({'text': titulo, 'callback_data': f'asst:reps:{reporte.pk}:{destino}'})
    if navegacion:
        teclado.append(navegacion)
    return texto, teclado


def detalle_reporte(usuario, rutas, chat_id, reporte_id, ruta_id, grupo='p', pagina=0):
    reporte = autorizado(usuario, rutas, chat_id, reporte_id)
    if not reporte or not rutas.filter(pk=ruta_id).exists():
        return 'Ruta o reporte no autorizado.', []
    ruta = next((r for r in reporte.rutas if r['id'] == ruta_id), None)
    if not ruta or grupo not in GRUPOS:
        return 'Detalle no disponible.', []
    filas = ruta.get('creditos_detalle', [])
    if grupo == 'p':
        filas = [d for d in filas if Decimal(d['pendiente']) > 0]
    elif grupo in ('s', 'n'):
        filas = [d for d in filas if d['estado'] == ('sin_registro' if grupo == 's' else 'sin_abono')]
    filas = sorted(filas, key=lambda d: (-Decimal(d['pendiente']), d['venta_id']))
    paginas = max(1, (len(filas) + POR_PAGINA - 1) // POR_PAGINA)
    if pagina < 0 or pagina >= paginas:
        return 'Página no disponible.', []
    corte = timezone.localtime(reporte.corte)
    lineas = [f'📅 <b>Jornada {reporte.fecha:%d/%m/%Y}</b> · foto del reporte',
        f'Alertas/corte: {corte:%d/%m/%Y %H:%M} · Santiago', bloque_ruta(ruta),
        f'<b>{GRUPOS[grupo]}</b>: {len(filas)} · página {pagina + 1}/{paginas}']
    etiquetas = {'sin_registro': 'sin registro', 'sin_abono': 'registrado sin abono', 'con_abono': 'con abono'}
    for detalle in filas[pagina * POR_PAGINA:(pagina + 1) * POR_PAGINA]:
        lineas.append(f'<b>{escape(detalle["cliente"], 80)}</b> · crédito #{detalle["venta_id"]}\n'
            f'Cuota {dinero(detalle["cuota"])} · abono {dinero(detalle["abonado"])}\n'
            f'Pendiente {dinero(detalle["pendiente"])} · {etiquetas[detalle["estado"]]}')
    if not filas:
        lineas.append('No hay créditos en este grupo.')
    lineas.append('<i>Foto histórica, no saldo actualizado. Abre la plataforma para registrar pagos o verificar cambios posteriores.</i>')
    callback = lambda g, p: f'asst:rep:{reporte.pk}:{ruta_id}:{g}:{p}'
    teclado = [[{'text': 'Pendientes', 'callback_data': callback('p', 0)},
        {'text': 'Sin registro', 'callback_data': callback('s', 0)}],
        [{'text': 'Sin abono', 'callback_data': callback('n', 0)},
        {'text': 'Todos', 'callback_data': callback('t', 0)}]]
    navegacion = []
    for destino, titulo in ((pagina - 1, 'Anterior'), (pagina + 1, 'Siguiente')):
        if 0 <= destino < paginas:
            navegacion.append({'text': titulo, 'callback_data': callback(grupo, destino)})
    if navegacion:
        teclado.append(navegacion)
    teclado.append([{'text': 'Otras rutas del reporte', 'callback_data': f'asst:reps:{reporte.pk}:0'}])
    return '\n\n'.join(lineas), teclado
