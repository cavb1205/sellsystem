# API de solo lectura para el asistente personal (Jarvis)

Código: `Tiendas/agente_api.py` · Pruebas: `Tiendas/test_agente_api.py`.

## Propósito

Permitir que el asistente personal del administrador (Jarvis, vía n8n) consulte
la operación de **sus** rutas para responder preguntas como "¿qué clientes no
pagaron ayer?" o "¿qué rutas quedaron con clientes sin registro?".

## Garantías

- **Solo lectura**: cada consulta corre en una transacción que siempre se revierte
  y, en PostgreSQL, se declara `READ ONLY`; cualquier intento de escritura falla.
  Solo se acepta `GET` (el resto responde 405).
- **Token propio**: `Authorization: Bearer <AGENTE_JARVIS_TOKEN>`. El valor vive en
  `sell_system/variables.py` del VPS (fuera de GitHub). Si no existe o tiene menos de
  32 caracteres, la API responde 401 a todo. No usa sesiones ni JWT de usuarios.
- **Alcance**: las rutas activas de `TELEGRAM_ALERTAS_USERNAME` (por defecto
  `cavb1205`) según `Tienda_Administrador`, el mismo alcance del reporte diario.
- **Datos mínimos**: nombre del cliente, montos y estados. Nunca teléfonos,
  documentos ni direcciones. Las alertas no incluyen su `detalle`.
- **Sin consolidar monedas**: los importes se informan por ruta, sin sumar rutas.
- **Límite**: 120 consultas por minuto, separado de la cuota de los usuarios.
- Reutiliza los cálculos del reporte diario (`cumplimiento_dia`, `resumen_operativo`);
  no usa funciones que crean alertas, recalculan estados persistidos u ordenan recorridos.

## Recursos (`/tiendas/agente/…`)

| Ruta | Parámetros | Devuelve |
|---|---|---|
| `rutas/` | — | Rutas con trabajadores, fecha operativa, créditos activos y cartera |
| `jornada/` | `fecha` = `hoy` (defecto), `ayer` o `YYYY-MM-DD`; `ruta` (id o parte del nombre); `detalle=0` para solo totales | Por ruta: cuotas del día, recaudado, pendientes, `no_pagaron` (visita sin abono), `sin_registro`, abonos parciales, ventas nuevas y cierre de caja (registrado/faltante) |
| `cartera/` | `ruta` | Cartera, atrasados, vencidos, sin primer abono, en riesgo y principales riesgos |
| `alertas/` | `ruta`, `severidad` | Alertas abiertas (tipo, severidad, título, cliente, saldo) |
| `cliente/` | `buscar` (≥ 3 letras), `ruta` | Créditos del cliente: estado, saldo, último abono, días sin abono |

`hoy`/`ayer` se resuelven con la fecha operativa y la zona horaria de cada ruta.
"Sin registro" significa ausencia de registro, no prueba de que no hubo visita.

## Configuración en el VPS

Agregar a `sell_system/variables.py` (no versionado):

```python
AGENTE_JARVIS_TOKEN = '<token aleatorio de 64 caracteres>'
```

y recargar gunicorn. Rotación: reemplazar el valor y recargar; el token anterior deja
de funcionar de inmediato.

## Pruebas

```sh
python ops/correr_pruebas_aisladas.py Tiendas.test_agente_api
```
