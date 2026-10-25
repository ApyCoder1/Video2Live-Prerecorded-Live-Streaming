# streaming/models.py

import os
import uuid

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


# ============================================================
# COMMON
# ============================================================

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ============================================================
# VIDEO
# ============================================================

class Video(TimeStampedModel):

    class Status(models.TextChoices):
        UPLOADING = "uploading", "Uploading"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="videos"
    )

    title = models.CharField(
        max_length=255
    )

    description = models.TextField(
        blank=True
    )

    # Original uploaded file
    video_file = models.FileField(
        upload_to="videos/%Y/%m/%d/"
    )

    thumbnail = models.ImageField(
        upload_to="video_thumbnails/%Y/%m/%d/",
        blank=True,
        null=True
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UPLOADING
    )

    # Video information
    duration_seconds = models.PositiveIntegerField(
        default=0
    )

    file_size = models.BigIntegerField(
        default=0,
        help_text="File size in bytes"
    )

    width = models.PositiveIntegerField(
        default=0
    )

    height = models.PositiveIntegerField(
        default=0
    )

    frame_rate = models.FloatField(
        default=0
    )

    codec = models.CharField(
        max_length=100,
        blank=True
    )

    bitrate = models.BigIntegerField(
        default=0,
        help_text="Bitrate in bits per second"
    )

    mime_type = models.CharField(
        max_length=100,
        blank=True
    )

    # Processing
    processing_started_at = models.DateTimeField(
        null=True,
        blank=True
    )

    processing_completed_at = models.DateTimeField(
        null=True,
        blank=True
    )

    processing_error = models.TextField(
        blank=True
    )

    # Visibility
    is_public = models.BooleanField(
        default=True
    )

    is_active = models.BooleanField(
        default=True
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def duration_minutes(self):
        return self.duration_seconds / 60

    @property
    def file_size_mb(self):
        return self.file_size / (1024 * 1024)


# ============================================================
# PLAYLIST
# ============================================================

class Playlist(TimeStampedModel):

    class PlayMode(models.TextChoices):
        ONE_BY_ONE = "one_by_one", "One by One"
        REPEAT = "repeat", "Repeat Playlist"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="playlists"
    )

    name = models.CharField(
        max_length=255
    )

    description = models.TextField(
        blank=True
    )

    play_mode = models.CharField(
        max_length=20,
        choices=PlayMode.choices,
        default=PlayMode.ONE_BY_ONE
    )

    is_active = models.BooleanField(
        default=True
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


# ============================================================
# PLAYLIST VIDEO
# ============================================================

class PlaylistVideo(TimeStampedModel):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    playlist = models.ForeignKey(
        Playlist,
        on_delete=models.CASCADE,
        related_name="items"
    )

    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="playlist_items"
    )

    position = models.PositiveIntegerField(
        default=0
    )

    is_enabled = models.BooleanField(
        default=True
    )

    class Meta:
        ordering = ["position", "created_at"]

        constraints = [
            models.UniqueConstraint(
                fields=["playlist", "video"],
                name="unique_video_in_playlist"
            )
        ]

    def __str__(self):
        return f"{self.playlist.name} - {self.video.title}"


# ============================================================
# YOUTUBE CHANNEL
# ============================================================

class YouTubeChannel(TimeStampedModel):

    class Status(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Error"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="youtube_channels"
    )

    channel_id = models.CharField(
        max_length=255,
        unique=True
    )

    channel_title = models.CharField(
        max_length=255,
        blank=True
    )

    channel_url = models.URLField(
        blank=True
    )

    # OAuth
    access_token = models.TextField(
        blank=True
    )

    refresh_token = models.TextField(
        blank=True
    )

    token_expires_at = models.DateTimeField(
        null=True,
        blank=True
    )

    scopes = models.TextField(
        blank=True
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CONNECTED
    )

    last_error = models.TextField(
        blank=True
    )

    last_connected_at = models.DateTimeField(
        null=True,
        blank=True
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.channel_title or self.channel_id

    @property
    def token_expired(self):
        if not self.token_expires_at:
            return False

        return timezone.now() >= self.token_expires_at


# ============================================================
# YOUTUBE STREAM
# ============================================================

class YouTubeStream(TimeStampedModel):

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        ERROR = "error", "Error"

    # ========================================================
    # PRIMARY KEY
    # ========================================================

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    # ========================================================
    # YOUTUBE CHANNEL
    # ========================================================

    channel = models.ForeignKey(
        YouTubeChannel,
        on_delete=models.CASCADE,
        related_name="streams"
    )

    # ========================================================
    # YOUTUBE STREAM INFORMATION
    # ========================================================

    youtube_stream_id = models.CharField(
        max_length=255,
        unique=True
    )

    stream_name = models.CharField(
        max_length=255,
        blank=True
    )

    # ========================================================
    # RTMP INGESTION
    # ========================================================

    # Example:
    # rtmp://a.rtmp.youtube.com/live2
    #
    # CharField is intentionally used instead of URLField
    # because RTMP is not an HTTP/HTTPS URL.

    ingestion_address = models.CharField(
        max_length=500,
        blank=True
    )

    # Backup RTMP/RTMPS ingestion address
    ingestion_backup_address = models.CharField(
        max_length=500,
        blank=True
    )

    # ========================================================
    # YOUTUBE STREAM KEY
    # ========================================================

    stream_key = models.TextField(
        blank=True
    )

    # ========================================================
    # VIDEO CONFIGURATION
    # ========================================================

    resolution = models.CharField(
        max_length=50,
        default="1080p"
    )

    frame_rate = models.CharField(
        max_length=20,
        default="30fps"
    )

    # ========================================================
    # STATUS
    # ========================================================

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CREATED
    )

    # ========================================================
    # ERROR INFORMATION
    # ========================================================

    last_error = models.TextField(
        blank=True
    )

    # ========================================================
    # META
    # ========================================================

    class Meta:
        ordering = ["-created_at"]

    # ========================================================
    # STRING
    # ========================================================

    def __str__(self):
        return self.stream_name or self.youtube_stream_id


