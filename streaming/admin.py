from django.contrib import admin

from .models import (
    Video,
    Playlist,
    PlaylistVideo,
    YouTubeChannel,
    YouTubeStream,
    StreamingSession,
    StreamingSessionItem,
    StreamingLog,
    StreamingServer,
)


# ============================================================
# VIDEO
# ============================================================

@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):

    list_display = (
        "title",
        "owner",
        "status",
        "duration_display",
        "file_size_display",
        "width",
        "height",
        "is_public",
        "is_active",
        "created_at",
    )

    list_filter = (
        "status",
        "is_public",
        "is_active",
        "created_at",
    )

    search_fields = (
        "title",
        "description",
        "owner__username",
        "owner__email",
    )

    readonly_fields = (
        "id",
        "file_size",
        "duration_seconds",
        "width",
        "height",
        "frame_rate",
        "codec",
        "bitrate",
        "mime_type",
        "processing_started_at",
        "processing_completed_at",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "id",
                    "owner",
                    "title",
                    "description",
                    "video_file",
                    "thumbnail",
                )
            },
        ),
        (
            "Status",
            {
                "fields": (
                    "status",
                    "is_public",
                    "is_active",
                )
            },
        ),
        (
            "Video Information",
            {
                "fields": (
                    "duration_seconds",
                    "file_size",
                    "width",
                    "height",
                    "frame_rate",
                    "codec",
                    "bitrate",
                    "mime_type",
                )
            },
        ),
        (
            "Processing",
            {
                "fields": (
                    "processing_started_at",
                    "processing_completed_at",
                    "processing_error",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def duration_display(self, obj):
        if not obj.duration_seconds:
            return "—"

        hours = obj.duration_seconds // 3600
        minutes = (obj.duration_seconds % 3600) // 60
        seconds = obj.duration_seconds % 60

        if hours:
            return f"{hours}h {minutes}m {seconds}s"

        return f"{minutes}m {seconds}s"

    duration_display.short_description = "Duration"

    def file_size_display(self, obj):
        if not obj.file_size:
            return "—"

        size = obj.file_size

        if size < 1024:
            return f"{size} B"

        if size < 1024 ** 2:
            return f"{size / 1024:.2f} KB"

        if size < 1024 ** 3:
            return f"{size / (1024 ** 2):.2f} MB"

        return f"{size / (1024 ** 3):.2f} GB"

    file_size_display.short_description = "File Size"


# ============================================================
# PLAYLIST
# ============================================================

@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "owner",
        "play_mode",
        "video_count",
        "is_active",
        "created_at",
    )

    list_filter = (
        "play_mode",
        "is_active",
        "created_at",
    )

    search_fields = (
        "name",
        "description",
        "owner__username",
        "owner__email",
    )

    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Playlist Information",
            {
                "fields": (
                    "id",
                    "owner",
                    "name",
                    "description",
                    "play_mode",
                    "is_active",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def video_count(self, obj):
        return obj.items.count()

    video_count.short_description = "Videos"


# ============================================================
# PLAYLIST VIDEO
# ============================================================

@admin.register(PlaylistVideo)
class PlaylistVideoAdmin(admin.ModelAdmin):

    list_display = (
        "playlist",
        "video",
        "position",
        "is_enabled",
        "created_at",
    )

    list_filter = (
        "is_enabled",
        "playlist",
    )

    search_fields = (
        "playlist__name",
        "video__title",
    )

    list_editable = (
        "position",
        "is_enabled",
    )

    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
    )

    ordering = (
        "playlist",
        "position",
    )


# ============================================================
# YOUTUBE CHANNEL
# ============================================================

@admin.register(YouTubeChannel)
class YouTubeChannelAdmin(admin.ModelAdmin):

    list_display = (
        "channel_title",
        "channel_id",
        "owner",
        "status",
        "token_status",
        "last_connected_at",
    )

    list_filter = (
        "status",
        "last_connected_at",
    )

    search_fields = (
        "channel_title",
        "channel_id",
        "owner__username",
        "owner__email",
    )

    readonly_fields = (
        "id",
        "channel_id",
        "channel_title",
        "channel_url",
        "token_expires_at",
        "scopes",
        "status",
        "last_connected_at",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "YouTube Channel",
            {
                "fields": (
                    "id",
                    "owner",
                    "channel_id",
                    "channel_title",
                    "channel_url",
                    "status",
                )
            },
        ),
        (
            "OAuth Information",
            {
                "fields": (
                    "token_expires_at",
                    "scopes",
                ),
                "description": (
                    "OAuth access and refresh tokens are intentionally "
                    "hidden from the Django Admin."
                ),
            },
        ),
        (
            "Connection",
            {
                "fields": (
                    "last_connected_at",
                    "last_error",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def token_status(self, obj):
        if obj.token_expired:
            return "Expired"

        if obj.token_expires_at:
            return "Valid"

        return "Unknown"

    token_status.short_description = "OAuth Token"


# ============================================================
# YOUTUBE STREAM
# ============================================================

@admin.register(YouTubeStream)
class YouTubeStreamAdmin(admin.ModelAdmin):

    # ========================================================
    # LIST DISPLAY
    # ========================================================

    list_display = (
        "stream_name",
        "channel",
        "youtube_stream_id",
        "resolution",
        "frame_rate",
        "status",
        "created_at",
    )

    # ========================================================
    # LIST FILTERS
    # ========================================================

    list_filter = (
        "status",
        "resolution",
        "frame_rate",
        "created_at",
    )

    # ========================================================
    # SEARCH
    # ========================================================

    search_fields = (
        "stream_name",
        "youtube_stream_id",
        "channel__channel_title",
        "channel__channel_id",
    )

    # ========================================================
    # READONLY FIELDS
    # ========================================================

    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
    )

    # ========================================================
    # FIELDSETS
    # ========================================================

    fieldsets = (

        # ----------------------------------------------------
        # BASIC INFORMATION
        # ----------------------------------------------------

        (
            "Stream Information",
            {
                "fields": (
                    "id",
                    "channel",
                    "youtube_stream_id",
                    "stream_name",
                    "status",
                )
            },
        ),

        # ----------------------------------------------------
        # RTMP CONFIGURATION
        # ----------------------------------------------------

        (
            "RTMP Configuration",
            {
                "fields": (
                    "ingestion_address",
                    "ingestion_backup_address",
                    "resolution",
                    "frame_rate",
                ),
                "description": (
                    "Enter the YouTube RTMP/RTMPS ingestion address. "
                    "Example: rtmp://a.rtmp.youtube.com/live2"
                ),
            },
        ),

        # ----------------------------------------------------
        # STREAM KEY
        # ----------------------------------------------------

        (
            "Security",
            {
                "fields": (
                    "stream_key",
                ),
                "description": (
                    "The YouTube stream key is sensitive. "
                    "Never expose it through APIs, frontend pages, "
                    "logs, or public responses."
                ),
            },
        ),

        # ----------------------------------------------------
        # ERROR
        # ----------------------------------------------------

        (
            "Error Information",
            {
                "fields": (
                    "last_error",
                ),
            },
        ),

        # ----------------------------------------------------
        # TIMESTAMPS
        # ----------------------------------------------------

        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    # ========================================================
    # FORM CUSTOMIZATION
    # ========================================================

    def get_form(self, request, obj=None, **kwargs):

        form = super().get_form(
            request,
            obj,
            **kwargs
        )

        # ----------------------------------------------------
        # Hide stream key while typing
        # ----------------------------------------------------

        if "stream_key" in form.base_fields:

            form.base_fields[
                "stream_key"
            ].widget.input_type = "password"

        return form

    # ========================================================
    # SAVE MODEL
    # ========================================================

    def save_model(
        self,
        request,
        obj,
        form,
        change
    ):

        super().save_model(
            request,
            obj,
            form,
            change
        )

# ============================================================
# STREAMING SESSION
# ============================================================

@admin.register(StreamingSession)
class StreamingSessionAdmin(admin.ModelAdmin):

    # ========================================================
    # LIST DISPLAY
    # ========================================================

    list_display = (
        "title",
        "owner",
        "playlist",
        "mode",
        "status",
        "youtube_privacy",
        "current_item_position",
        "started_at",
        "ended_at",
    )


    # ========================================================
    # LIST FILTERS
    # ========================================================

    list_filter = (
        "mode",
        "status",
        "youtube_privacy",
        "auto_start",
        "auto_continue",
        "auto_stop",
        "scheduled_start",
        "started_at",
    )


    # ========================================================
    # SEARCH
    # ========================================================

    search_fields = (
        "title",
        "description",
        "owner__username",
        "owner__email",
        "playlist__name",
        "youtube_broadcast_id",
        "youtube_video_id",
    )


    # ========================================================
    # READONLY FIELDS
    # ========================================================

    readonly_fields = (
        "id",

        # YouTube generated information
        "youtube_broadcast_id",
        "youtube_video_id",
        "youtube_url",

        # Stream timing
        "started_at",
        "ended_at",

        # Current video timing
        "current_video_started_at",
        "current_video_ended_at",

        # FFmpeg
        "ffmpeg_pid",
        "ffmpeg_command",

        # Django timestamps
        "created_at",
        "updated_at",
    )


    # ========================================================
    # FIELDSETS
    # ========================================================

    fieldsets = (

        # ----------------------------------------------------
        # SESSION
        # ----------------------------------------------------

        (
            "Session",
            {
                "fields": (
                    "id",
                    "owner",
                    "title",
                    "description",
                    "playlist",
                    "mode",
                    "status",
                )
            },
        ),


        # ----------------------------------------------------
        # YOUTUBE RTMP
        # ----------------------------------------------------

        (
            "YouTube RTMP",
            {
                "fields": (
                    "youtube_rtmp_url",
                    "youtube_stream_key",
                    "youtube_privacy",
                    "youtube_broadcast_id",
                    "youtube_video_id",
                    "youtube_url",
                ),

                "description": (
                    "YouTube RTMP configuration. "
                    "Keep the stream key private."
                ),
            },
        ),


        # ----------------------------------------------------
        # AUTOMATION
        # ----------------------------------------------------

        (
            "Automation",
            {
                "fields": (
                    "auto_start",
                    "auto_continue",
                    "auto_stop",
                )
            },
        ),


        # ----------------------------------------------------
        # SCHEDULE
        # ----------------------------------------------------

        (
            "Schedule",
            {
                "fields": (
                    "scheduled_start",
                )
            },
        ),


        # ----------------------------------------------------
        # PLAYBACK
        # ----------------------------------------------------

        (
            "Playback",
            {
                "fields": (
                    "current_item_position",
                    "current_video_started_at",
                    "current_video_ended_at",
                )
            },
        ),


        # ----------------------------------------------------
        # FFMPEG
        # ----------------------------------------------------

        (
            "FFmpeg",
            {
                "fields": (
                    "ffmpeg_pid",
                    "ffmpeg_command",
                )
            },
        ),


        # ----------------------------------------------------
        # ERRORS
        # ----------------------------------------------------

        (
            "Errors",
            {
                "fields": (
                    "last_error",
                )
            },
        ),


        # ----------------------------------------------------
        # TIMESTAMPS
        # ----------------------------------------------------

        (
            "Timestamps",
            {
                "fields": (
                    "started_at",
                    "ended_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )


    # ========================================================
    # FORM FIELD CUSTOMIZATION
    # ========================================================

    def get_form(self, request, obj=None, **kwargs):

        form = super().get_form(
            request,
            obj,
            **kwargs
        )

        # Stream key is sensitive.
        # Show it as a password field in Django admin.

        if "youtube_stream_key" in form.base_fields:

            form.base_fields[
                "youtube_stream_key"
            ].widget.input_type = "password"


        return form


    # ========================================================
    # SAVE MODEL
    # ========================================================

    def save_model(
        self,
        request,
        obj,
        form,
        change
    ):

        # Automatically assign current user
        # when owner hasn't been selected.

        if not obj.owner_id:

            obj.owner = request.user

        super().save_model(
            request,
            obj,
            form,
            change
        )

# ============================================================
# STREAMING SESSION ITEM
# ============================================================

@admin.register(StreamingSessionItem)
class StreamingSessionItemAdmin(admin.ModelAdmin):

    list_display = (
        "session",
        "position",
        "video",
        "status",
        "play_count",
        "started_at",
        "ended_at",
    )

    list_filter = (
        "status",
        "session",
    )

    search_fields = (
        "session__title",
        "video__title",
    )

    list_editable = (
        "status",
    )

    readonly_fields = (
        "id",
        "started_at",
        "ended_at",
        "created_at",
        "updated_at",
    )

    ordering = (
        "session",
        "position",
    )


# ============================================================
# STREAMING LOG
# ============================================================

@admin.register(StreamingLog)
class StreamingLogAdmin(admin.ModelAdmin):

    list_display = (
        "session",
        "level",
        "short_message",
        "created_at",
    )

    list_filter = (
        "level",
        "created_at",
    )

    search_fields = (
        "message",
        "session__title",
    )

    readonly_fields = (
        "session",
        "level",
        "message",
        "data",
        "created_at",
        "updated_at",
    )

    def short_message(self, obj):
        if len(obj.message) > 80:
            return obj.message[:80] + "..."

        return obj.message

    short_message.short_description = "Message"


# ============================================================
# STREAMING SERVER
# ============================================================

@admin.register(StreamingServer)
class StreamingServerAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "host",
        "port",
        "status",
        "max_concurrent_streams",
        "current_streams",
        "cpu_usage",
        "memory_usage",
        "last_heartbeat",
    )

    list_filter = (
        "status",
    )

    search_fields = (
        "name",
        "host",
    )

    readonly_fields = (
        "id",
        "current_streams",
        "cpu_usage",
        "memory_usage",
        "last_heartbeat",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Server",
            {
                "fields": (
                    "id",
                    "name",
                    "host",
                    "port",
                    "status",
                )
            },
        ),
        (
            "Capacity",
            {
                "fields": (
                    "max_concurrent_streams",
                    "current_streams",
                )
            },
        ),
        (
            "Monitoring",
            {
                "fields": (
                    "cpu_usage",
                    "memory_usage",
                    "last_heartbeat",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )