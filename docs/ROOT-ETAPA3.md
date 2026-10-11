# Panel root — métricas y consultas (etapa 3)

Documento interno, 11-10-2026. Rama `codex/root-etapa3-gestion`, sobre backend publicado `0535d81ca56544eeb497ea8f75e5f260fa2b80ba`. Frontend base `1f40d5439cebfa755b48905587fcebd114e49a75`. Sin migraciones ni despliegue; caja, cartera, cuotas diarias y guía pública fuera del alcance.

## Contratos

`Tiendas/root_metricas.py` concentra la comparación del libro vigente, cohortes y resumen. Se conservan las claves anteriores para compatibilidad, pero el frontend nuevo interpreta las nuevas definiciones; no usa `bloqueadas_mes` como historia ni `conversion_trial` como conversión de prueba.

- Resumen: añade `fecha_corte`, `comparacion_mes` con importes/rangos y variación nullable, y `cohortes_registro`. Mes actual hasta corte vs mismo prefijo del mes anterior, limitado al mes más corto. Los totales reales del mes/año conservan todo lo registrado hasta corte. Una sola agrupación de seis meses excluye futuras fechas; `parcial` identifica el mes actual.
- Anual: `comparacion_anual` usa fechas equivalentes hasta corte para el año actual y años completos para los anteriores. 29 de febrero se limita al 28 en ambos períodos. Año inválido, anterior a 1901 o futuro responde 400. Variación sin denominador positivo es null, no 0 ni infinito.
- Ingresos: `pagos` conserva solo vigentes; `pagos_anulados` es historial separado con estado/motivo/fecha de anulación. Añade administrador completo, usuario e ID. Todo excluye registros con fecha posterior al corte. Los anulados nunca se suman a `por_mes` ni `total_anual`. La clave antigua `total_anio_anterior` conserva el año completo; el porcentaje nuevo usa exclusivamente `comparacion_anual`.
- Proyección `mrr_estimado`: suma con Decimal de precios actuales de rutas activas no archivadas de pago, anual/12. No cambia montos históricos ni promete caja/utilidad/ingreso bancario.
- Cohortes: seis meses de registro de rutas que aún existen, incluidas archivadas. Evaluadas si registro+30 días <= corte; primer registro positivo vigente de plan pagado, no anterior al registro, y anterior a registro+30 días. Día 30 no está dentro; nuevas en observación quedan fuera de denominador. No presupone prueba gratuita. No reconstruye eliminadas. Una anulación puede corregir la conversión.
- Retención: `suspensiones_manuales_mes` cuenta CambioSuscripcion con acción vencimiento_manual y fecha real del mes; `rutas_suspendidas_mes` cuenta sus IDs distintos no nulos. `sin_primer_pago_vencidas` es estado actual no archivado sin registro positivo vigente hasta corte. No es baja definitiva. Pruebas activas no incluyen rutas creadas directamente como pendientes.
- Serializadores root compactos y revisión añaden nombre completo con fallback username, usuario e ID. Select-related en ingresos evita consultar el código por cada pago; revisión precarga administrador y membresía de ruta. Controles de autenticación/superusuario no cambian.

## Rendimiento y seguridad

Normalización conserva las reglas de calendario por ruta y vencimiento manual. Una lectura agrupada detecta candidatas; solo esas llaman al recálculo existente, que revalida bajo bloqueo. Solicitudes sanas no consultan la membresía una a una. No se reemplaza la autoridad de las transacciones de aprobación, archivado, precios o cuenta.

Consultas medidas con fixtures SQLite de rutas sanas; no son tiempos ni pruebas de carga:

| Endpoint | Antes: 2 rutas | Antes: 22 rutas | Ahora: 2 rutas | Ahora: 22 rutas |
|---|---:|---:|---:|---:|
| Resumen root | 34 | 74 | 11 | 11 |
| Listado `vista=lista` | 7 | 47 | 3 | 3 |
| Ingresos | 3 | 3 | 4 | 4 |
| Revisión | no medido | no medido | 5 | 5 |

Ingresos incorpora una consulta para comparación equivalente. También se prueba que agregar 20 solicitudes/pagos enlazados no añade consultas por fila. Las candidatas realmente vencidas todavía requieren transacciones de mantenimiento; el número constante no se afirma para ellas. El listado histórico sin vista=lista conserva su cálculo de reportes, no usa el serializador compacto.

Los GET todavía pueden escribir vencimientos/expiraciones. Separar ese mantenimiento exige otro cambio operativo. No se agregan eventos automáticos ficticios a CambioSuscripcion, ni backfill de bloqueos antiguos. Se conserva únicamente historia manual ya guardada. Concurrencia real PostgreSQL, carga, extractos/bot reales y modo consulta root quedan fuera.

## Pruebas reproducibles

Con dependencias Django 4.1:

```sh
DJANGO_SETTINGS_MODULE=sell_system.settings_pruebas_reportes python3 -m django test Tiendas.test_root_metricas Tiendas.test_root_estados Tiendas.test_root_integridad Tiendas.test_suscripciones Tiendas.test_orden_visita Ventas.test_total_liquidacion Ventas.test_calendario Publicidad.test_publicidad Ventas.test_reporte_historico Trabajadores.tests --noinput
```

177 pruebas correctas, incluidas 18 nuevas: fechas/bisiestos, períodos comparables, futuro/anulados, proyección sin modificar ledger, cohortes/denominador, eventos manuales, identidad de administrador, roles, consultas y conservación de caja/cartera. Frontend: 37 pruebas unitarias, compilación y lint correctos, integración Chrome escritorio/móvil con API→SQLite, cero escrituras en la nueva prueba. Regresiones de las dos etapas anteriores correctas con sus escrituras solo ficticias. Sin comprobación de datos reales.

Fixture `ROOT_ETAPA3=1 python3 ops/prueba_root_local.py`: loopback 3041, SQLite en memoria, comprobantes falsos y red externa prohibida. Solo pruebas; no iniciar en producción. Sin flag mantiene la fixture previa; ROOT_ETAPA2 conserva sus escenarios. Al autorizar despliegue, verificar commits pushed, publicar backend antes de frontend y no desplegar cambios sin commit. No se incluyen credenciales ni datos reales.