# ============================================================
# STREAMING SESSION
# ============================================================

class StreamingSession(TimeStampedModel):

    # ========================================================
    # STREAM MODE
    # ========================================================

    class Mode(models.TextChoices):

        ONE_BY_ONE = (
            "one_by_one",
            "One by One"
        )

        REPEAT = (
            "repeat",
            "Repeat"
        )


    # ========================================================
    # SESSION STATUS
    # ========================================================

    class Status(models.TextChoices):

        DRAFT = (
            "draft",
            "Draft"
        )

        QUEUED = (
            "queued",
            "Queued"
        )

        STARTING = (
            "starting",
            "Starting"
        )

        LIVE = (
            "live",
            "Live"
        )

        STOPPING = (
            "stopping",
            "Stopping"
        )

        STOPPED = (
            "stopped",
            "Stopped"
        )

        FAILED = (
            "failed",
            "Failed"
        )


    # ========================================================
    # PRIMARY KEY
    # ========================================================

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )


    # ========================================================
    # OWNER
    # ========================================================

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="streaming_sessions"
    )


    # ========================================================
    # PLAYLIST
    # ========================================================

    playlist = models.ForeignKey(
        Playlist,
        on_delete=models.PROTECT,
        related_name="streaming_sessions"
    )


    # ========================================================
    # STREAM MODE
    # ========================================================

    mode = models.CharField(
        max_length=20,
        choices=Mode.choices,
        default=Mode.ONE_BY_ONE
    )


    # ========================================================
    # SESSION STATUS
    # ========================================================

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT
    )


    # ========================================================
    # YOUTUBE RTMP CONFIGURATION
    # ========================================================

    youtube_rtmp_url = models.CharField(
    max_length=500,
    default="rtmp://a.rtmp.youtube.com/live2"
)


    youtube_stream_key = models.CharField(
        max_length=255,
        blank=True
    )


    # ========================================================
    # YOUTUBE VIDEO / BROADCAST INFORMATION
    # ========================================================

    youtube_broadcast_id = models.CharField(
        max_length=255,
        blank=True
    )


    youtube_video_id = models.CharField(
        max_length=255,
        blank=True
    )


    youtube_url = models.URLField(
        blank=True
    )


    # ========================================================
    # YOUTUBE METADATA
    # ========================================================

    title = models.CharField(
        max_length=255,
        blank=True
    )


    description = models.TextField(
        blank=True
    )


    youtube_privacy = models.CharField(
        max_length=20,
        choices=[
            (
                "public",
                "Public"
            ),
            (
                "unlisted",
                "Unlisted"
            ),
            (
                "private",
                "Private"
            ),
        ],
        default="unlisted"
    )


    # ========================================================
    # SCHEDULING
    # ========================================================

    scheduled_start = models.DateTimeField(
        null=True,
        blank=True
    )


    # ========================================================
    # AUTOMATION
    # ========================================================

    auto_start = models.BooleanField(
        default=False
    )


    auto_continue = models.BooleanField(
        default=True
    )


    auto_stop = models.BooleanField(
        default=True
    )


    # ========================================================
    # STREAM TIMING
    # ========================================================

    started_at = models.DateTimeField(
        null=True,
        blank=True
    )


    ended_at = models.DateTimeField(
        null=True,
        blank=True
    )


    # ========================================================
    # CURRENT PLAYLIST POSITION
    # ========================================================

    current_item_position = models.PositiveIntegerField(
        default=0
    )


    # ========================================================
    # CURRENT VIDEO TIMING
    # ========================================================

    current_video_started_at = models.DateTimeField(
        null=True,
        blank=True
    )


    current_video_ended_at = models.DateTimeField(
        null=True,
        blank=True
    )


    # ========================================================
    # FFMPEG PROCESS
    # ========================================================

    ffmpeg_pid = models.PositiveIntegerField(
        null=True,
        blank=True
    )


    ffmpeg_command = models.TextField(
        blank=True
    )


    # ========================================================
    # ERROR INFORMATION
    # ========================================================

    last_error = models.TextField(
        blank=True
    )


    # ========================================================
    # META
    # ========================================================

    class Meta:

        ordering = [
            "-created_at"
        ]


    # ========================================================
    # STRING
    # ========================================================

    def __str__(self):

        return (
            f"{self.owner} - "
            f"{self.playlist.name}"
        )


