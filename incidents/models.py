import uuid

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import rules


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, name, password=None, **extra_fields):
        if not email:
            raise ValueError("El correo electrónico es obligatorio.")
        user = self.model(
            email=self.normalize_email(email),
            name=name,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("Un superusuario debe tener is_staff e is_superuser activados.")
        return self.create_user(email, name, password, **extra_fields)


class Community(models.Model):
    """Comunidad geográfica (por defecto, las 16 regiones de Chile). Editable desde el admin."""

    slug = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=100)
    lat = models.FloatField(help_text="Latitud del centro de la comunidad.")
    lng = models.FloatField(help_text="Longitud del centro de la comunidad.")
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "communities"

    def __str__(self):
        return self.name

    def to_json(self):
        return {"slug": self.slug, "name": self.name, "lat": self.lat, "lng": self.lng}


class User(AbstractUser):
    class Role(models.TextChoices):
        CITIZEN = "ciudadano", "Ciudadano"
        OPERATOR = "operador", "Operador"
        ADMINISTRATOR = "administrador", "Administrador"

    id = models.CharField(primary_key=True, max_length=64, default=uuid.uuid4, editable=False)
    password = models.CharField(_("password"), max_length=256)
    username = None
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=80)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CITIZEN)
    community = models.ForeignKey(
        Community, null=True, blank=True, on_delete=models.SET_NULL, related_name="members"
    )
    reputation = models.IntegerField(default=0)
    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return self.email

    @property
    def vote_weight(self):
        return rules.vote_weight(self.reputation)

    @property
    def reputation_level(self):
        return rules.reputation_level(self.reputation)


class ApiToken(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_tokens")
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(default=timezone.now)


class Incident(models.Model):
    STATUSES = (
        ("abierto", "Abierto"),
        ("en proceso", "En proceso"),
        ("resuelto", "Resuelto"),
    )
    PRIORITIES = (
        ("baja", "Baja"),
        ("media", "Media"),
        ("alta", "Alta"),
        ("crítica", "Crítica"),
    )

    id = models.CharField(primary_key=True, max_length=100)
    type = models.CharField(max_length=50)
    description = models.CharField(max_length=2000)
    lat = models.FloatField()
    lng = models.FloatField()
    time = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(max_length=20, choices=STATUSES, default="abierto")
    priority = models.CharField(max_length=20, choices=PRIORITIES, default="media")
    reporter = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="reported_incidents"
    )
    reporter_name = models.CharField(max_length=80, blank=True, default="")
    assigned_to = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_incidents"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    comments = models.JSONField(default=list)
    history = models.JSONField(default=list)
    community = models.ForeignKey(
        Community, null=True, blank=True, on_delete=models.SET_NULL, related_name="incidents"
    )
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    veracity = models.CharField(
        max_length=24, choices=rules.VERACITY_CHOICES, default=rules.UNVERIFIED, db_index=True
    )
    veracity_score = models.FloatField(default=0.0)
    confirm_count = models.PositiveIntegerField(default=0)
    deny_count = models.PositiveIntegerField(default=0)
    hidden = models.BooleanField(default=False, db_index=True)
    hidden_at = models.DateTimeField(null=True, blank=True)
    flag_count = models.PositiveIntegerField(default=0)
    flag_score = models.FloatField(default=0.0)

    class Meta:
        ordering = ["-time", "id"]

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    def to_json(self, my_vote=None, my_flag=False):
        reporter_reputation = self.reporter.reputation if self.reporter_id else None
        return {
            "id": self.id,
            "type": self.type,
            "description": self.description,
            "lat": self.lat,
            "lng": self.lng,
            "time": self.time.isoformat(),
            "status": self.status,
            "priority": self.priority,
            "assignedTo": (
                {"id": self.assigned_to_id, "name": self.assigned_to.name}
                if self.assigned_to_id
                else None
            ),
            "reporterId": self.reporter_id,
            "reporterName": self.reporter_name,
            "resolvedAt": self.resolved_at.isoformat() if self.resolved_at else None,
            "comments": self.comments,
            "history": self.history,
            "community": (
                {"slug": self.community.slug, "name": self.community.name} if self.community_id else None
            ),
            "expiresAt": self.expires_at.isoformat() if self.expires_at else None,
            "expired": self.is_expired,
            "veracity": self.veracity,
            "veracityLabel": rules.VERACITY_LABELS.get(self.veracity, self.veracity),
            "veracityScore": self.veracity_score,
            "confirmCount": self.confirm_count,
            "denyCount": self.deny_count,
            "myVote": my_vote,
            "myFlag": my_flag,
            "hidden": self.hidden,
            "flagCount": self.flag_count,
            "reporterLevel": rules.reputation_level(reporter_reputation) if reporter_reputation is not None else None,
        }


class Vote(models.Model):
    """Voto de un usuario sobre la veracidad de una incidencia (+1 confirma, -1 niega)."""

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="votes")
    value = models.SmallIntegerField()
    weight = models.FloatField(default=1.0)  # peso (según reputación) vigente al votar
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["incident", "user"], name="unique_vote_per_incident_user")
        ]


class Flag(models.Model):
    """Denuncia de abuso (broma, spam, información falsa...). Varias denuncias ocultan la incidencia."""

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="flags")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="flags")
    reason = models.CharField(max_length=20)
    weight = models.FloatField(default=1.0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["incident", "user"], name="unique_flag_per_incident_user")
        ]


class ReputationEvent(models.Model):
    """Bitácora de cambios de reputación (auditable)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reputation_events")
    incident = models.ForeignKey(
        Incident, null=True, blank=True, on_delete=models.SET_NULL, related_name="reputation_events"
    )
    delta = models.IntegerField()
    reason = models.CharField(max_length=120)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class PasswordResetToken(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reset_tokens")
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class Notification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    incident = models.ForeignKey(
        Incident, null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications"
    )
    message = models.CharField(max_length=300)
    at = models.DateTimeField(default=timezone.now, db_index=True)
    read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-at"]

    def to_json(self):
        return {
            "id": str(self.id),
            "userId": self.user_id,
            "incidentId": self.incident_id,
            "message": self.message,
            "at": self.at.isoformat(),
            "read": self.read,
        }
