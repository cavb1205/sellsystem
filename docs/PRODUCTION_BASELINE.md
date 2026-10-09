# Código de producción sincronizado

Referencia: los archivos del backend de `https://api.carterafinanciera.com`,
en `/home/cavb1205/sellsystem/sellsystem`, el 9 de octubre de 2026.

Se verificaron 180 archivos de implementación, migraciones, dependencias y
scripts operativos. Sus huellas están en `production-source-sha256.json`.
Esta sincronización no modifica los archivos del servidor, no ejecuta migraciones,
no reinicia servicios ni escribe en la base de datos de producción.

Incluye código ya publicado de calendario de créditos, reportes históricos,
permisos/asignación de trabajadores y autoría/mapa de Publicidad. No incorpora
funcionalidades pendientes que solamente existen en otros directorios locales.

## Configuración y archivos excluidos

- `sell_system/variables.py` contiene configuración privada y no se versiona.
- `sell_system/settings.py` importa esa configuración; su contenido público
  coincide con producción y no debe sustituirse por configuraciones de pruebas.
- No incluir `.env*`, respaldos, volcados SQL, datos de clientes, archivos de
  medios, logs, entornos virtuales ni archivos compilados `__pycache__`.
- Las plantillas `ops/*.conf` que ya existían en GitHub se conservan como ejemplos;
  no equivalen a las configuraciones privadas activas de Nginx/Supervisor.
- Las pruebas se ejecutan con SQLite en memoria y configuración aislada.

## Verificación reproducible

Desde la raíz del repositorio, con las dependencias de `requirements.txt`:

```bash
python -c 'import hashlib,json,pathlib; m=json.loads(pathlib.Path("docs/production-source-sha256.json").read_text()); bad=[p for p,h in m.items() if not pathlib.Path(p).is_file() or hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()!=h]; print("Archivos verificados:",len(m),"Diferencias:",bad); raise SystemExit(bool(bad))'
python manage.py test Publicidad.test_publicidad Ventas.test_calendario Ventas.test_reporte_historico --settings=sell_system.settings_pruebas_reportes
```

El test runner completa una tabla legacy exclusivamente en la base desechable.
Nunca usar esta configuración para procesos de producción ni aplicar ese ajuste
manualmente a la base real. Antes de futuros despliegues, comprobar diferencias
con el commit desplegado y conservar separadamente configuración y datos privados.