# ============================================================
# STREAMING SESSION ITEM
# ============================================================

class StreamingSessionItem(TimeStampedModel):

    class Status(models.TextChoices):

        QUEUED = "queued", "Queued"

        PLAYING = "playing", "Playing"

        COMPLETED = "completed", "Completed"

        SKIPPED = "skipped", "Skipped"

        FAILED = "failed", "Failed"


    # ========================================================
    # ID
    # ========================================================

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )


    # ========================================================
    # SESSION
    # ========================================================

    session = models.ForeignKey(
        StreamingSession,
        on_delete=models.CASCADE,
        related_name="items",
    )


    # ========================================================
    # VIDEO
    # ========================================================

    video = models.ForeignKey(
        Video,
        on_delete=models.PROTECT,
        related_name="streaming_items",
    )


    # ========================================================
    # PLAYLIST POSITION
    # ========================================================

    position = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Position of this video inside the "
            "streaming session playlist."
        ),
    )


    # ========================================================
    # STATUS
    # ========================================================

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )


    # ========================================================
    # PLAYBACK START
    # ========================================================

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )


    # ========================================================
    # PLAYBACK END
    # ========================================================

    ended_at = models.DateTimeField(
        null=True,
        blank=True,
    )


    # ========================================================
    # PLAY COUNT
    # ========================================================
    #
    # ONE_BY_ONE:
    #
    # Video A → 1
    # Video B → 1
    # Video C → 1
    #
    # REPEAT:
    #
    # Video A → 2
    # Video B → 2
    # Video C → 2
    #
    # ========================================================

    play_count = models.PositiveIntegerField(
        default=0,
    )


    # ========================================================
    # ERROR
    # ========================================================

    error_message = models.TextField(
        blank=True,
    )


    # ========================================================
    # META
    # ========================================================

    class Meta:

        ordering = [
            "position",
        ]

        constraints = [

            models.UniqueConstraint(
                fields=[
                    "session",
                    "position",
                ],
                name="unique_session_item_position",
            ),

        ]

        indexes = [

            models.Index(
                fields=[
                    "session",
                    "status",
                ],
                name="ssi_status_idx",
            ),

            models.Index(
                fields=[
                    "session",
                    "position",
                ],
                name="ssi_position_idx",
            ),

        ]


    # ========================================================
    # STRING
    # ========================================================

    def __str__(self):

        return (
            f"{self.session} - "
            f"{self.position + 1} - "
            f"{self.video.title}"
        )

# ============================================================
# STREAMING LOG
# ============================================================

class StreamingLog(TimeStampedModel):

    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    session = models.ForeignKey(
        StreamingSession,
        on_delete=models.CASCADE,
        related_name="logs"
    )

    level = models.CharField(
        max_length=20,
        choices=Level.choices,
        default=Level.INFO
    )

    message = models.TextField()

    data = models.JSONField(
        default=dict,
        blank=True
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.level}: {self.message}"


# ============================================================
# STREAMING SERVER
# ============================================================

class StreamingServer(TimeStampedModel):

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        ERROR = "error", "Error"

    name = models.CharField(
        max_length=255
    )

    host = models.CharField(
        max_length=255
    )

    port = models.PositiveIntegerField(
        default=22
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OFFLINE
    )

    max_concurrent_streams = models.PositiveIntegerField(
        default=1
    )

    current_streams = models.PositiveIntegerField(
        default=0
    )

    cpu_usage = models.FloatField(
        default=0
    )

    memory_usage = models.FloatField(
        default=0
    )

    last_heartbeat = models.DateTimeField(
        null=True,
        blank=True
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name