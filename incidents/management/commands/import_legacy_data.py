import json
import os
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from incidents.models import Incident, Notification, User
from incidents.views import assign_community, expiry_for


class Command(BaseCommand):
    help = "Importa usuarios, incidencias y notificaciones desde los archivos JSON de Node."

    def add_arguments(self, parser):
        parser.add_argument(
            "--data-dir",
            default=os.environ.get("DATA_DIR", str(settings.BASE_DIR / "data")),
        )
        parser.add_argument(
            "--renew-expiry",
            action="store_true",
            help="Calcula la caducidad desde hoy (útil para datos de ejemplo antiguos, que si no caducarían al instante).",
        )

    def handle(self, *args, **options):
        data_dir = Path(options["data_dir"])
        self.renew_expiry = options["renew_expiry"]
        users = self._read_optional(data_dir / "users.json")
        incidents = self._read_required(data_dir / "incidents.json")
        notifications = self._read_optional(data_dir / "notifications.json")

        with transaction.atomic():
            user_count = self._import_users(users)
            incident_count = self._import_incidents(incidents)
            notification_count = self._import_notifications(notifications)
        self.stdout.write(
            self.style.SUCCESS(
                f"Importación completada: {user_count} usuarios, {incident_count} incidencias y "
                f"{notification_count} notificaciones nuevas."
            )
        )

    def _read_required(self, path):
        if not path.is_file():
            raise CommandError(f"No se encontró el archivo requerido: {path}")
        return self._read_json(path)

    def _read_optional(self, path):
        return self._read_json(path) if path.is_file() else []

    def _read_json(self, path):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CommandError(f"No se pudo leer {path}: {error}") from error
        if not isinstance(value, list):
            raise CommandError(f"{path.name} debe contener una lista JSON.")
        return value

    def _import_users(self, records):
        count = 0
        valid_roles = {role for role, _label in User.Role.choices}
        for record in records:
            if not isinstance(record, dict):
                raise CommandError("Cada registro de usuario debe ser un objeto JSON.")
            email = record.get("email", "")
            name = record.get("name", "")
            if not isinstance(email, str) or not isinstance(name, str) or not email.strip() or not name.strip():
                raise CommandError("Un usuario importado no contiene nombre y correo válidos.")
            email = email.strip().lower()
            try:
                validate_email(email)
            except ValidationError as error:
                raise CommandError(f"El usuario importado contiene un correo no válido: {email}") from error
            user_id = str(record.get("id") or uuid.uuid4())
            existing_id = User.objects.filter(pk=user_id).first()
            if existing_id:
                continue
            existing_email = User.objects.filter(email=email).first()
            if existing_email:
                raise CommandError(
                    f"El correo {email} ya pertenece a otro usuario; resuelve el conflicto antes de importar."
                )
            role = record.get("role")
            if role not in valid_roles:
                role = User.Role.CITIZEN
            user = User(
                id=user_id,
                email=email,
                name=name.strip()[:80],
                role=role,
            )
            legacy_hash = record.get("passwordHash", "")
            if isinstance(legacy_hash, str) and re.fullmatch(r"[a-fA-F0-9]+:[a-fA-F0-9]{128}", legacy_hash):
                salt, digest = legacy_hash.split(":", 1)
                user.password = f"legacy_scrypt${salt}${digest.lower()}"
            else:
                user.set_unusable_password()
            user.save()
            count += 1
        return count

    def _import_incidents(self, records):
        count = 0
        for record in records:
            if not isinstance(record, dict):
                raise CommandError("Cada incidencia importada debe ser un objeto JSON.")
            incident_id = str(record.get("id") or "")
            if not incident_id:
                raise CommandError("Una incidencia importada no contiene id.")
            if Incident.objects.filter(pk=incident_id).exists():
                continue
            incident_time = self._datetime(record.get("time"))
            lat = self._coordinate(record.get("lat"), incident_id, "lat")
            lng = self._coordinate(record.get("lng"), incident_id, "lng")
            reporter = User.objects.filter(pk=record.get("reporterId")).first()
            assigned = record.get("assignedTo")
            assigned_user = (
                User.objects.filter(pk=assigned.get("id")).first()
                if isinstance(assigned, dict) and assigned.get("id")
                else None
            )
            history = record.get("history")
            if not isinstance(history, list):
                history = [
                    {
                        "at": incident_time.isoformat(),
                        "action": "Incidencia registrada",
                        "actor": record.get("reporterName") or "Sistema",
                    }
                ]
            comments = record.get("comments")
            if not isinstance(comments, list):
                comments = []
            Incident.objects.create(
                id=incident_id,
                type=str(record.get("type") or "Otra")[:50],
                description=str(record.get("description") or "")[:2000],
                lat=lat,
                lng=lng,
                time=incident_time,
                status=record.get("status") if record.get("status") in dict(Incident.STATUSES) else "abierto",
                priority=record.get("priority") if record.get("priority") in dict(Incident.PRIORITIES) else "media",
                reporter=reporter,
                reporter_name=str(record.get("reporterName") or (reporter.name if reporter else ""))[:80],
                assigned_to=assigned_user,
                resolved_at=self._datetime(record.get("resolvedAt")) if record.get("resolvedAt") else None,
                comments=comments,
                history=history,
                community=assign_community(lat, lng),
                expires_at=expiry_for(
                    str(record.get("type") or "Otra"),
                    None if self.renew_expiry else incident_time,
                ),
            )
            count += 1
        return count

    def _coordinate(self, value, incident_id, field):
        try:
            coordinate = float(value)
        except (TypeError, ValueError) as error:
            raise CommandError(
                f"La incidencia {incident_id} tiene una coordenada {field} no válida."
            ) from error
        minimum, maximum = (-90, 90) if field == "lat" else (-180, 180)
        if not minimum <= coordinate <= maximum:
            raise CommandError(
                f"La incidencia {incident_id} tiene una coordenada {field} fuera de rango."
            )
        return coordinate

    def _import_notifications(self, records):
        count = 0
        for record in records:
            if not isinstance(record, dict):
                raise CommandError("Cada notificación importada debe ser un objeto JSON.")
            try:
                notification_id = uuid.UUID(str(record.get("id")))
            except (TypeError, ValueError, AttributeError) as error:
                raise CommandError("Una notificación importada tiene un id no válido.") from error
            if Notification.objects.filter(pk=notification_id).exists():
                continue
            user = User.objects.filter(pk=record.get("userId")).first()
            if not user:
                continue
            incident = Incident.objects.filter(pk=record.get("incidentId")).first()
            Notification.objects.create(
                id=notification_id,
                user=user,
                incident=incident,
                message=str(record.get("message") or "")[:300],
                at=self._datetime(record.get("at")),
                read=bool(record.get("read", False)),
            )
            count += 1
        return count

    def _datetime(self, value):
        if value is None:
            return timezone.now()
        parsed = parse_datetime(value) if isinstance(value, str) else None
        if not parsed:
            raise CommandError(f"La fecha heredada no es válida: {value!r}")
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed
