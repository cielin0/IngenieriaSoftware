import hashlib
import json
import logging
import math
import re
import secrets
import uuid
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.cache import cache
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import geo, rules
from .models import (
    ApiToken,
    Community,
    Flag,
    Incident,
    Notification,
    PasswordResetToken,
    ReputationEvent,
    User,
    Vote,
)


logger = logging.getLogger(__name__)
STATUSES = ("abierto", "en proceso", "resuelto")
PRIORITIES = ("baja", "media", "alta", "crítica")
TYPES = rules.TYPE_NAMES
VALID_ROLES = tuple(role for role, _label in User.Role.choices)
SESSION_DURATION = timedelta(hours=12)
OPERATOR_ROLES = (User.Role.OPERATOR, User.Role.ADMINISTRATOR)
GENERIC_RESET_MESSAGE = (
    "Si el correo está registrado, te enviamos instrucciones para restablecer la contraseña. "
    "Revisa también la carpeta de spam."
)


class APIError(Exception):
    def __init__(self, status, message, extra=None):
        self.status = status
        self.message = message
        self.extra = extra or {}
        super().__init__(message)


def api_endpoint(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            response = view(request, *args, **kwargs)
        except APIError as error:
            response = JsonResponse({"message": error.message, **error.extra}, status=error.status)
        except Exception:
            logger.exception("Error interno procesando %s %s", request.method, request.path)
            response = JsonResponse({"message": "Error interno del servidor."}, status=500)
        response["Cache-Control"] = "no-store"
        return response

    return wrapped


def require_method(request, method):
    if request.method != method:
        return JsonResponse(
            {"message": "Método no permitido."},
            status=405,
            headers={"Allow": method},
        )
    return None


def parse_body(request):
    try:
        raw = request.body
    except RequestDataTooBig as error:
        raise APIError(413, "El cuerpo de la solicitud supera el límite permitido.") from error
    if len(raw) > 1_000_000:
        raise APIError(413, "El cuerpo de la solicitud supera el límite permitido.")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise APIError(400, "JSON inválido.") from error
    if not isinstance(data, dict):
        raise APIError(400, "El cuerpo JSON debe ser un objeto.")
    return data


def public_user(user):
    return {
        "id": user.pk,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "community": user.community.to_json() if user.community_id else None,
        "reputation": user.reputation,
        "reputationLevel": user.reputation_level,
        "voteWeight": user.vote_weight,
    }


def bearer_token(request):
    authorization = request.headers.get("Authorization", "")
    match = re.fullmatch(r"Bearer\s+([A-Za-z0-9_-]+)", authorization)
    return match.group(1) if match else None


def get_user(request, required=True):
    token = bearer_token(request)
    if not token:
        if required:
            raise APIError(401, "Inicia sesión para continuar.")
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    session = ApiToken.objects.select_related("user", "user__community").filter(pk=digest).first()
    if not session or session.expires_at <= timezone.now():
        if session:
            session.delete()
        if required:
            raise APIError(401, "Inicia sesión para continuar.")
        return None
    if not session.user.is_active:
        session.delete()
        if required:
            raise APIError(401, "La sesión ya no es válida.")
        return None
    return session.user


def require_role(user, roles):
    if user.role not in roles:
        raise APIError(403, "No tienes permisos para realizar esta acción.")


def is_operator(user):
    return bool(user) and user.role in OPERATOR_ROLES


def client_ip(request):
    return request.META.get("REMOTE_ADDR", "desconocida")


def throttle(request, scope, limit, window_seconds, identity=""):
    """Limita intentos por IP (y opcionalmente por correo) usando la caché de Django."""
    if not getattr(settings, "API_THROTTLE_ENABLED", True):
        return
    raw = f"{scope}|{client_ip(request)}|{identity}"
    key = "throttle:" + hashlib.sha256(raw.encode()).hexdigest()
    cache.add(key, 0, timeout=window_seconds)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window_seconds)
        count = 1
    if count > limit:
        raise APIError(429, "Demasiados intentos. Espera unos minutos e inténtalo de nuevo.")


def create_notification(incident, message):
    if incident.reporter_id:
        Notification.objects.create(user=incident.reporter, incident=incident, message=message)


def add_history(incident, action, actor):
    incident.history.append(
        {"at": timezone.now().isoformat(), "action": action, "actor": actor}
    )


