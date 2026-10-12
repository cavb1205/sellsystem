# Reporte diario de rutas por Telegram

El envío continúa a las 08:00 de America/Santiago. Resume la jornada anterior
y únicamente las rutas activas administradas por el usuario configurado para
alertas. El resumen de membresías permanece separado.

## Interpretación

- Cuotas del día: la misma base original de Liquidar, no toda la mora ni el
  importe que queda después de cobrar. Incluye créditos pagados durante la jornada.
- Total recaudado: pagos reales de esa fecha; no cierres ficticios por renovación.
- Cuotas cubiertas: lo recibido de cada crédito hasta el importe de su cuota.
- Cuotas pendientes: lo que faltó de esas cuotas; cubiertas + pendientes = base.
- Cobros adicionales: lo recibido por encima o fuera de las cuotas de la fecha.
  Puede incluir anticipos, atrasos y pagos extraordinarios: no es todo anticipo.
- Los créditos se cuentan una vez, aunque tengan varios recibos o visita y pago.
- Sin registro significa ausencia de registro, no prueba falta de visita.
- Cierre registrado significa que existe un cierre; no certifica efectivo cuadrado.
- Alertas: estado abierto al corte indicado, distinto de la fecha de los cobros.
  No representan pérdidas confirmadas ni demuestran fraude.

Las rutas prioritarias aparecen primero. Se muestran todas las rutas del alcance,
incluidas las que no tienen actividad. No se consolidan importes entre rutas con
monedas/unidades no verificadas. Los montos no se convierten a miles ni se truncan
internamente; los centavos se muestran cuando existen.

## Consulta del detalle

El botón «Detalle» abre la foto de esa ruta y fecha. Se puede consultar pendientes,
sin registro, registrados sin abono y todos los créditos con cuota, con paginación
de ocho créditos. `/reporte` permite volver al último reporte guardado.

La fecha y el corte permanecen fijos: pagos posteriores no reescriben la foto.
Para saldos actuales y registros de pagos se utiliza la plataforma. Cada consulta
revalida el chat privado, el propietario del reporte y la ruta actualmente autorizada.
Los botones solo se incluyen si el usuario/chat del asistente coincide con el envío.

## Entrega

La nueva tabla `Tiendas_reporterutastelegram` guarda la foto y el estado de cada
parte. La migración 0029 solo crea esa tabla y su restricción única. No modifica
créditos, caja, recaudos, scores o fechas contractuales.

- Una foto por usuario y jornada; regenerar la tarea no reescribe la foto.
- Las partes respetan un máximo interno de 3.500 unidades UTF-16 visibles y no
  cortan bloques HTML ni rutas. El límite de Telegram es 4.096 caracteres.
- Antes de enviar se reclama la parte con bloqueo de fila, se guarda el intento
  y se revalidan rutas/chat. El bloqueo no se mantiene durante la llamada HTTP.
- Solo una respuesta válida con `ok=true` y `message_id` confirma la entrega.
- Un rechazo explícito y no ambiguo puede reintentarse, respetando `retry_after`
  y un máximo de tres intentos. El mantenimiento siguiente revisa los reportes
  de los últimos siete días; no reenvía sus partes ya confirmadas.
- Timeout, fallo de red, HTTP 5xx o respuesta ambigua se marcan `incierto`.
  No se reenvían automáticamente: Telegram no ofrece una clave de idempotencia
  para sendMessage. Un proceso interrumpido queda incierto al expirar su reserva.
- Un cambio de permisos bloquea la parte correspondiente; no se envía a otro chat.
- Los logs indican entrega confirmada/incompleta. No imprimen tokens, URLs del
  bot, IDs del chat ni contenido de clientes. Los estados inciertos requieren
  comprobación operacional antes de cualquier reenvío manual.

Las consultas no modifican ninguna cuenta. No se utiliza el mantenimiento real
para probar: mantiene sus otras operaciones de suscripciones y no es solo lectura.

## Pruebas y producción

Usar `sell_system.settings_pruebas_reportes`, SQLite desechable y Telegram simulado:

```sh
python manage.py test Tiendas.test_reporte_rutas Tiendas.test_entrega_reporte_rutas \
  Ventas.test_total_liquidacion Ventas.test_calendario \
  --settings=sell_system.settings_pruebas_reportes
```

Verificación real: PostgreSQL READ ONLY, red externa bloqueada y solo las rutas
del usuario autorizado. No crear fotos ni enviar mensajes de prueba en producción.
Publicar exclusivamente el commit probado y previamente publicado en GitHub.

Referencia: https://core.telegram.org/bots/api#sendmessage
