# Permisos y conservación de datos

La autorización de operaciones se aplica en la API, independientemente de los
menús visibles en el frontend.

| Operación | Trabajador | Administrador de ruta | Superusuario |
| --- | --- | --- | --- |
| Cobros, clientes y gastos operativos | Según su asignación | Sus rutas | Todas |
| Aportes, utilidades y configuración de rutas | No | Sus rutas | Todas |
| Crear una ruta | No | Propietario de su ruta nueva | Sí |
| Gestionar trabajadores | No | Trabajadores de rutas administradas | Sí |
| Modificar otra cuenta administradora | No | No | Sí |
| Cambiar permisos administrativos | No | No | Sí |
| Cambiar contraseña propia | Sí, con acceso operativo | Sí | Sí |
| Cambiar categorías globales de gastos | No | No | Sí |
| Borrar una categoría utilizada | No | No | No |

Las ediciones ordinarias de clientes y utilidades no permiten cambiar su ruta,
ni siquiera entre dos rutas del mismo administrador. El envío de una ruta
distinta devuelve 400 sin modificar datos. La ruta actual puede enviarse por
compatibilidad con formularios existentes.

El autor de aportes y utilidades nuevos se obtiene del usuario autenticado. La
ruta se obtiene de la URL o del perfil autenticado. Las correcciones y reversas
mantienen el registro transaccional de caja.

Los serializadores públicos de usuarios utilizan una lista explícita de campos
y omiten hashes de contraseña, grupos y permisos internos. El frontend limpia
también las sesiones guardadas con el formato anterior al restaurarlas.

La migración `Gastos.0004_proteger_categoria_gastos` cambia la política de
eliminación a PROTECT; no elimina categorías ni gastos y no modifica importes.
Debe aplicarse antes de declarar terminado el despliegue del backend.

## Comprobaciones locales

Usar un entorno virtual compatible con los requisitos del repositorio y las
opciones de prueba aisladas, que utilizan SQLite desechable y no importan
configuración de producción:

```sh
python -m django test Tiendas.test_security_authorization Tiendas.test_suscripciones Tiendas.test_root_integridad Tiendas.test_orden_visita --settings=sell_system.settings_pruebas_reportes
```

La cobertura incluye rechazos con conservación de registros y caja, categorías
compartidas, clientes con créditos, jerarquía de cuentas, variantes de URL,
autor autenticado y flujos legítimos de trabajadores y administradores.

## Archivos privados en Nginx

`ops/nginx_private_files.conf` es un fragmento para incluir dentro de cada
servidor virtual y el servidor por defecto, antes de otras ubicaciones regex.
Bloquea segmentos que empiezan con punto y conserva la excepción de Certbot.
No modifica la configuración activa al añadirse al repositorio.

Al publicarlo: guardar copia privada de la configuración actual, revisar las
reglas `location ^~`, verificar `nginx -t`, recargar únicamente si pasa y
comprobar que las rutas Git devuelvan 404 tanto por dominio como por el servidor
por defecto. Verificar también la aplicación, la API y el desafío ACME. Si se
produce una falla, restaurar la configuración y verificarla antes de recargar.

No guardar repositorios, archivos de entorno ni respaldos en raíces públicas.
Este fragmento no demuestra que un historial previamente expuesto esté libre
de secretos: revisar ese historial y rotar las credenciales que correspondan.

## Límites de esta entrega

La actualización de dependencias, la revocación de JWT, el endurecimiento de
sesiones y límites de acceso, la migración del sistema operativo y el acceso
privado a PostgreSQL requieren entregas posteriores y sus propias pruebas.
No cerrar el acceso remoto de PostgreSQL sin comprobar las dependencias de
otros servicios del VPS.

Publicar únicamente commits comprobados y subidos a GitHub, y registrar los
commits efectivos de frontend y backend tras desplegar. Este documento es
operativo para desarrolladores; no forma parte de la guía pública de usuarios.
