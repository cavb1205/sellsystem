"""Degrada a los usuarios que quedaron como superuser por el bug del registro.

Hasta el fix de C1, `register_user` creaba cada admin de ruta con
`is_superuser=True`, lo que les daba acceso a /admin/ de Django y a los
paneles root. El rol de admin de ruta se identifica solo por `is_staff`,
así que este comando quita `is_superuser` a todos menos a `root`.

Correr una vez tras desplegar el fix:
    python manage.py degradar_superusers

Es idempotente: en una segunda corrida reporta 0 degradados. No toca
`is_staff` (los admins lo conservan; los trabajadores ya lo tienen en False).
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Quita is_superuser a todos los usuarios excepto root (fix C1)'

    def handle(self, *args, **options):
        afectados = User.objects.exclude(username='root').filter(is_superuser=True)
        usernames = list(afectados.values_list('username', flat=True))
        count = afectados.update(is_superuser=False)

        if count:
            self.stdout.write(self.style.SUCCESS(
                f'Degradados {count} usuario(s) a is_superuser=False: {", ".join(usernames)}'
            ))
        else:
            self.stdout.write('No había usuarios para degradar (ya estaba todo correcto).')
