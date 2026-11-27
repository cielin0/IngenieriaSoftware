from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from incidents.models import Incident


class Command(BaseCommand):
    help = (
        "Elimina definitivamente las incidencias caducadas hace más de N días "
        "(por defecto 30). Las caducadas dejan de mostrarse solas; esto solo limpia la base de datos."
    )

    def add_arguments(self, parser):
        parser.add_argument("--older-than-days", type=int, default=30)

    def handle(self, *args, **options):
        limit = timezone.now() - timedelta(days=options["older_than_days"])
        deleted, _ = Incident.objects.filter(expires_at__lt=limit).delete()
        self.stdout.write(self.style.SUCCESS(f"Incidencias eliminadas (con sus votos y denuncias): {deleted}"))
