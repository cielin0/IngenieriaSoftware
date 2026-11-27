"""Reglas de negocio puras (sin dependencias de Django).

Todo lo que se pueda querer ajustar (tipos de incidente, caducidad, umbrales de
veracidad, pesos de reputación) vive aquí para tocarlo en un solo lugar.
"""

# --------------------------------------------------------------------------- #
# Tipos de incidente
#   ttl_days:      días que una incidencia permanece visible sin nuevas confirmaciones
#   dup_radius_m:  radio para sugerir "¿es el mismo incidente?" al reportar
# --------------------------------------------------------------------------- #
TYPE_CONFIG = [
    {"name": "Derrumbe en ruta", "color": "#dc4c3f", "short": "Derrumbes", "ttl_days": 14, "dup_radius_m": 400},
    {"name": "Accidente vial", "color": "#2878a8", "short": "Accidentes", "ttl_days": 2, "dup_radius_m": 400},
    {"name": "Nevazón o ruta cerrada", "color": "#5aa7c9", "short": "Nevazón", "ttl_days": 5, "dup_radius_m": 1500},
    {"name": "Problema de conectividad", "color": "#d28b12", "short": "Conectividad", "ttl_days": 7, "dup_radius_m": 1000},
    {"name": "Corte de luz", "color": "#e0b400", "short": "Corte de luz", "ttl_days": 3, "dup_radius_m": 1000},
    {"name": "Corte de agua", "color": "#1d8fd1", "short": "Corte de agua", "ttl_days": 3, "dup_radius_m": 1000},
    {"name": "Emergencia climática", "color": "#7356bd", "short": "Clima", "ttl_days": 5, "dup_radius_m": 3000},
    {"name": "Inundación", "color": "#0e6ba8", "short": "Inundación", "ttl_days": 10, "dup_radius_m": 1000},
    {"name": "Incendio forestal", "color": "#e8590c", "short": "Incendio forestal", "ttl_days": 14, "dup_radius_m": 2000},
    {"name": "Incendio estructural", "color": "#c2255c", "short": "Incendio estructural", "ttl_days": 2, "dup_radius_m": 300},
    {"name": "Actividad volcánica", "color": "#862e2e", "short": "Volcán", "ttl_days": 30, "dup_radius_m": 5000},
    {"name": "Sismo", "color": "#6741d9", "short": "Sismo", "ttl_days": 2, "dup_radius_m": 20000},
    {"name": "Otra", "color": "#64748b", "short": "Otros", "ttl_days": 7, "dup_radius_m": 400},
]
TYPE_NAMES = tuple(item["name"] for item in TYPE_CONFIG)
_TYPE_BY_NAME = {item["name"]: item for item in TYPE_CONFIG}
DEFAULT_TTL_DAYS = 7
DEFAULT_DUP_RADIUS_M = 400


def ttl_days(incident_type):
    return _TYPE_BY_NAME.get(incident_type, {}).get("ttl_days", DEFAULT_TTL_DAYS)


def dup_radius_m(incident_type):
    return _TYPE_BY_NAME.get(incident_type, {}).get("dup_radius_m", DEFAULT_DUP_RADIUS_M)


# --------------------------------------------------------------------------- #
# Veracidad
# --------------------------------------------------------------------------- #
UNVERIFIED = "sin_verificar"
COMMUNITY_CONFIRMED = "confirmado_comunidad"
VERIFIED = "verificado"
PROBABLY_FALSE = "probablemente_falso"
FALSE = "falso"

VERACITY_LABELS = {
    UNVERIFIED: "Sin verificar",
    COMMUNITY_CONFIRMED: "Confirmado por la comunidad",
    VERIFIED: "Verificado por un operador",
    PROBABLY_FALSE: "Probablemente falso",
    FALSE: "Falso (descartado)",
}
VERACITY_CHOICES = tuple(VERACITY_LABELS.items())
# Estados fijados por un operador: la votación automática no los modifica.
LOCKED_VERACITY = (VERIFIED, FALSE)
# Estados que un operador puede asignar a mano ("sin verificar" devuelve el control a los votos).
OPERATOR_VERACITY = (UNVERIFIED, VERIFIED, FALSE)

# Puntos de reputación que recibe quien reportó, según el estado de su incidencia.
REPUTATION_BY_VERACITY = {
    UNVERIFIED: 0,
    COMMUNITY_CONFIRMED: 5,
    VERIFIED: 8,
    PROBABLY_FALSE: -5,
    FALSE: -15,
}

# --------------------------------------------------------------------------- #
# Votos
# --------------------------------------------------------------------------- #
VOTE_CONFIRM = 1
VOTE_DENY = -1

CONFIRM_SCORE = 5.0       # puntaje neto (suma de pesos) para "confirmado por la comunidad"
CONFIRM_MIN_VOTERS = 3    # y al menos esta cantidad de personas distintas confirmando
FALSE_SCORE = -3.0        # puntaje neto para "probablemente falso"
FALSE_MIN_VOTERS = 3      # y al menos esta cantidad de personas distintas negando

# --------------------------------------------------------------------------- #
# Reputación
# --------------------------------------------------------------------------- #
REPUTATION_MIN = -50
REPUTATION_MAX = 100
MIN_WEIGHT = 0.25
MAX_WEIGHT = 2.0


def vote_weight(reputation):
    """Peso del voto: 1.0 con reputación 0; sube hasta 2.0 y baja hasta 0.25."""
    weight = 1 + (reputation or 0) / 50
    return round(max(MIN_WEIGHT, min(MAX_WEIGHT, weight)), 2)


def reputation_level(reputation):
    reputation = reputation or 0
    if reputation >= 40:
        return "Muy confiable"
    if reputation >= 15:
        return "Confiable"
    if reputation > -15:
        return "Nuevo / neutral"
    return "Poco confiable"


def evaluate_veracity(votes):
    """votes: iterable de (valor, peso). Devuelve (estado, puntaje, confirmaciones, negaciones)."""
    score = 0.0
    confirms = 0
    denies = 0
    for value, weight in votes:
        if value == VOTE_CONFIRM:
            confirms += 1
            score += weight
        elif value == VOTE_DENY:
            denies += 1
            score -= weight
    if score >= CONFIRM_SCORE and confirms >= CONFIRM_MIN_VOTERS:
        state = COMMUNITY_CONFIRMED
    elif score <= FALSE_SCORE and denies >= FALSE_MIN_VOTERS:
        state = PROBABLY_FALSE
    else:
        state = UNVERIFIED
    return state, round(score, 2), confirms, denies


# --------------------------------------------------------------------------- #
# Denuncias de abuso ("reportar incidencia")
# --------------------------------------------------------------------------- #
FLAG_REASONS = {
    "falso": "Información falsa",
    "broma": "Broma o troleo",
    "spam": "Spam o publicidad",
    "ofensivo": "Contenido ofensivo",
    "duplicado": "Reporte duplicado",
}
FLAG_HIDE_SCORE = 3.0     # suma de pesos de las denuncias para ocultar la incidencia
FLAG_MIN_USERS = 3        # y al menos esta cantidad de personas distintas
HIDE_PENALTY = 5          # puntos de reputación que pierde quien la creó
