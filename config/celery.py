import os

from celery import Celery


# ============================================================
# DJANGO SETTINGS
# ============================================================

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)


# ============================================================
# CELERY APP
# ============================================================

app = Celery(
    "config"
)


# ============================================================
# LOAD DJANGO CELERY SETTINGS
# ============================================================

app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)


# ============================================================
# AUTO DISCOVER TASKS
# ============================================================

app.autodiscover_tasks()


# ============================================================
# CELERY BEAT SCHEDULE
# ============================================================
#
# Check scheduled streaming sessions every 60 seconds.
#
# This calls:
#
# streaming.tasks.start_scheduled_streams
#
# That task checks:
#
# status = QUEUED
# auto_start = True
# scheduled_start <= current time
#
# and then calls:
#
# start_streaming_session.delay(session_id)
#
# ============================================================

app.conf.beat_schedule = {

    "start-scheduled-streams-every-minute": {

        "task": "streaming.start_scheduled_streams",

        "schedule": 60.0,

    },

}


# ============================================================
# TIMEZONE
# ============================================================

app.conf.timezone = "Asia/Kolkata"


# ============================================================
# UTC
# ============================================================

app.conf.enable_utc = True