def valid_coordinate(value, minimum, maximum):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and minimum <= number <= maximum else None


def require_chile(lat, lng):
    if not geo.in_chile(lat, lng):
        raise APIError(400, "Solo se pueden reportar incidencias dentro de Chile.")


# --------------------------------------------------------------------------- #
# Comunidades, caducidad, visibilidad
# --------------------------------------------------------------------------- #
def assign_community(lat, lng):
    """Comunidad activa cuyo centro está más cerca del punto (o None si no hay comunidades)."""
    communities = list(Community.objects.filter(is_active=True))
    if not communities:
        return None
    return min(communities, key=lambda c: geo.haversine_m(lat, lng, c.lat, c.lng))


def expiry_for(incident_type, start=None):
    return (start or timezone.now()) + timedelta(days=rules.ttl_days(incident_type))


def extend_expiry(incident):
    """Una confirmación renueva la vigencia (hasta un máximo de 2 veces el plazo original)."""
    ttl = timedelta(days=rules.ttl_days(incident.type))
    candidate = min(timezone.now() + ttl / 2, incident.time + ttl * 2)
    if incident.expires_at is None or candidate > incident.expires_at:
        incident.expires_at = candidate


def active_filter():
    return Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())


def incident_queryset(user, include_inactive=False):
    """Incidencias que `user` puede ver: sin ocultas, caducadas ni descartadas (salvo operadores/autor)."""
    queryset = Incident.objects.select_related("assigned_to", "community", "reporter")
    if is_operator(user):
        return queryset if include_inactive else queryset.filter(hidden=False).filter(active_filter())
    visible = ~Q(veracity=rules.FALSE)
    if user:
        visible |= Q(reporter=user)
    return queryset.filter(hidden=False).filter(active_filter()).filter(visible)


def serialize_incidents(incidents, viewer):
    my_votes, my_flags = {}, set()
    if viewer:
        my_votes = dict(Vote.objects.filter(user=viewer).values_list("incident_id", "value"))
        my_flags = set(Flag.objects.filter(user=viewer).values_list("incident_id", flat=True))
    return [
        incident.to_json(my_vote=my_votes.get(incident.pk), my_flag=incident.pk in my_flags)
        for incident in incidents
    ]


def find_duplicates(viewer, incident_type, lat, lng):
    """Incidencias vigentes del mismo tipo dentro del radio de duplicados, de la más cercana a la más lejana."""
    radius = rules.dup_radius_m(incident_type)
    dlat = radius / 111_000
    dlng = radius / (111_000 * max(math.cos(math.radians(lat)), 0.01))
    candidates = (
        incident_queryset(viewer)
        .filter(type=incident_type, lat__range=(lat - dlat, lat + dlat), lng__range=(lng - dlng, lng + dlng))
        .exclude(status="resuelto")
    )
    found = []
    for candidate in candidates:
        distance = geo.haversine_m(lat, lng, candidate.lat, candidate.lng)
        if distance <= radius:
            found.append((distance, candidate))
    found.sort(key=lambda item: item[0])
    return found[:3]


# --------------------------------------------------------------------------- #
# Veracidad, votos, reputación
# --------------------------------------------------------------------------- #
def adjust_reputation(user, delta, reason, incident=None):
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        new_value = max(rules.REPUTATION_MIN, min(rules.REPUTATION_MAX, locked.reputation + delta))
        applied = new_value - locked.reputation
        locked.reputation = new_value
        locked.save(update_fields=["reputation"])
        if applied:
            ReputationEvent.objects.create(user=locked, incident=incident, delta=applied, reason=reason[:120])
    user.reputation = new_value


def change_veracity(incident, new_state, actor_name):
    """Cambia la veracidad, ajusta la reputación de quien reportó y le envía una notificación."""
    old_state = incident.veracity
    if old_state == new_state:
        return
    incident.veracity = new_state
    label = rules.VERACITY_LABELS[new_state]
    add_history(incident, f"Veracidad cambiada a «{label}»", actor_name)
    if incident.reporter_id:
        delta = rules.REPUTATION_BY_VERACITY[new_state] - rules.REPUTATION_BY_VERACITY[old_state]
        if delta:
            adjust_reputation(incident.reporter, delta, f"Veracidad: {label}", incident)
        create_notification(incident, f'Tu incidencia "{incident.type}" ahora figura como: {label}.')


