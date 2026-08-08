#!/bin/sh

# El servidor usa UTC y la regla CRON_TZ no se aplica de forma confiable en
# este cron. Se ejecuta cada hora y solo continúa cuando en Chile son las 08.
if [ "$(TZ=America/Santiago /bin/date '+%H')" != "08" ]; then
    exit 0
fi

set -eu
cd /home/cavb1205/sellsystem/sellsystem
exec /home/cavb1205/sellsystem/venv/bin/python manage.py mantenimiento_membresias
