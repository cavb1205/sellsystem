# Dependencias y sesiones

Esta entrega requiere Python 3.12.15 y un entorno virtual nuevo. No instalar
los requisitos sobre el entorno Python 3.10 del servicio activo. Django es
5.2.18 LTS; las versiones exactas de las bibliotecas y sus wheels admitidos
están fijadas en los archivos `.lock`.

## Entorno candidato y pruebas

En una copia aislada del commit:

```sh
python3.12 -m venv /ruta/privada/venv-candidato
/ruta/privada/venv-candidato/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements-tools.lock -r requirements.lock
/ruta/privada/venv-candidato/bin/python -m pip check
/ruta/privada/venv-candidato/bin/python ops/correr_pruebas_aisladas.py
/ruta/privada/venv-candidato/bin/python ops/prueba_seguridad_http_local.py
```

El primer programa fuerza SQLite en memoria y bloquea conexiones de red. El
segundo levanta Gunicorn en loopback, crea una base SQLite temporal, utiliza
configuración y cuentas ficticias, bloquea conexiones externas y elimina el
servidor y la base al terminar. Comprueba emisión y renovación de JWT, cambio
de contraseña, rechazo de tokens anteriores y dos abonos sin duplicación.
Ninguno necesita importar las credenciales reales de `variables.py`.

Para regenerar los locks después de cambiar una versión, ejecutar
`python ops/bloquear_dependencias.py` y revisar el resultado. Consulta metadatos
HTTPS de PyPI, incluye solo hashes de wheels no retirados y no ejecuta paquetes
descargados. Los hashes fijan los artefactos publicados; no sustituyen el análisis
de vulnerabilidades o la revisión del proveedor.

`ops/python-runtime-linux.json` fija una distribución CPython redistribuida por
Astral y su SHA-256 publicado en GitHub. Es una opción para preparar un runtime
paralelo sin modificar Python del sistema. Verificar el digest antes de extraer
y ejecutar el binario. En un VPS LTS nuevo también puede utilizarse un Python
3.12 mantenido por el proveedor, después de comprobar versiones y parches.

## Política de acceso

- Los accesos y refresh tokens incluyen la comprobación de revocación de
  SimpleJWT. Cambiar la contraseña invalida ambos. No se devuelve el hash Django
  de la contraseña en los campos públicos del usuario.
- La renovación comprueba cuenta existente, estado activo, contraseña vigente
  y al menos una ruta operativa para trabajadores. Un usuario eliminado, un ID
  inválido o una sesión sin comprobación de revocación devuelve un error de
  autenticación, sin emitir otro token.
- `/login/` y `/token/` comparten permisos de acceso a rutas y límite de intentos.
  `/token/refresh/` tiene un límite separado. Los límites siguen usando el cache
  configurado: aún se necesita un almacenamiento compartido para coordinarlos
  entre procesos; esta entrega no lo instala.
- Las consultas de ruta implícita validan la ruta principal del perfil. Tener
  acceso a otra ruta no rehabilita una asignación retirada de la principal.
- Se mantienen las duraciones de acceso y renovación de 60 minutos y la
  renovación sin rotación para conservar el comportamiento existente.
- Las cookies de sesión y CSRF requieren HTTPS. CORS admite los dominios y alias
  verificados del proyecto y sus previews del equipo; no todos los sitios Vercel.
- El registro, alta de trabajadores y cambios de contraseña aplican los
  validadores Django de longitud, claves comunes, solo números y similitud con
  datos del usuario. Los rechazos se explican en español sin guardar la clave
  propuesta. Las contraseñas existentes siguen verificándose normalmente.
  Las cuentas nuevas se crean con `create_user`, que calcula el hash antes
  del INSERT; no se guarda temporalmente una contraseña en texto claro.

## Claves privadas y publicación

La clave Django y la clave de firma JWT deben ser distintas. `settings.py` exige
`JWT_SIGNING_KEY` en `variables.py`, con al menos 50 caracteres y diferente de
`KEY`. No añadir valores reales al repositorio, logs, argumentos de comandos o
mensajes. El servicio falla al iniciar si falta esa configuración.

Antes de activar el código se puede preparar una copia privada separada:

```sh
python ops/preparar_claves_sesiones.py --source /ruta/privada/variables.py --destination /ruta/privada/candidata.py
```

El programa genera claves independientes con `secrets.token_urlsafe(64)`,
conserva el resto del archivo, mantiene propietario y permisos 600, rechaza
sobrescrituras y no cambia el origen. Solo muestra indicadores y la ruta de la
copia, nunca las claves. La activación de esa copia corresponde al procedimiento
de despliegue, con respaldo de la configuración anterior.

**Al publicar esta entrega, las sesiones anteriores tendrán que iniciar sesión
de nuevo.** La rotación de claves y el requisito de revocación se coordinan en
la misma intervención para evitar dos cierres de sesión separados. No se
eliminan usuarios, clientes, ventas, abonos ni movimientos de caja.

Antes de publicar: confirmar bases de GitHub y producción; respaldar código,
configuración privada y base; preparar y probar el entorno nuevo; inventariar
los procesos, cron y comandos que usan el entorno viejo. No cambiar solo los
workers con SIGHUP cuando se cambia el intérprete: arrancar los procesos con el
Python nuevo mediante una transición controlada. Considerar los servicios API,
webhook y trabajadores programados, y comprobar memoria disponible.

Revisar el plan de migraciones de las dependencias actualizadas antes de
aplicarlo. No generar automáticamente migraciones de diferencias históricas de
modelos ni alterar precisión monetaria en esta intervención. Esta entrega no
agrega migraciones de modelos financieros. Ejecutar comprobaciones de solo
lectura y comparar el código activo con el commit publicado al terminar.

Si se revierte, usar el commit anterior existente en GitHub y restaurar el
entorno y la configuración privada anteriores de manera coordinada. No restaurar
automáticamente la base: podrían perderse cobros legítimos registrados después
del cambio. La sesión volverá a requerir autenticación si cambia su firma.

La actualización de bibliotecas no renueva el sistema operativo, no restringe
el puerto PostgreSQL, no configura MFA ni acredita respaldos externos. Esos
controles siguen siendo necesarios. La CSP y las cabeceras de proxy también
requieren una entrega específica compatible con Nginx y la aplicación.