def refresh_veracity(incident):
    """Recalcula puntajes con los votos actuales y aplica el cambio automático de estado."""
    rows = list(Vote.objects.filter(incident=incident).values_list("value", "weight"))
    state, score, confirms, denies = rules.evaluate_veracity(rows)
    incident.veracity_score = score
    incident.confirm_count = confirms
    incident.deny_count = denies
    if incident.veracity not in rules.LOCKED_VERACITY:
        change_veracity(incident, state, "Sistema (votos de la comunidad)")


def hide_incident(incident, actor_name):
    if incident.hidden:
        return
    incident.hidden = True
    incident.hidden_at = timezone.now()
    add_history(incident, "Incidencia ocultada por denuncias", actor_name)
    if incident.reporter_id:
        adjust_reputation(incident.reporter, -rules.HIDE_PENALTY, "Incidencia ocultada por denuncias", incident)
        create_notification(
            incident,
            f'Tu incidencia "{incident.type}" fue ocultada tras varias denuncias de la comunidad.',
        )


def restore_incident(incident, actor_name):
    if not incident.hidden:
        return
    incident.hidden = False
    incident.hidden_at = None
    incident.flag_count = 0
    incident.flag_score = 0.0
    Flag.objects.filter(incident=incident).delete()
    add_history(incident, "Incidencia restaurada tras revisión", actor_name)
    if incident.reporter_id:
        adjust_reputation(incident.reporter, rules.HIDE_PENALTY, "Incidencia restaurada tras revisión", incident)
        create_notification(incident, f'Tu incidencia "{incident.type}" fue restaurada tras una revisión.')


def home(request):
    return render(request, "index.html")


def admin_panel(request):
    return render(request, "admin_panel.html")


@csrf_exempt
@api_endpoint
def api_docs(request):
    if response := require_method(request, "GET"):
        return response
    return JsonResponse(
        {
            "title": "API de Incidencias de Chile",
            "authentication": "Token Bearer; el registro público crea usuarios ciudadanos e inicia su sesión.",
            "endpoints": [
                "GET /api/meta",
                "POST /api/auth/register",
                "POST /api/auth/login",
                "GET /api/auth/me",
                "PUT /api/auth/me",
                "POST /api/auth/logout",
                "POST /api/auth/password-reset/request",
                "POST /api/auth/password-reset/confirm",
                "GET /api/incidents?community=<slug>&include=all",
                "POST /api/incidents",
                "PUT /api/incidents/:id",
                "DELETE /api/incidents/:id",
                "POST /api/incidents/:id/vote",
                "DELETE /api/incidents/:id/vote",
                "POST /api/incidents/:id/flag",
                "DELETE /api/incidents/:id/flag",
                "GET /api/admin/stats",
                "GET /api/users",
                "PUT /api/users/:id/role",
                "GET /api/notifications",
                "PUT /api/notifications/read",
            ],
            "incidentStates": list(STATUSES),
            "veracityStates": [value for value, _label in rules.VERACITY_CHOICES],
            "priorities": list(PRIORITIES),
            "rolePermissions": {
                "ciudadano": "Crear incidencias, votar, denunciar abusos y editar o eliminar solo las propias.",
                "operador": "Actualizar estado, prioridad, veracidad, asignación, moderación y comentarios.",
                "administrador": "Permisos de operador, eliminación de cualquier reporte y administración de roles.",
            },
        }
    )


@csrf_exempt
@api_endpoint
def meta(request):
    if response := require_method(request, "GET"):
        return response
    return JsonResponse(
        {
            "types": [
                {
                    "name": item["name"],
                    "color": item["color"],
                    "short": item["short"],
                    "ttlDays": item["ttl_days"],
                    "dupRadiusM": item["dup_radius_m"],
                }
                for item in rules.TYPE_CONFIG
            ],
            "veracity": [{"value": value, "label": label} for value, label in rules.VERACITY_CHOICES],
            "flagReasons": [{"value": value, "label": label} for value, label in rules.FLAG_REASONS.items()],
            "communities": [c.to_json() for c in Community.objects.filter(is_active=True)],
            "chile": {
                "polygon": [list(point) for point in geo.CHILE_POLYGON],
                "islands": [list(box) for box in geo.CHILE_ISLAND_BOXES],
            },
            "rules": {
                "confirmScore": rules.CONFIRM_SCORE,
                "confirmMinVoters": rules.CONFIRM_MIN_VOTERS,
                "falseScore": rules.FALSE_SCORE,
                "falseMinVoters": rules.FALSE_MIN_VOTERS,
                "flagMinUsers": rules.FLAG_MIN_USERS,
            },
        }
    )


