# Panel root — etapa 1 de seguridad e integridad

Implementación del 11-10-2026 sobre `a42911754ef5e5ed112b34763c7a1cafe450d5f5`.
Documento interno; no pertenece a la guía de usuarios.

## Contrato y comportamiento

- Ambos DELETE de ruta (`/<tienda_id>/delete/` y `/<tienda_id>/admin/delete/`)
  requieren autenticación y `is_superuser`. Usan una sola política, una transacción
  y bloqueo de la ruta. Caja distinta de cero, historial relacionado, alertas
  operativas o versión de suscripción distinta de cero impiden borrar (409).
  Solo se exceptúan las relaciones estructurales de suscripción/administrador.
  Las rutas con historial se archivan, no se eliminan.
- GET `/tiendas/list/?vista=lista` agrega `puede_eliminar`, calculado desde la misma
  política. La ausencia del dato nunca autoriza al frontend a ofrecer borrado.
- Cada nueva `SolicitudPago` guarda `monto_acordado`. El endpoint de creación bloquea
  el plan al cotizar. API pública, conciliación, Telegram y libro de ingresos usan
  ese importe, no el precio que tenga el catálogo al confirmar.
- PUT `/tiendas/planes/` recibe una lista de `{id, precio, version}` (o `{planes: [...]}`).
  IDs únicos positivos, versión entera no negativa e importes enteros 0..9999999999.
  Valida todo antes de escribir. Guarda el conjunto en una transacción, bloqueando
  los planes en orden. Una versión antigua responde 409 sin cambios parciales.
  Cambiar el precio incrementa la versión; guardar el mismo precio no lo hace.
- POST `/tiendas/<membresia_id>/archivar/` requiere `{archivar: boolean, version: integer}`.
  Bloquea la suscripción y conserva snapshots en `CambioSuscripcion`. Cambiar el estado
  incrementa la versión; repetir el mismo estado con versión actual es un no-op.
  Una reversión de pago no puede sobrescribir un archivado posterior.

## Solicitudes anteriores y migración 0027

Migración aditiva: versión en el catálogo y monto nullable en la solicitud.
Recupera el monto solo cuando hay un único importe distinto en sus pagos relacionados.
No modifica importes/estados del libro de ingresos ni caja/cartera/recaudos.

Si el monto anterior no está demostrado, queda NULL: no se reemplaza por la tarifa
actual. Root debe revisar comprobante y movimiento bancario y enviar `monto_confirmado`
al aprobar desde el panel. Telegram no puede inventar ese importe y remite a revisión.
Solicitudes con cotización guardada no admiten un importe distinto al confirmar.

## Verificación aislada

```sh
DJANGO_SETTINGS_MODULE=sell_system.settings_pruebas_reportes python3 manage.py test Tiendas.test_root_integridad Tiendas.test_suscripciones Tiendas.test_orden_visita Ventas.test_total_liquidacion Ventas.test_calendario Publicidad.test_publicidad Ventas.test_reporte_historico Trabajadores.tests --noinput
python3 ops/prueba_root_local.py
```

144 pruebas correctas, incluyendo 21 nuevas regresiones de esta etapa. Servidor de
integración solo en 127.0.0.1:3041, SQLite en memoria, comprobante/usuarios ficticios
y llamadas externas del backend prohibidas. Se verificó también la migración real
0026 -> 0027 con datos históricos ficticios: probado, desconocido y contradictorio.

No se probó estrés concurrente en PostgreSQL. Las pruebas reproducen conflictos
entre sesiones y fallos transaccionales con SQLite; los bloqueos de PostgreSQL
requieren verificación aislada adicional antes de afirmar cobertura de carga.
Se conservan las discrepancias previas modelos/migraciones documentadas en
`SUSCRIPCIONES-MANUALES.md`; no se genera una migración ajena para resolverlas aquí.

## Publicación pendiente

No desplegado en esta etapa. Publicar solo con autorización del usuario, desde los
commits verificados de GitHub. Preparar respaldo, migrar 0027 y coordinar backend y
frontend: precios/archivado exigen versión y clientes web antiguos deben recargarse.
No usar `makemigrations` indiscriminadamente en producción ni revertir automáticamente
la migración aditiva para volver al código anterior. Conservar ambientes/datos fuera
de GitHub. Validar hashes del backend y SHA del frontend tras cualquier despliegue.
