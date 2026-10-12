# Centro de excepciones administrativas

El alcance continúa siendo el usuario configurado de alertas (`cavb1205` en producción) y sus rutas activas vinculadas en `Tienda_Administrador`. Los clientes de la plataforma, trabajadores y otros administradores no obtienen acceso a este centro.

## Cambios

- `Tiendas/alertas_operativas.py`: los créditos cerrados no reactivan riesgo por el paso del tiempo; el mantenimiento y el registro autorizado de cierres reconcilian omisiones ya corregidas. Las notas de revisión sobreviven a la actualización del perfil.
- `Tiendas/centro_alertas.py`: lectura de excepciones, cierres agrupados, perfiles actuales con consultas agregadas, control de continuidad de movimientos auditados de caja y registro de revisiones administrativas.
- `Tiendas/views.py`: endpoint paginado de pendientes, filtros sobre toda la colección, feed con búsqueda insensible a acentos y revisión con nota/bloqueo/verificación de actualización.
- `Tiendas/telegram_bot.py`: validación de alcance, trazabilidad de revisión y prevención de resolución de una condición de cobranza vigente.

Las consultas de pendientes no llaman sincronizadores, no envían notificaciones y no guardan cambios. La resolución durable de registros obsoletos corresponde al mantenimiento o una operación autorizada. No se borran alertas ni se hacen reparaciones de saldos.

## API

`GET /tiendas/alertas/pendientes/`: devuelve `version=1`, `casos`, `rutas`, `resumen`, `paginacion`, `caja_sin_historial` y fecha de cálculo. Filtros `tienda`, `severidad`, `tipo`, `estado`, `q`, `page`, `limit`. Las rutas ajenas no se convierten silenciosamente en una consulta global. `solo_resumen=1` omite las filas, no los controles que respaldan el total.

El feed histórico `GET /tiendas/alertas/` conserva su formato, añade `paginacion` y prioriza críticas, incluyendo las revisadas en los contadores de severidad.

`PATCH /tiendas/alertas/<id>/estado/`: permite una nota de hasta 1000 caracteres, obligatoria al resolver (mínimo cinco). `actualizada` proporciona control optimista para el navegador. Una alerta de cobranza vigente no se resuelve manualmente (409); puede marcarse revisada para seguimiento. El registro de notas y estados queda en `datos.revisiones`. Telegram guarda origen y actor del chat, sin inventar una identidad Django para esa persona.

## Control de caja

No se concilian cierres sin hora contra movimientos de fecha: esos cierres no identifican los límites exactos de un período y la fórmula anterior omitía desembolsos. Se compara la cadena de `MovimientoCaja` con la caja actual, incluyendo deltas efectivos, correcciones y reversas. La consulta captura ambos en un mismo snapshot SQL.

Una ruta sin historial se informa como no comprobable. La ausencia de discontinuidad no garantiza integridad de la historia previa ni efectivo físico. No reconstruir cierres, enviar mensajes o declarar fraude a partir de este control.

## Pruebas y operación

Pruebas: `DJANGO_SETTINGS_MODULE=sell_system.settings_pruebas_reportes python3 manage.py test Tiendas.test_alertas`. Requiere el runtime aislado Django 4.1 usado por las pruebas del proyecto. La base es SQLite desechable y las notificaciones están bloqueadas.

`ops/prueba_alertas_local.py` ofrece la fixture en `127.0.0.1:3042` para el navegador del frontend. Nunca importa variables privadas de producción; los accesos y notas se hacen sobre datos ficticios.

No requiere migraciones nuevas. No ejecutar estos scripts de prueba contra PostgreSQL. Esta implementación no ejecuta depuración en producción. Al autorizar publicar: verificar commits en GitHub, respaldar, backend primero, frontend después y comprobar el alcance del monitor.