# --------------------------------------------------------------------------- #
# Autenticación y recuperación de contraseña
# --------------------------------------------------------------------------- #
@csrf_exempt
@api_endpoint
def register(request):
    if response := require_method(request, "POST"):
        return response
    throttle(request, "register", 10, 3600)
    body = parse_body(request)
    name = body.get("name", "").strip() if isinstance(body.get("name"), str) else ""
    email = body.get("email", "").strip().lower() if isinstance(body.get("email"), str) else ""
    password = body.get("password", "") if isinstance(body.get("password"), str) else ""
    if (
        not 2 <= len(name) <= 80
        or len(email) > 254
        or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
        or not password
    ):
        raise APIError(
            400,
            "Ingresa un nombre, un correo válido y una contraseña.",
        )
    community = None
    if body.get("community"):
        community = Community.objects.filter(slug=body.get("community"), is_active=True).first()
        if not community:
            raise APIError(400, "Comunidad no válida.")
    try:
        with transaction.atomic():
            if User.objects.filter(email=email).exists():
                raise APIError(409, "Ese correo ya tiene una cuenta. Inicia sesión.")
            user = User(email=email, name=name, role=User.Role.CITIZEN, community=community)
            user.set_password(password)
            user.save()
            token = create_session_token(user)
    except IntegrityError as error:
        if User.objects.filter(email=email).exists():
            raise APIError(409, "Ese correo ya tiene una cuenta. Inicia sesión.") from error
        raise
    return JsonResponse({"token": token, "user": public_user(user)}, status=201)


def create_session_token(user):
    token = secrets.token_urlsafe(32)
    ApiToken.objects.filter(expires_at__lte=timezone.now()).delete()
    ApiToken.objects.create(
        digest=hashlib.sha256(token.encode()).hexdigest(),
        user=user,
        expires_at=timezone.now() + SESSION_DURATION,
    )
    return token


@csrf_exempt
@api_endpoint
def login(request):
    if response := require_method(request, "POST"):
        return response
    body = parse_body(request)
    email = body.get("email", "").strip().lower() if isinstance(body.get("email"), str) else ""
    password = body.get("password", "") if isinstance(body.get("password"), str) else ""
    throttle(request, "login", 10, 900, email)
    user = authenticate(request, email=email, password=password)
    if not user or not user.is_active:
        raise APIError(401, "Correo o contraseña incorrectos.")
    token = create_session_token(user)
    return JsonResponse({"token": token, "user": public_user(user)})


@csrf_exempt
@api_endpoint
def current_user(request):
    if request.method == "GET":
        return JsonResponse({"user": public_user(get_user(request))})
    if request.method == "PUT":
        actor = get_user(request)
        body = parse_body(request)
        if "community" not in body:
            raise APIError(400, "Indica la comunidad.")
        slug = body["community"]
        if slug in (None, ""):
            actor.community = None
        else:
            community = Community.objects.filter(slug=slug, is_active=True).first() if isinstance(slug, str) else None
            if not community:
                raise APIError(400, "Comunidad no válida.")
            actor.community = community
        actor.save(update_fields=["community"])
        return JsonResponse({"user": public_user(actor)})
    return JsonResponse({"message": "Método no permitido."}, status=405, headers={"Allow": "GET, PUT"})


@csrf_exempt
@api_endpoint
def logout(request):
    if response := require_method(request, "POST"):
        return response
    token = bearer_token(request)
    if token:
        digest = hashlib.sha256(token.encode()).hexdigest()
        ApiToken.objects.filter(pk=digest).delete()
    return JsonResponse({"loggedOut": True})


def send_reset_email(user, token):
    link = f"{settings.SITE_URL.rstrip('/')}/#reset={token}"
    minutes = settings.PASSWORD_RESET_MINUTES
    send_mail(
        "Restablece tu contraseña · Centro de incidencias",
        (
            f"Hola {user.name},\n\n"
            "Recibimos una solicitud para restablecer la contraseña de tu cuenta.\n"
            f"Abre este enlace (vence en {minutes} minutos y solo sirve una vez):\n\n{link}\n\n"
            "Si no fuiste tú, ignora este mensaje: tu contraseña no cambiará."
        ),
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )


