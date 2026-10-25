from pathlib import Path


# ============================================================
# BASE DIRECTORY
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# SECURITY
# ============================================================

SECRET_KEY = (
    "django-insecure-7rw(*#21-kik%8=6^-lhsvhs0c9v2)"
    "a-qlctle#_fster2mlt$"
)

DEBUG = True

ALLOWED_HOSTS = [
'*'
]


# ============================================================
# APPLICATIONS
# ============================================================

INSTALLED_APPS = [

    # --------------------------------------------------------
    # Django
    # --------------------------------------------------------

    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # --------------------------------------------------------
    # Celery Beat
    # --------------------------------------------------------

    "django_celery_beat",

    # --------------------------------------------------------
    # Application
    # --------------------------------------------------------

    "streaming",
]


# ============================================================
# MIDDLEWARE
# ============================================================

MIDDLEWARE = [

    "django.middleware.security.SecurityMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    "django.middleware.common.CommonMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ============================================================
# URL CONFIGURATION
# ============================================================

ROOT_URLCONF = "config.urls"


# ============================================================
# TEMPLATES
# ============================================================

TEMPLATES = [

    {
        "BACKEND": (
            "django.template.backends.django.DjangoTemplates"
        ),

        "DIRS": [
            BASE_DIR / "templates",
        ],

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


# ============================================================
# WSGI / ASGI
# ============================================================

WSGI_APPLICATION = "config.wsgi.application"


# ============================================================
# DATABASE
# ============================================================

DATABASES = {

    "default": {

        "ENGINE": (
            "django.db.backends.sqlite3"
        ),

        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# ============================================================
# PASSWORD VALIDATION
# ============================================================

AUTH_PASSWORD_VALIDATORS = [

    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },

    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
    },

    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        ),
    },

    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        ),
    },
]


# ============================================================
# INTERNATIONALIZATION
# ============================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Kolkata"

USE_I18N = True

USE_TZ = True


# ============================================================
# STATIC FILES
# ============================================================

STATIC_URL = "/static/"

STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]


# ============================================================
# MEDIA FILES
# ============================================================

MEDIA_URL = "/media/"

MEDIA_ROOT = BASE_DIR / "media"


# ============================================================
# DEFAULT PRIMARY KEY
# ============================================================

DEFAULT_AUTO_FIELD = (
    "django.db.models.BigAutoField"
)


# ============================================================
# REDIS / MEMURAI
# ============================================================

# Memurai runs as a Redis-compatible server on Windows.
#
# Default:
#
#     Host: 127.0.0.1
#     Port: 6379
#
# Test with:
#
#     Test-NetConnection 127.0.0.1 -Port 6379
#
# ============================================================

REDIS_HOST = "127.0.0.1"

REDIS_PORT = 6379

REDIS_DB = 0


REDIS_URL = (
    f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
)


# ============================================================
# CELERY
# ============================================================

# ------------------------------------------------------------
# Broker
# ------------------------------------------------------------

CELERY_BROKER_URL = REDIS_URL


# ------------------------------------------------------------
# Result Backend
# ------------------------------------------------------------

CELERY_RESULT_BACKEND = (
    f"redis://{REDIS_HOST}:{REDIS_PORT}/1"
)


# ------------------------------------------------------------
# Serialization
# ------------------------------------------------------------

CELERY_ACCEPT_CONTENT = [
    "json",
]

CELERY_TASK_SERIALIZER = "json"

CELERY_RESULT_SERIALIZER = "json"


# ------------------------------------------------------------
# Timezone
# ------------------------------------------------------------

CELERY_TIMEZONE = TIME_ZONE

CELERY_ENABLE_UTC = True


# ------------------------------------------------------------
# Task Tracking
# ------------------------------------------------------------

CELERY_TASK_TRACK_STARTED = True


# ------------------------------------------------------------
# Task Limits
# ------------------------------------------------------------

# 12 hours maximum task runtime.

CELERY_TASK_TIME_LIMIT = (
    60 * 60 * 12
)


# 11 hours soft limit.

CELERY_TASK_SOFT_TIME_LIMIT = (
    60 * 60 * 11
)


# ------------------------------------------------------------
# Worker
# ------------------------------------------------------------

CELERY_WORKER_PREFETCH_MULTIPLIER = 1

CELERY_WORKER_MAX_TASKS_PER_CHILD = 100


# ------------------------------------------------------------
# Reliability
# ------------------------------------------------------------

CELERY_TASK_ACKS_LATE = True

CELERY_TASK_REJECT_ON_WORKER_LOST = True


# ============================================================
# CELERY BEAT
# ============================================================

CELERY_BEAT_SCHEDULER = (
    "django_celery_beat.schedulers.DatabaseScheduler"
)


# ============================================================
# FFMPEG
# ============================================================

# Your system already has FFmpeg in Windows PATH.
#
# You verified:
#
#     ffmpeg -version
#
# works successfully.
#
# Therefore:

FFMPEG_BINARY = "ffmpeg"


# If you ever need an absolute path:
#
# FFMPEG_BINARY = (
#     r"C:\ffmpeg\bin\ffmpeg.exe"
# )


# ============================================================
# FILE UPLOAD SETTINGS
# ============================================================

# ------------------------------------------------------------
# Maximum request body
# ------------------------------------------------------------

# 2 GB

DATA_UPLOAD_MAX_MEMORY_SIZE = (
    2 * 1024 * 1024 * 1024
)


# ------------------------------------------------------------
# Uploaded file memory threshold
# ------------------------------------------------------------

# 2 GB

FILE_UPLOAD_MAX_MEMORY_SIZE = (
    2 * 1024 * 1024 * 1024
)


# ============================================================
# LOGIN
# ============================================================

LOGIN_URL = "/accounts/login/"

LOGIN_REDIRECT_URL = "/"

LOGOUT_REDIRECT_URL = "/"


# ============================================================
# DEVELOPMENT
# ============================================================

# Development only.
#
# Do not use DEBUG=True in production.

CELERY_BEAT_SCHEDULE = {
    "start-scheduled-streams": {
        "task": "streaming.start_scheduled_streams",
        "schedule": 60.0,
    },
}


FILE_UPLOAD_HANDLERS = [
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]

FILE_UPLOAD_TEMP_DIR = None

DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024