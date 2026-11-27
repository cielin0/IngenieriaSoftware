import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent

_INSECURE_DEV_KEY = "django-insecure-local-development-key-change-before-deploying"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", _INSECURE_DEV_KEY)
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes"}
if not DEBUG and SECRET_KEY == _INSECURE_DEV_KEY:
    # Los enlaces de registro se firman con esta clave: con la de desarrollo cualquiera podría falsificarlos.
    raise ImproperlyConfigured("Define DJANGO_SECRET_KEY con una clave privada cuando DJANGO_DEBUG sea false.")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "incidents",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "los_lagos.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "los_lagos.wsgi.application"

DB_ENGINE = os.environ.get("DJANGO_DB_ENGINE", "mysql").lower()
if DB_ENGINE == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("MYSQL_DATABASE", "los_lagos"),
            "USER": os.environ.get("MYSQL_USER", "root"),
            "PASSWORD": os.environ.get("MYSQL_PASSWORD", ""),
            "HOST": os.environ.get("MYSQL_HOST", "127.0.0.1"),
            "PORT": os.environ.get("MYSQL_PORT", "3306"),
            "OPTIONS": {"charset": "utf8mb4"},
            "CONN_MAX_AGE": 60,
        }
    }
elif DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("DJANGO_DATABASE", str(BASE_DIR / "db.sqlite3")),
        }
    }
else:
    raise ValueError("DJANGO_DB_ENGINE debe ser 'mysql' o 'sqlite'.")

AUTH_USER_MODEL = "incidents.User"
AUTH_PASSWORD_VALIDATORS = []
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "incidents.hashers.LegacyScryptPasswordHasher",
]

LANGUAGE_CODE = "es-cl"
TIME_ZONE = "America/Santiago"
USE_I18N = True
USE_TZ = True

# Django envía por defecto "Referrer-Policy: same-origin", lo que impide que el
# navegador mande el Referer a los servidores de mosaicos de OpenStreetMap y
# estos responden 403 (política de uso de tiles). Se envía solo el origen.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "collected_static"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 1_000_000

# --- Recuperación de contraseña / correo -------------------------------------------
# Por defecto el correo se imprime en la consola del servidor (desarrollo). Para enviar
# correos reales define EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend y las
# variables EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS.
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000")
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "false").lower() in {"1", "true", "yes"}
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Centro de incidencias <no-reply@localhost>")
PASSWORD_RESET_MINUTES = 30

# Límite de intentos (login, registro, recuperación). Usa la caché de Django; con varios
# procesos/servidores configura una caché compartida (Redis/Memcached) en CACHES.
API_THROTTLE_ENABLED = True
