# Panel root — etapa 2: estados y configuración

Documento interno, 11-10-2026. Sobre etapa 1 `3c81a7b00132fd6780c70fe4f3c2d60d86d7c885`, rama `codex/root-etapa2-estados`. Sin merge ni despliegue.

## Cambios y contratos

- Una regla compartida normaliza vencimientos en conciliación, listado/resumen root, Telegram y aprobación interna. Conserva el plazo existente: tres días desde creación para pendiente de confirmación, o preactivación vencida; códigos pendientes conservan su plazo expira. No se inventa una nueva ventana desde recepción del comprobante.
- Expirar una solicitud no debe quitar la preactivación de un comprobante posterior. Cuando cambia la suscripción se registra versión y auditoría de expiración.
- Confirmación fuera de plazo solo con superusuario real, comprobante, `confirmar_expirada: true` y motivo de 5–500 caracteres. Se comprueba de nuevo el plazo bajo bloqueo. Se conserva precio congelado y ledger idempotente de etapa 1; Telegram no dispone de esta excepción.
- Revisión acepta `resultado: confirmar|rechazar|revertir`; rechazo/reversión y excepción requieren motivo. Se mantiene rechazo de confirmada como reversión por compatibilidad. Se responde con acción, mensaje y estado/vigencia reales; anular no es un reembolso.
- `GET /tiendas/solicitudes/revision/?dias=30|90|365` entrega pendientes, confirmadas, vencidas con comprobante y revisadas. El período restringe las tres últimas por procesada; las pendientes no se ocultan. Las vencidas históricas sin procesada quedan fuera, sin inventar fechas.
- Cuenta GET incluye versión, configurada e historial. PUT exige versión entera no negativa y banco/número/titular/tipo no vacíos de hasta 100 caracteres; una versión obsoleta devuelve 409. Cambio y bitácora son atómicos; una petición sin cambio no reescribe auditoría/versiones. La interfaz pide motivo; el servidor permite motivo predeterminado para compatibilidad.
- Precios PUT sigue aceptando la lista de etapa 1 y también `{planes: [...], motivo: ...}`. Validación y bloqueo de todo el conjunto; bitácora atómica solo si cambian valores. El payload antiguo conserva motivo predeterminado.
- `GET /tiendas/configuracion/historial/?tipo=cuenta|planes` es solo root y devuelve últimos 20 cambios con actor, fecha, motivo y snapshots antes/después.
- Nuevas solicitudes requieren cuenta completa y congelan los datos bancarios bajo bloqueo. Reutilizar una solicitud existente no cambia su cuenta/precio. Las anteriores sin snapshot devuelven cuenta null y marca explícita; no se sustituyen por la cuenta actual. Se mantiene soporte para revisar sus comprobantes.
- Listado compacto sin rutas devuelve `[]`; vista antigua conserva su mensaje vacío.

## Migración y pruebas

Migración aditiva `0028_root_estados_configuracion`, posterior a 0027: snapshot de cuenta, marca/motivo de revisión, versión de cuenta y tabla CambioConfiguracionRoot. No rellena retroactivamente cuentas, motivos ni auditorías, y no recalcula ingresos/caja/cartera.

Con dependencias Django 4.1 y settings de pruebas aislados:

```sh
DJANGO_SETTINGS_MODULE=sell_system.settings_pruebas_reportes python3 manage.py test Tiendas.test_root_estados Tiendas.test_root_integridad Tiendas.test_suscripciones Tiendas.test_orden_visita Ventas.test_total_liquidacion Ventas.test_calendario Publicidad.test_publicidad Ventas.test_reporte_historico Trabajadores.tests --noinput
python3 ops/verificar_root_etapa2_migracion.py
```

Resultado: 159 pruebas correctas, incluidas 15 nuevas; migración real 0027 → 0028 en SQLite en memoria conserva cuenta anterior, precio, ingreso y caja. Los campos nuevos y la nueva tabla coinciden con el estado de migraciones. No se reparó el drift histórico ajeno a esta migración.

`ops/prueba_root_local.py` solo SQLite en memoria, media desechable y red externa bloqueada. Sin variable ROOT_ETAPA2 sirve regresión original; con `ROOT_ETAPA2=1` agrega rutas ficticias para preactivación, paginación y pago vencido. Pruebas frontend: 12 errores/reintentos, cinco escrituras etapa 2, ocho de etapa 1, sin cambios en caja/saldo/clientes/recaudos y cero errores JS.

Límites: no se probaron bloqueos/concurrencia reales PostgreSQL, carga, extractos ni bot real. Los GET todavía pueden normalizar vencimientos y escribir esos estados, igual que la arquitectura existente. Optimización/separación de mantenimiento corresponde a etapa 3.

## Despliegue cuando se autorice

Verificar commits de ambos repositorios en GitHub; incluir etapa 1 y ejecutar migraciones 0027/0028 antes de publicar frontend. No desplegar checkouts sucios. Solicitudes anteriores mostrarán cuenta original desconocida y soporte; si la cuenta actual está incompleta no se generan solicitudes nuevas hasta completarla. No inventar datos históricos para evitar esa advertencia. Reload de sesiones antiguas root obligatorio por versión de cuenta y nuevos requisitos de motivo. Credenciales y datos reales quedan fuera de GitHub.