@csrf_exempt
@api_endpoint
def password_reset_request(request):
    if response := require_method(request, "POST"):
        return response
    body = parse_body(request)
    email = body.get("email", "").strip().lower() if isinstance(body.get("email"), str) else ""
    throttle(request, "reset-request", 5, 900, email)
    user = User.objects.filter(email=email, is_active=True).first() if email else None
    if user:
        token = secrets.token_urlsafe(32)
        PasswordResetToken.objects.filter(user=user).delete()
        PasswordResetToken.objects.create(
            digest=hashlib.sha256(token.encode()).hexdigest(),
            user=user,
            expires_at=timezone.now() + timedelta(minutes=settings.PASSWORD_RESET_MINUTES),
        )
        try:
            send_reset_email(user, token)
        except Exception:
            logger.exception("No se pudo enviar el correo de recuperación")
    # Misma respuesta exista o no la cuenta, para no revelar qué correos están registrados.
    return JsonResponse({"message": GENERIC_RESET_MESSAGE}, status=202)


@csrf_exempt
@api_endpoint
def password_reset_confirm(request):
    if response := require_method(request, "POST"):
        return response
    throttle(request, "reset-confirm", 10, 900)
    body = parse_body(request)
    token = body.get("token") if isinstance(body.get("token"), str) else ""
    password = body.get("password") if isinstance(body.get("password"), str) else ""
    invalid = APIError(400, "El enlace no es válido o ya expiró. Solicita uno nuevo.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
        raise invalid
    if not password:
        raise APIError(400, "Ingresa una contraseña.")
    record = (
        PasswordResetToken.objects.select_related("user")
        .filter(pk=hashlib.sha256(token.encode()).hexdigest(), used_at__isnull=True)
        .first()
    )
    if not record or record.expires_at <= timezone.now() or not record.user.is_active:
        raise invalid
    with transaction.atomic():
        user = record.user
        user.set_password(password)
        user.save(update_fields=["password"])
        record.used_at = timezone.now()
        record.save(update_fields=["used_at"])
        PasswordResetToken.objects.filter(user=user).exclude(pk=record.pk).delete()
        ApiToken.objects.filter(user=user).delete()  # cierra las sesiones abiertas
    return JsonResponse({"message": "Contraseña actualizada. Ya puedes ingresar."})


# --------------------------------------------------------------------------- #
# Incidencias
# --------------------------------------------------------------------------- #
@csrf_exempt
@api_endpoint
def incident_collection(request):
    if request.method == "GET":
        viewer = get_user(request, required=False)
        include_inactive = is_operator(viewer) and request.GET.get("include") == "all"
        queryset = incident_queryset(viewer, include_inactive=include_inactive)
        slug = request.GET.get("community")
        if slug:
            queryset = queryset.filter(community__slug=slug)
        veracity = request.GET.get("veracity")
        if veracity:
            wanted = [value for value in veracity.split(",") if value in dict(rules.VERACITY_CHOICES)]
            queryset = queryset.filter(veracity__in=wanted)
        return JsonResponse(serialize_incidents(queryset.order_by("-time", "id"), viewer), safe=False)
    if request.method != "POST":
        return JsonResponse({"message": "Método no permitido."}, status=405, headers={"Allow": "GET, POST"})
    actor = get_user(request)
    body = parse_body(request)
    incident_type = body.get("type")
    description = body.get("description")
    lat = valid_coordinate(body.get("lat"), -90, 90)
    lng = valid_coordinate(body.get("lng"), -180, 180)
    if incident_type not in TYPES or not isinstance(description, str) or not description.strip() or lat is None or lng is None:
        raise APIError(400, "Tipo, descripción y coordenadas válidas son obligatorios.")
    require_chile(lat, lng)
    if body.get("confirmDistinct") is not True:
        duplicates = find_duplicates(actor, incident_type, lat, lng)
        if duplicates:
            raise APIError(
                409,
                "Ya hay un reporte parecido cerca. ¿Es el mismo incidente?",
                {
                    "code": "possible_duplicate",
                    "duplicates": [
                        {**item.to_json(), "distanceM": round(distance)} for distance, item in duplicates
                    ],
                },
            )
    now = timezone.now()
    incident = Incident.objects.create(
        id=f"incident-{uuid.uuid4()}",
        type=incident_type,
        description=description.strip()[:2000],
        lat=lat,
        lng=lng,
        time=now,
        reporter=actor,
        reporter_name=actor.name,
        community=assign_community(lat, lng),
        expires_at=expiry_for(incident_type, now),
        history=[{"at": now.isoformat(), "action": "Incidencia registrada", "actor": actor.name}],
    )
    return JsonResponse(incident.to_json(), status=201)


@csrf_exempt
@api_endpoint
def admin_stats(request):
    if response := require_method(request, "GET"):
        return response
    actor = get_user(request)
    require_role(actor, OPERATOR_ROLES)
    incidents = Incident.objects.all()
    if request.GET.get("community"):
        incidents = incidents.filter(community__slug=request.GET["community"])
    live = incidents.filter(hidden=False).filter(active_filter())
    return JsonResponse(
        {
            "total": live.count(),
            "open": live.filter(status="abierto").count(),
            "inProgress": live.filter(status="en proceso").count(),
            "resolved": live.filter(status="resuelto").count(),
            "highPriority": live.filter(priority__in=("alta", "crítica")).count(),
            "confirmed": live.filter(veracity__in=(rules.COMMUNITY_CONFIRMED, rules.VERIFIED)).count(),
            "hidden": incidents.filter(hidden=True).count(),
            "expired": incidents.filter(hidden=False, expires_at__lte=timezone.now()).count(),
        }
    )


@csrf_exempt
@api_endpoint
def admin_incidents(request):
    if response := require_method(request, "GET"):
        return response
    actor = get_user(request)
    require_role(actor, (User.Role.ADMINISTRATOR,))
    queryset = Incident.objects.select_related("assigned_to", "community", "reporter").order_by("-time", "id")
    community = request.GET.get("community")
    if community:
        queryset = queryset.filter(community__slug=community)
    return JsonResponse(serialize_incidents(queryset, actor), safe=False)


@csrf_exempt
@api_endpoint
def users(request):
    if response := require_method(request, "GET"):
        return response
    actor = get_user(request)
    require_role(actor, OPERATOR_ROLES)
    people = User.objects.select_related("community").order_by("name", "email")
    return JsonResponse(
        [public_user(person) for person in people],
        safe=False,
    )


@csrf_exempt
@api_endpoint
def user_role(request, user_id):
    if response := require_method(request, "PUT"):
        return response
    actor = get_user(request)
    require_role(actor, (User.Role.ADMINISTRATOR,))
    body = parse_body(request)
    role = body.get("role")
    if role not in VALID_ROLES:
        raise APIError(400, "Rol no válido.")
    user = User.objects.filter(pk=user_id).first()
    if not user:
        raise APIError(404, "Usuario no encontrado.")
    if user.pk == actor.pk and user.role != role:
        raise APIError(400, "No puedes cambiar tu propio rol.")
    if (
        user.role == User.Role.ADMINISTRATOR
        and role != User.Role.ADMINISTRATOR
        and User.objects.filter(role=User.Role.ADMINISTRATOR).count() == 1
    ):
        raise APIError(409, "No se puede retirar el único rol administrador.")
    user.role = role
    user.save(update_fields=["role"])
    return JsonResponse({"user": public_user(user)})


@csrf_exempt
@api_endpoint
def notifications(request):
    if response := require_method(request, "GET"):
        return response
    actor = get_user(request)
    records = Notification.objects.filter(user=actor).select_related("incident")
    return JsonResponse([record.to_json() for record in records], safe=False)


@csrf_exempt
@api_endpoint
def mark_notifications_read(request):
    if response := require_method(request, "PUT"):
        return response
    actor = get_user(request)
    Notification.objects.filter(user=actor, read=False).update(read=True)
    return JsonResponse({"updated": True})


@csrf_exempt
@api_endpoint
def incident_detail(request, incident_id):
    incident = Incident.objects.select_related("assigned_to", "community", "reporter").filter(pk=incident_id).first()
    if not incident:
        raise APIError(404, "Incidencia no encontrada.")
    if request.method == "DELETE":
        actor = get_user(request)
        # El administrador puede eliminar cualquier reporte; cada persona, los suyos.
        if actor.role != User.Role.ADMINISTRATOR and incident.reporter_id != actor.pk:
            raise APIError(403, "Solo puedes eliminar tus propios reportes.")
        incident.delete()
        return JsonResponse({"deleted": incident_id})
    if request.method != "PUT":
        return JsonResponse(
            {"message": "Método no permitido."},
            status=405,
            headers={"Allow": "PUT, DELETE"},
        )

    actor = get_user(request)
    body = parse_body(request)
    operator = is_operator(actor)
    with transaction.atomic():
        if "comment" in body:
            if not operator:
                raise APIError(403, "Solo operadores pueden comentar en incidencias.")
            text = body["comment"].strip() if isinstance(body["comment"], str) else ""
            if not text or len(text) > 1000:
                raise APIError(400, "El comentario debe tener entre 1 y 1000 caracteres.")
            incident.comments.append(
                {"id": str(uuid.uuid4()), "text": text, "actor": actor.name, "at": timezone.now().isoformat()}
            )
            add_history(incident, "Comentario añadido", actor.name)
            create_notification(incident, f"Un operador comentó tu incidencia: {incident.type}")
        elif not operator:
            if incident.reporter_id != actor.pk or incident.status != "abierto":
                raise APIError(403, "Solo puedes editar tus propias incidencias abiertas.")
            allowed = {"type", "description", "lat", "lng"}
            if any(key not in allowed for key in body):
                raise APIError(403, "No tienes permiso para modificar el estado o la gestión de esta incidencia.")
            if not body:
                raise APIError(400, "Indica al menos un dato para actualizar.")
            if (incident.confirm_count or incident.deny_count) and any(k in body for k in ("type", "lat", "lng")):
                raise APIError(
                    403, "Tras recibir votos solo puedes editar la descripción, no el tipo ni la ubicación."
                )
            if "type" in body:
                if body["type"] not in TYPES:
                    raise APIError(400, "Tipo de incidencia no válido.")
                incident.type = body["type"]
                incident.expires_at = expiry_for(incident.type, incident.time)
            if "description" in body:
                description = body["description"]
                if not isinstance(description, str) or not description.strip() or len(description) > 2000:
                    raise APIError(400, "Descripción no válida.")
                incident.description = description.strip()
            if "lat" in body:
                lat = valid_coordinate(body["lat"], -90, 90)
                if lat is None:
                    raise APIError(400, "Latitud no válida.")
                incident.lat = lat
            if "lng" in body:
                lng = valid_coordinate(body["lng"], -180, 180)
                if lng is None:
                    raise APIError(400, "Longitud no válida.")
                incident.lng = lng
            if "lat" in body or "lng" in body:
                require_chile(incident.lat, incident.lng)
                incident.community = assign_community(incident.lat, incident.lng)
            add_history(incident, "Reporte editado por el ciudadano", actor.name)
        else:
            if "status" in body:
                if body["status"] not in STATUSES:
                    raise APIError(400, "Estado no válido.")
                if body["status"] != incident.status:
                    incident.status = body["status"]
                    incident.resolved_at = timezone.now() if incident.status == "resuelto" else None
                    add_history(incident, f"Estado cambiado a {incident.status}", actor.name)
                    create_notification(
                        incident,
                        f'El estado de tu incidencia "{incident.type}" cambió a {incident.status}.',
                    )
            if "priority" in body:
                if body["priority"] not in PRIORITIES:
                    raise APIError(400, "Prioridad no válida.")
                if body["priority"] != incident.priority:
                    incident.priority = body["priority"]
                    add_history(incident, f"Prioridad cambiada a {incident.priority}", actor.name)
            if "veracity" in body:
                if body["veracity"] not in rules.OPERATOR_VERACITY:
                    raise APIError(400, "Estado de veracidad no válido.")
                if body["veracity"] == rules.UNVERIFIED:
                    # Devuelve el control a los votos de la comunidad.
                    change_veracity(incident, rules.UNVERIFIED, actor.name)
                    refresh_veracity(incident)
                else:
                    change_veracity(incident, body["veracity"], actor.name)
            if "hidden" in body:
                if not isinstance(body["hidden"], bool):
                    raise APIError(400, "El valor de «hidden» debe ser verdadero o falso.")
                if body["hidden"]:
                    hide_incident(incident, actor.name)
                else:
                    restore_incident(incident, actor.name)
            if body.get("renew") is True:
                incident.expires_at = expiry_for(incident.type)
                add_history(incident, "Vigencia renovada", actor.name)
            if "assignedTo" in body:
                assignee_id = body["assignedTo"]
                assignee = None
                if assignee_id is not None:
                    if not isinstance(assignee_id, str):
                        raise APIError(400, "La asignación debe corresponder a un operador válido.")
                    assignee = User.objects.filter(pk=assignee_id).first()
                    if not assignee or assignee.role not in OPERATOR_ROLES:
                        raise APIError(400, "La asignación debe corresponder a un operador válido.")
                incident.assigned_to = assignee
                add_history(
                    incident,
                    f"Asignada a {assignee.name}" if assignee else "Asignación retirada",
                    actor.name,
                )
        incident.save()
    return JsonResponse(serialize_incidents([incident], actor)[0])


@csrf_exempt
@api_endpoint
def incident_vote(request, incident_id):
    """POST: confirmar o negar (cambia el voto previo). DELETE: retirar el voto."""
    if request.method not in ("POST", "DELETE"):
        return JsonResponse({"message": "Método no permitido."}, status=405, headers={"Allow": "POST, DELETE"})
    actor = get_user(request)
    body = parse_body(request) if request.method == "POST" else {}
    with transaction.atomic():
        incident = Incident.objects.select_for_update().filter(pk=incident_id).first()
        if not incident or not incident_queryset(actor).filter(pk=incident_id).exists():
            raise APIError(404, "Incidencia no encontrada.")
        if incident.reporter_id == actor.pk:
            raise APIError(403, "No puedes votar tu propio reporte.")
        if incident.is_expired:
            raise APIError(409, "Esta incidencia ya caducó.")
        if incident.status == "resuelto":
            raise APIError(409, "Esta incidencia ya fue resuelta.")
        if request.method == "DELETE":
            Vote.objects.filter(incident=incident, user=actor).delete()
        else:
            value = {"confirmar": rules.VOTE_CONFIRM, "negar": rules.VOTE_DENY}.get(body.get("value"))
            if value is None:
                raise APIError(400, "El voto debe ser «confirmar» o «negar».")
            now = timezone.now()
            Vote.objects.update_or_create(
                incident=incident,
                user=actor,
                defaults={"value": value, "weight": actor.vote_weight, "updated_at": now},
            )
            if value == rules.VOTE_CONFIRM:
                extend_expiry(incident)
        refresh_veracity(incident)
        incident.save()
    return JsonResponse(serialize_incidents([incident], actor)[0])


@csrf_exempt
@api_endpoint
def incident_flag(request, incident_id):
    """POST: denunciar un reporte abusivo/falso. DELETE: retirar la denuncia."""
    if request.method not in ("POST", "DELETE"):
        return JsonResponse({"message": "Método no permitido."}, status=405, headers={"Allow": "POST, DELETE"})
    actor = get_user(request)
    body = parse_body(request) if request.method == "POST" else {}
    with transaction.atomic():
        incident = Incident.objects.select_for_update().filter(pk=incident_id).first()
        if not incident or not incident_queryset(actor).filter(pk=incident_id).exists():
            raise APIError(404, "Incidencia no encontrada.")
        if incident.reporter_id == actor.pk:
            raise APIError(403, "No puedes denunciar tu propio reporte.")
        if request.method == "DELETE":
            Flag.objects.filter(incident=incident, user=actor).delete()
        else:
            reason = body.get("reason")
            if reason not in rules.FLAG_REASONS:
                raise APIError(400, "Indica el motivo de la denuncia.")
            Flag.objects.update_or_create(
                incident=incident,
                user=actor,
                defaults={"reason": reason, "weight": actor.vote_weight},
            )
        flags = list(Flag.objects.filter(incident=incident).values_list("weight", flat=True))
        incident.flag_count = len(flags)
        incident.flag_score = round(sum(flags), 2)
        if (
            not incident.hidden
            and incident.flag_count >= rules.FLAG_MIN_USERS
            and incident.flag_score >= rules.FLAG_HIDE_SCORE
        ):
            hide_incident(incident, "Sistema (denuncias de la comunidad)")
        incident.save()
    if incident.hidden:
        return JsonResponse({"hidden": True, "id": incident.pk})
    return JsonResponse(serialize_incidents([incident], actor)[0])


@csrf_exempt
@api_endpoint
def api_not_found(request, remaining):
    return JsonResponse({"message": "Endpoint no encontrado."}, status=404)
