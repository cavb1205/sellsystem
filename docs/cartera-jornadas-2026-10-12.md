# Cartera diaria: corrección por jornadas

Un ciclo de gestión diario es una fecha distinta de recaudo no asociado a renovación. Dos abonos parciales, o visita seguida de pago en la misma fecha, no exigen dos cuotas. Todos los importes se suman íntegros; un abono múltiple puede cubrir varias jornadas. Se conserva la cantidad de recibos para el promedio histórico por registro.

`dias_atrasados` conserva su contrato de equivalente de cuotas redondeado a dos decimales, no días calendario. Lista, modelo, monitor y resumen de clientes usan jornadas distintas. Semanal/Mensual continúan usando su calendario contractual.

Se añade `importe_atrasado_estimado` en lista/detalle: importe directo en Decimal, con tope de saldo, sin multiplicar cuotas redondeadas. Devuelve null en no diarios y cero en créditos cerrados. El frontend lo prefiere cuando existe, con compatibilidad para respuestas anteriores. El calendario contractual mantiene prioridad.

No se impone un calendario diario desde la originación, ni se cambia qué fecha histórica cuenta como gestión. La inferencia sin primer abono y las jornadas sin abono siguen siendo señales históricas de seguimiento; un importe estimado cero no confirma por sí solo que el contrato esté al día. No se borra historial ni se recalculan saldos, vencimientos o scores persistidos. No hay migraciones. El total contractual de cuotas del día de Liquidar no se cambia.

Verificación: 243 pruebas backend, incluyendo 16 regresiones nuevas, calendarios, liquidación, membresías, root, publicidad y Telegram con red prohibida. Servidor de interfaz: `ops/prueba_cartera_local.py`, solo GET, SQLite en memoria; nunca usar con configuración productiva. `ops/verificar_cartera.py` comprueba contratos y permisos en PostgreSQL REPEATABLE READ READ ONLY, sobre las rutas administradas por cavb1205, sin imprimir datos de clientes ni enviar notificaciones.
