# Correcciones de suscripción por root

POST `/tiendas/<tienda_id>/admin/vencer/`: motivo obligatorio (5–500 caracteres),
versión entera del detalle, pago_id opcional y reconocimiento de solicitudes pendientes.
Solo superusuarios. El ID de la URL es de ruta; los endpoints de activación antiguos
siguen recibiendo ID de Tienda_Membresia.

- Vencimiento administrativo inmediato: estado Vencida, fecha operativa de la ruta,
  bandera manual sin gracia y preactivación eliminada. No se toca caja/cartera.
- Sin pago seleccionado se conservan todos los ingresos. Con pago seleccionado solo
  se permite uno vigente, manual, sin solicitud y de esa ruta. Se anula, no se borra
  ni se reembolsa; queda fuera de ingresos y conversión.
- Una corrección posterior de ingreso no reinicia la fecha de suspensión.
- Historial CambioSuscripcion con snapshots anteriores/nuevos, usuario, motivo,
  versión, pago y código de solicitud cuando corresponde.
- Bloqueo operativo del servidor por ruta u objeto, incluso con sesión abierta.
  Root está exento. Selección de otras rutas y trámites de renovación permanecen disponibles.
- Snapshot GET `detail/admin/<id>/?vista=membresia` es liviano, sin escritura ni
  consultas de cartera; detecta cambios en sesiones abiertas.
- Un comprobante recibido no elimina un vencimiento manual. Lo elimina la aprobación
  explícita de pago o la activación de root. Nueva vigencia desde hoy en ese caso.
- Activaciones de la nueva interfaz envían versión; reintentos/conflictos son 409.
  Clientes antiguos sin versión tienen deduplicación diaria del mismo plan.
- Reversión de pagos conserva registros anulados. Si hubo cambios posteriores, no
  sobrescribe la suscripción. Pagos históricos sin snapshot no se pueden revertir
  sobre cambios nuevos: requieren revisión de root.

## Verificación y publicación

Pruebas aisladas: `DJANGO_SETTINGS_MODULE=sell_system.settings_pruebas_reportes
python3 manage.py test Tiendas.test_suscripciones Tiendas.test_orden_visita
Publicidad.test_publicidad Ventas.test_calendario Ventas.test_reporte_historico
Trabajadores.tests --noinput`.

Integración: `ops/prueba_suscripciones_local.py`, solo loopback y SQLite en memoria;
frontend `tests/suscripciones-browser.cjs`, red externa bloqueada y personas ficticias.

La migración 0026 solo agrega campos de suscripción/anulación y la tabla de historial.
No ejecuta correcciones sobre rutas reales. Existen discrepancias previas entre
modelos y migraciones (Tienda_Administrador, índices e IDs); no se incluyen en 0026.

Despliegue `ops/desplegar_suscripciones.py`: manifiesto de commits GitHub,
backup verificado pg_dump, migración única con timeouts, instalación del archivo
del commit y recarga gradual Gunicorn. Verificación de hashes y logs posteriores.
Una reversión del código debe usar el commit anterior publicado, sin borrar datos
ni revertir automáticamente la migración aditiva.
