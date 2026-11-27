import os

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from incidents.models import User


class Command(BaseCommand):
    help = "Crea la cuenta administradora inicial usando ADMIN_EMAIL y ADMIN_PASSWORD."

    def handle(self, *args, **options):
        email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
        password = os.environ.get("ADMIN_PASSWORD", "")
        if not email and not password:
            self.stdout.write("No se configuró ADMIN_EMAIL ni ADMIN_PASSWORD; no se creó una cuenta.")
            return
        if not email or not password:
            raise CommandError(
                "Configura ADMIN_EMAIL y una ADMIN_PASSWORD no vacía para inicializar el administrador."
            )
        try:
            validate_email(email)
        except ValidationError as error:
            raise CommandError("ADMIN_EMAIL debe ser un correo válido.") from error
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            user = User(email=email, name="Administrador", role=User.Role.ADMINISTRATOR)
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS("Cuenta administradora inicializada."))
            return
        if user.role != User.Role.ADMINISTRATOR:
            self.stdout.write(
                self.style.WARNING("La cuenta ya existe y conserva su rol; no se modificó.")
            )
            return
        self.stdout.write("La cuenta administradora ya existe; no se modificó.")
