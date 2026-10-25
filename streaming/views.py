import logging
from django.db.models.deletion import ProtectedError
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db.models import Count
import json
import subprocess
from django.db import transaction
from .models import (
    Video,
    Playlist,
    PlaylistVideo,
    StreamingSession,
    StreamingSessionItem,
    YouTubeChannel,
    YouTubeStream,
)


# ============================================================
# DASHBOARD
# ============================================================

@login_required
def dashboard(request):

    videos = Video.objects.filter(
        owner=request.user
    ).order_by("-created_at")

    playlists = Playlist.objects.filter(
        owner=request.user
    ).order_by("-created_at")

    sessions = StreamingSession.objects.filter(
        owner=request.user
    ).select_related(
        "playlist",
    ).order_by("-created_at")

    context = {
        "video_count": videos.count(),
        "playlist_count": playlists.count(),
        "session_count": sessions.count(),

        "recent_videos": videos[:5],
        "recent_playlists": playlists[:5],
        "recent_sessions": sessions[:5],
    }

    return render(
        request,
        "streaming/dashboard.html",
        context
    )


# ============================================================
# VIDEO LIST
# ============================================================

@login_required
def video_list(request):

    videos = (
        Video.objects
        .filter(
            owner=request.user
        )
        .order_by(
            "-created_at"
        )
    )

    return render(
        request,
        "streaming/video_list.html",
        {
            "videos": videos,
        }
    )


def get_video_metadata(video_path):
    """
    Extract video metadata using FFprobe.
    """

    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        video_path,
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)

    format_data = data.get("format", {})

    video_stream = None

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    duration = float(
        format_data.get("duration", 0) or 0
    )

    file_size = int(
        format_data.get("size", 0) or 0
    )

    width = 0
    height = 0
    frame_rate = 0
    codec = ""
    bitrate = 0

    if video_stream:

        width = int(
            video_stream.get("width", 0) or 0
        )

        height = int(
            video_stream.get("height", 0) or 0
        )

        codec = (
            video_stream.get("codec_name")
            or ""
        )

        bitrate_value = (
            video_stream.get("bit_rate")
            or format_data.get("bit_rate")
            or 0
        )

        try:
            bitrate = int(float(bitrate_value))
        except (ValueError, TypeError):
            bitrate = 0

        # Example:
        # r_frame_rate = "30000/1001"
        fps_value = (
            video_stream.get("r_frame_rate")
            or "0/1"
        )

        try:
            numerator, denominator = (
                fps_value.split("/")
            )

            if float(denominator) != 0:
                frame_rate = (
                    float(numerator)
                    / float(denominator)
                )

        except (
            ValueError,
            TypeError,
            ZeroDivisionError,
        ):
            frame_rate = 0

    return {
        "duration_seconds": int(
            round(duration)
        ),
        "file_size": file_size,
        "width": width,
        "height": height,
        "frame_rate": frame_rate,
        "codec": codec,
        "bitrate": bitrate,
    }
# ============================================================
# VIDEO UPLOAD
# ============================================================

from .tasks import process_uploaded_video

logger = logging.getLogger(__name__)


@login_required
def video_upload(request):

    if request.method != "POST":
        return render(
            request,
            "streaming/video_upload.html",
        )

    # --------------------------------------------------
    # Get form data
    # --------------------------------------------------
    title = request.POST.get("title", "").strip()
    description = request.POST.get("description", "").strip()

    video_file = request.FILES.get("video_file")
    thumbnail = request.FILES.get("thumbnail")

    is_public = request.POST.get("is_public") == "on"

    # --------------------------------------------------
    # Validate title
    # --------------------------------------------------
    if not title:
        messages.error(request, "Please enter a video title.")
        return redirect("streaming:video_upload")

    # --------------------------------------------------
    # Validate video file
    # --------------------------------------------------
    if not video_file:
        messages.error(request, "Please select a video file.")
        return redirect("streaming:video_upload")

    # Example application limit: 5 GiB.
    max_size = 5 * 1024**3

    if video_file.size > max_size:
        messages.error(
            request,
            "Video size cannot exceed 5 GiB.",
        )
        return redirect("streaming:video_upload")

    # --------------------------------------------------
    # Save video first
    # --------------------------------------------------
    try:
        video = Video.objects.create(
            owner=request.user,
            title=title,
            description=description,
            video_file=video_file,
            thumbnail=thumbnail,
            is_public=is_public,
            status=Video.Status.PROCESSING,
        )

    except Exception:
        logger.exception("Could not save uploaded video.")

        messages.error(
            request,
            "Could not save the uploaded video. Please try again.",
        )
        return redirect("streaming:video_upload")

    # --------------------------------------------------
    # Queue background processing
    # --------------------------------------------------
    try:
        process_uploaded_video.delay(video.pk)

    except Exception as exc:
        logger.exception(
            "Could not queue video processing for %s.",
            video.pk,
        )

        video.status = Video.Status.FAILED
        video.processing_error = (
            f"Could not queue Celery task: {exc}"
        )[:4000]

        video.save(
            update_fields=[
                "status",
                "processing_error",
                "updated_at",
            ]
        )

        messages.error(
            request,
            "The video was saved, but background processing "
            "could not be queued. Check that Redis and Celery "
            "are running.",
        )

        return redirect("streaming:video_list")

    # --------------------------------------------------
    # Redirect to detail page
    # --------------------------------------------------
    messages.success(
        request,
        "Video uploaded successfully. Background processing has started.",
    )

    return redirect(
        reverse(
            "streaming:video_detail",
            kwargs={"pk": video.pk},
        )
    )


# ============================================================
# VIDEO DETAIL
# ============================================================

@login_required
def video_detail(request, pk):

    video = get_object_or_404(
        Video,
        pk=pk,
        owner=request.user
    )

    # IMPORTANT:
    # Only playlists belonging to current user
    playlists = (
        Playlist.objects
        .filter(
            owner=request.user
        )
        .order_by("-created_at")
    )

    return render(
        request,
        "streaming/video_detail.html",
        {
            "video": video,
            "playlists": playlists,
        }
    )


# ============================================================
# VIDEO DELETE
# ============================================================

@login_required
def video_delete(request, pk):
    video = get_object_or_404(
        Video,
        pk=pk,
        owner=request.user,
    )

    if request.method != "POST":
        return render(
            request,
            "streaming/video_detail.html",
            {
                "video": video,
                "playlists": Playlist.objects.filter(
                    owner=request.user,
                ),
            },
        )

    # Save storage references before deleting the database record.
    stored_files = []

    for field_name in ("video_file", "thumbnail"):
        field = getattr(video, field_name)

        if field and field.name:
            stored_files.append(
                (field.storage, field.name)
            )

    try:
        # Permanently delete the database record.
        # ProtectedError prevents deletion if a protected
        # streaming-session item still references this video.
        video.delete()

    except ProtectedError:
        messages.error(
            request,
            "Permanent deletion failed: this video is referenced "
            "by a streaming session. Remove the protected reference "
            "before deleting the video."
        )
        return redirect("streaming:video_list")

    # Delete physical files only after successful database deletion.
    file_errors = []

    for storage, file_name in stored_files:
        try:
            storage.delete(file_name)
        except Exception:
            logger.exception(
                "Could not delete stored video file: %s",
                file_name,
            )
            file_errors.append(file_name)

    if file_errors:
        messages.warning(
            request,
            "The video record was permanently deleted, but some "
            "storage files could not be removed. Check the server logs."
        )
    else:
        messages.success(
            request,
            "Video and associated files permanently deleted."
        )

    return redirect("streaming:video_list")



# ============================================================
# PLAYLIST LIST
# ============================================================

@login_required
def playlist_list(request):

    playlists = (
        Playlist.objects
        .filter(
            owner=request.user,
            is_active=True,
        )
        .annotate(
            video_count=Count(
                "items",
                distinct=True,
            )
        )
        .prefetch_related(
            "items__video",
        )
        .order_by(
            "-created_at",
        )
    )

    return render(
        request,
        "streaming/playlist_list.html",
        {
            "playlists": playlists,
        },
    )




# ============================================================
# PLAYLIST CREATE
# ============================================================

@login_required
def playlist_create(request):

    # --------------------------------------------------------
    # GET USER'S READY VIDEOS
    # --------------------------------------------------------

    videos = (
        Video.objects
        .filter(
            owner=request.user,
            status=Video.Status.READY,
            is_active=True,
        )
        .order_by("-created_at")
    )

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    if request.method == "POST":

        name = request.POST.get(
            "name",
            ""
        ).strip()

        description = request.POST.get(
            "description",
            ""
        ).strip()

        play_mode = request.POST.get(
            "play_mode",
            Playlist.PlayMode.ONE_BY_ONE
        )

        # ----------------------------------------------------
        # SELECTED VIDEOS
        # ----------------------------------------------------

        video_ids = request.POST.getlist(
            "video_ids"
        )

        # ----------------------------------------------------
        # VALIDATE NAME
        # ----------------------------------------------------

        if not name:

            messages.error(
                request,
                "Playlist name is required."
            )

            return render(
                request,
                "streaming/playlist_form.html",
                {
                    "videos": videos,
                    "selected_video_ids": video_ids,
                }
            )

        # ----------------------------------------------------
        # VALIDATE PLAY MODE
        # ----------------------------------------------------

        valid_modes = [
            choice[0]
            for choice in Playlist.PlayMode.choices
        ]

        if play_mode not in valid_modes:

            play_mode = Playlist.PlayMode.ONE_BY_ONE

        # ----------------------------------------------------
        # VALIDATE VIDEOS
        # ----------------------------------------------------

        selected_videos = (
            Video.objects
            .filter(
                owner=request.user,
                pk__in=video_ids,
                status=Video.Status.READY,
                is_active=True,
            )
        )

        selected_video_map = {
            str(video.pk): video
            for video in selected_videos
        }

        # ----------------------------------------------------
        # CHECK AT LEAST ONE VIDEO
        # ----------------------------------------------------

        if not video_ids:

            messages.error(
                request,
                "Please select at least one video."
            )

            return render(
                request,
                "streaming/playlist_form.html",
                {
                    "videos": videos,
                    "selected_video_ids": video_ids,
                }
            )

        # ----------------------------------------------------
        # CREATE PLAYLIST
        # ----------------------------------------------------

        playlist = Playlist.objects.create(
            owner=request.user,
            name=name,
            description=description,
            play_mode=play_mode,
        )

        # ----------------------------------------------------
        # CREATE PLAYLIST VIDEOS
        # ----------------------------------------------------

        playlist_items = []

        position = 0

        for video_id in video_ids:

            video = selected_video_map.get(
                str(video_id)
            )

            if not video:
                continue

            playlist_items.append(
                PlaylistVideo(
                    playlist=playlist,
                    video=video,
                    position=position,
                    is_enabled=True,
                )
            )

            position += 1

        # ----------------------------------------------------
        # SAVE ITEMS
        # ----------------------------------------------------

        if playlist_items:

            PlaylistVideo.objects.bulk_create(
                playlist_items
            )

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        messages.success(
            request,
            "Playlist created successfully."
        )

        return redirect(
            "streaming:playlist_detail",
            pk=playlist.pk
        )

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    return render(
        request,
        "streaming/playlist_form.html",
        {
            "videos": videos,
            "selected_video_ids": [],
            "playlist": None,
        }
    )


# ============================================================
# PLAYLIST DETAIL
# ============================================================

@login_required
def playlist_detail(request, pk):

    # --------------------------------------------------------
    # GET PLAYLIST
    # --------------------------------------------------------

    playlist = get_object_or_404(
        Playlist.objects
        .prefetch_related(
            "items__video"
        ),
        pk=pk,
        owner=request.user
    )

    # --------------------------------------------------------
    # PLAYLIST ITEMS
    # --------------------------------------------------------

    playlist_items = list(
        playlist.items.all()
    )

    # --------------------------------------------------------
    # VIDEO COUNT
    # --------------------------------------------------------

    video_count = len(
        playlist_items
    )

    # --------------------------------------------------------
    # AVAILABLE VIDEOS
    # --------------------------------------------------------

    videos = (
        Video.objects
        .filter(
            owner=request.user,
            status=Video.Status.READY,
            is_active=True
        )
        .order_by("-created_at")
    )

    # --------------------------------------------------------
    # RENDER
    # --------------------------------------------------------

    return render(
        request,
        "streaming/playlist_detail.html",
        {
            "playlist": playlist,
            "videos": videos,
            "playlist_items": playlist_items,
            "video_count": video_count,
        }
    )

# ============================================================
# ADD VIDEO TO PLAYLIST
# ============================================================

@login_required
def playlist_add_video(request, pk):

    playlist = get_object_or_404(
        Playlist,
        pk=pk,
        owner=request.user
    )

    if request.method != "POST":

        return redirect(
            "streaming:playlist_detail",
            pk=playlist.pk
        )

    video_id = request.POST.get(
        "video_id"
    )

    if not video_id:

        messages.error(
            request,
            "Please select a video."
        )

        return redirect(
            "streaming:playlist_detail",
            pk=playlist.pk
        )

    video = get_object_or_404(
        Video,
        pk=video_id,
        owner=request.user,
        status=Video.Status.READY,
        is_active=True
    )

    # --------------------------------------------------------
    # CHECK DUPLICATE
    # --------------------------------------------------------

    if PlaylistVideo.objects.filter(
        playlist=playlist,
        video=video
    ).exists():

        messages.warning(
            request,
            "This video is already in the playlist."
        )

        return redirect(
            "streaming:playlist_detail",
            pk=playlist.pk
        )

    # --------------------------------------------------------
    # GET LAST POSITION
    # --------------------------------------------------------

    last_item = (
        playlist.items
        .order_by("-position")
        .first()
    )

    position = (
        last_item.position + 1
        if last_item
        else 0
    )

    # --------------------------------------------------------
    # ADD VIDEO
    # --------------------------------------------------------

    PlaylistVideo.objects.create(
        playlist=playlist,
        video=video,
        position=position,
        is_enabled=True
    )

    messages.success(
        request,
        f'"{video.title}" added to playlist.'
    )

    return redirect(
        "streaming:playlist_detail",
        pk=playlist.pk
    )


# ============================================================
# REMOVE VIDEO FROM PLAYLIST
# ============================================================

@login_required
def playlist_remove_video(
    request,
    playlist_pk,
    item_pk
):

    playlist = get_object_or_404(
        Playlist,
        pk=playlist_pk,
        owner=request.user
    )

    item = get_object_or_404(
        PlaylistVideo,
        pk=item_pk,
        playlist=playlist
    )

    if request.method == "POST":

        item.delete()

        messages.success(
            request,
            "Video removed from playlist."
        )

    return redirect(
        "streaming:playlist_detail",
        pk=playlist.pk
    )


# ============================================================
# PLAYLIST REORDER
# ============================================================

@login_required
@require_POST
def playlist_reorder(request, pk):

    playlist = get_object_or_404(
        Playlist,
        pk=pk,
        owner=request.user,
    )

    # --------------------------------------------------------
    # Get requested item
    # --------------------------------------------------------

    item_id = request.POST.get("item_id")
    new_position_raw = request.POST.get("position")

    if not item_id or not new_position_raw:
        messages.error(
            request,
            "Video and position are required.",
        )

        return redirect(
            "streaming:playlist_detail",
            pk=playlist.pk,
        )

    # --------------------------------------------------------
    # Validate position
    # --------------------------------------------------------

    try:
        requested_position = int(new_position_raw)
    except (ValueError, TypeError):

        messages.error(
            request,
            "Position must be a valid number.",
        )

        return redirect(
            "streaming:playlist_detail",
            pk=playlist.pk,
        )

    # User sees positions as:
    #
    # 1
    # 2
    # 3
    #
    # Database stores:
    #
    # 0
    # 1
    # 2
    #
    if requested_position < 1:
        messages.error(
            request,
            "Position must start from 1.",
        )

        return redirect(
            "streaming:playlist_detail",
            pk=playlist.pk,
        )

    # --------------------------------------------------------
    # Get playlist items
    # --------------------------------------------------------

    items = list(
        PlaylistVideo.objects
        .filter(
            playlist=playlist,
        )
        .select_related("video")
        .order_by(
            "position",
            "created_at",
        )
    )

    # --------------------------------------------------------
    # Find selected item
    # --------------------------------------------------------

    selected_item = next(
        (
            item
            for item in items
            if str(item.id) == str(item_id)
        ),
        None,
    )

    if not selected_item:

        messages.error(
            request,
            "Playlist video was not found.",
        )

        return redirect(
            "streaming:playlist_detail",
            pk=playlist.pk,
        )

    # --------------------------------------------------------
    # Calculate target index
    # --------------------------------------------------------

    target_index = requested_position - 1

    # If user enters a number greater than the playlist size,
    # simply move it to the last position.

    if target_index >= len(items):
        target_index = len(items) - 1

    # --------------------------------------------------------
    # Remove selected item from current position
    # --------------------------------------------------------

    items.remove(selected_item)

    # --------------------------------------------------------
    # Insert at requested position
    # --------------------------------------------------------

    items.insert(
        target_index,
        selected_item,
    )

    # --------------------------------------------------------
    # SAVE NEW ORDER
    # --------------------------------------------------------

    with transaction.atomic():

        # ----------------------------------------------------
        # STEP 1
        # Move everything temporarily to unique positions.
        #
        # This prevents UNIQUE constraint conflicts.
        # ----------------------------------------------------

        temporary_start = len(items) + 1000

        for index, item in enumerate(items):

            PlaylistVideo.objects.filter(
                pk=item.pk,
                playlist=playlist,
            ).update(
                position=temporary_start + index
            )

        # ----------------------------------------------------
        # STEP 2
        # Save final positions.
        # ----------------------------------------------------

        for index, item in enumerate(items):

            PlaylistVideo.objects.filter(
                pk=item.pk,
                playlist=playlist,
            ).update(
                position=index
            )

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    messages.success(
        request,
        f'"{selected_item.video.title}" moved to position '
        f"{target_index + 1}.",
    )

    return redirect(
        "streaming:playlist_detail",
        pk=playlist.pk,
    )




# ============================================================
# STREAMING SESSION LIST
# ============================================================

@login_required
def session_list(request):

    sessions = (
        StreamingSession.objects
        .filter(
            owner=request.user
        )
        .select_related(
            "playlist",
        )
        .order_by("-created_at")
    )

    # --------------------------------------------------------
    # SUMMARY COUNTS
    # --------------------------------------------------------

    running_sessions = sessions.filter(
        status__in=[
            StreamingSession.Status.STARTING,
            StreamingSession.Status.LIVE,
        ]
    ).count()

    scheduled_sessions = sessions.filter(
        scheduled_start__isnull=False,
        status=StreamingSession.Status.QUEUED
    ).count()

    failed_sessions = sessions.filter(
        status=StreamingSession.Status.FAILED
    ).count()

    return render(
        request,
        "streaming/session_list.html",
        {
            "sessions": sessions,

            "running_sessions": running_sessions,

            "scheduled_sessions": scheduled_sessions,

            "failed_sessions": failed_sessions,
        }
    )


# ============================================================
# CREATE STREAMING SESSION
# ============================================================

@login_required
def session_create(request):

    # --------------------------------------------------------
    # USER'S PLAYLISTS
    # --------------------------------------------------------

    playlists = (
        Playlist.objects
        .filter(
            owner=request.user,
            is_active=True
        )
        .prefetch_related(
            "items__video"
        )
        .order_by("-created_at")
    )

    # --------------------------------------------------------
    # USER'S YOUTUBE CHANNELS
    # --------------------------------------------------------

    youtube_channels = (
        YouTubeChannel.objects
        .filter(
            owner=request.user
        )
        .order_by("-created_at")
    )

    # --------------------------------------------------------
    # USER'S YOUTUBE STREAMS
    # --------------------------------------------------------

    youtube_streams = (
        YouTubeStream.objects
        .filter(
            channel__owner=request.user
        )
        .order_by("-created_at")
    )

    # ========================================================
    # POST
    # ========================================================

    if request.method == "POST":

        # ----------------------------------------------------
        # BASIC INFORMATION
        # ----------------------------------------------------

        playlist_id = request.POST.get("playlist")

        youtube_channel_id = request.POST.get(
            "youtube_channel"
        )

        youtube_stream_id = request.POST.get(
            "youtube_stream"
        )

        title = request.POST.get(
            "title",
            ""
        ).strip()

        description = request.POST.get(
            "description",
            ""
        ).strip()

        # ----------------------------------------------------
        # MANUAL RTMP CONFIGURATION
        # ----------------------------------------------------
        #
        # These values come directly from frontend.
        #
        # Example:
        # youtube_rtmp_url =
        # rtmp://a.rtmp.youtube.com/live2
        #
        # youtube_stream_key =
        # xxxx-xxxx-xxxx-xxxx
        #
        # ----------------------------------------------------

        youtube_rtmp_url = request.POST.get(
            "youtube_rtmp_url",
            ""
        ).strip()

        youtube_stream_key = request.POST.get(
            "youtube_stream_key",
            ""
        ).strip()

        # ----------------------------------------------------
        # MODE
        # ----------------------------------------------------

        mode = request.POST.get(
            "mode",
            StreamingSession.Mode.ONE_BY_ONE
        )

        # ----------------------------------------------------
        # AUTO START
        # ----------------------------------------------------
        #
        # Checkbox sends "on" when enabled and nothing when disabled.
        #
        auto_start = (
            request.POST.get("auto_start") == "on"
        )

        # ----------------------------------------------------
        # SCHEDULE
        # ----------------------------------------------------

        start_type = request.POST.get(
            "start_type",
            "now"
        )

        scheduled_start_value = request.POST.get(
            "scheduled_at"
        )

        # ====================================================
        # VALIDATE PLAYLIST
        # ====================================================

        if not playlist_id:

            messages.error(
                request,
                "Please select a playlist."
            )

            return render(
                request,
                "streaming/session_form.html",
                {
                    "playlists": playlists,
                    "youtube_channels": youtube_channels,
                    "youtube_streams": youtube_streams,
                }
            )

        playlist = get_object_or_404(
            Playlist,
            pk=playlist_id,
            owner=request.user,
            is_active=True
        )

        # ====================================================
        # YOUTUBE CHANNEL
        # ====================================================
        #
        # Channel is still optional if RTMP URL + KEY
        # are manually provided.
        #
        # ====================================================

        youtube_channel = None

        if youtube_channel_id:

            youtube_channel = get_object_or_404(
                YouTubeChannel,
                pk=youtube_channel_id,
                owner=request.user
            )

        # ====================================================
        # YOUTUBE STREAM
        # ====================================================

        youtube_stream = None

        if youtube_stream_id:

            if not youtube_channel:

                messages.error(
                    request,
                    "Please select a YouTube channel first."
                )

                return render(
                    request,
                    "streaming/session_form.html",
                    {
                        "playlists": playlists,
                        "youtube_channels": youtube_channels,
                        "youtube_streams": youtube_streams,
                    }
                )

            youtube_stream = get_object_or_404(
                YouTubeStream,
                pk=youtube_stream_id,
                channel=youtube_channel
            )

        # ====================================================
        # RTMP URL / STREAM KEY
        # ====================================================
        #
        # Priority:
        #
        # 1. Frontend supplied URL + key
        # 2. Selected YouTubeStream URL + key
        # 3. Default YouTube RTMP URL
        #
        # ====================================================

        if not youtube_rtmp_url and youtube_stream:

            youtube_rtmp_url = (
                youtube_stream.ingestion_address
                or "rtmp://a.rtmp.youtube.com/live2"
            )

        if not youtube_stream_key and youtube_stream:

            youtube_stream_key = (
                youtube_stream.stream_key
            )

        # ----------------------------------------------------
        # DEFAULT RTMP URL
        # ----------------------------------------------------

        if not youtube_rtmp_url:

            youtube_rtmp_url = (
                "rtmp://a.rtmp.youtube.com/live2"
            )

        # ----------------------------------------------------
        # STREAM KEY REQUIRED
        # ----------------------------------------------------

        if not youtube_stream_key:

            messages.error(
                request,
                "Please enter a YouTube stream key."
            )

            return render(
                request,
                "streaming/session_form.html",
                {
                    "playlists": playlists,
                    "youtube_channels": youtube_channels,
                    "youtube_streams": youtube_streams,
                }
            )

        # ====================================================
        # VALIDATE MODE
        # ====================================================

        valid_modes = [
            StreamingSession.Mode.ONE_BY_ONE,
            StreamingSession.Mode.REPEAT,
        ]

        if mode not in valid_modes:

            mode = (
                StreamingSession.Mode.ONE_BY_ONE
            )

        # ====================================================
        # GET PLAYLIST ITEMS
        # ====================================================

        playlist_items = (
            playlist.items
            .filter(
                is_enabled=True,
                video__status=Video.Status.READY,
                video__is_active=True
            )
            .select_related(
                "video"
            )
            .order_by(
                "position"
            )
        )

        # ====================================================
        # CHECK PLAYLIST
        # ====================================================

        if not playlist_items.exists():

            messages.error(
                request,
                "The selected playlist has no ready videos."
            )

            return render(
                request,
                "streaming/session_form.html",
                {
                    "playlists": playlists,
                    "youtube_channels": youtube_channels,
                    "youtube_streams": youtube_streams,
                }
            )

        # ====================================================
        # CREATE SESSION
        # ====================================================

        session = StreamingSession.objects.create(

            owner=request.user,

            # ------------------------------------------------
            # FRONTEND / YOUTUBE RTMP
            # ------------------------------------------------

            youtube_rtmp_url=youtube_rtmp_url,

            youtube_stream_key=youtube_stream_key,

            # ------------------------------------------------
            # PLAYLIST
            # ------------------------------------------------

            playlist=playlist,

            mode=mode,

            auto_start=auto_start,

            status=StreamingSession.Status.DRAFT,

            title=(
                title
                or playlist.name
            ),

            description=description,
        )

        # ====================================================
        # SCHEDULE
        # ====================================================

        if (
            start_type == "scheduled"
            and scheduled_start_value
        ):

            parsed_datetime = parse_datetime(
                scheduled_start_value
            )

            if parsed_datetime:

                # Handle naive datetime
                if timezone.is_naive(
                    parsed_datetime
                ):
                    parsed_datetime = (
                        timezone.make_aware(
                            parsed_datetime
                        )
                    )

                session.scheduled_start = (
                    parsed_datetime
                )

                session.status = (
                    StreamingSession.Status.QUEUED
                )

                session.save(
                    update_fields=[
                        "scheduled_start",
                        "status",
                        "updated_at",
                    ]
                )

        # ====================================================
        # CREATE SESSION QUEUE
        # ====================================================

        StreamingSessionItem.objects.bulk_create(

            [
                StreamingSessionItem(
                    session=session,
                    video=item.video,
                    position=index,
                    status=(
                        StreamingSessionItem
                        .Status
                        .QUEUED
                    ),
                )

                for index, item
                in enumerate(
                    playlist_items
                )
            ]
        )

        # ====================================================
        # SUCCESS
        # ====================================================

        messages.success(
            request,
            "Streaming session created successfully."
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk
        )

    # ========================================================
    # GET
    # ========================================================

    return render(
        request,
        "streaming/session_form.html",
        {
            "playlists": playlists,
            "youtube_channels": youtube_channels,
            "youtube_streams": youtube_streams,
        }
    )


# ============================================================
# STREAMING SESSION DETAIL
# ============================================================

@login_required
def session_detail(request, pk):

    session = get_object_or_404(
        StreamingSession.objects.select_related(
            "playlist",
        ),
        pk=pk,
        owner=request.user
    )

    items = (
        session.items
        .select_related(
            "video"
        )
        .order_by(
            "position"
        )
    )

    return render(
        request,
        "streaming/session_detail.html",
        {
            "session": session,
            "items": items,
        }
    )


# ============================================================
# START STREAMING SESSION
# ============================================================

@login_required
@require_POST
def session_start(request, pk):

    # --------------------------------------------------------
    # GET SESSION
    # --------------------------------------------------------

    session = get_object_or_404(
        StreamingSession.objects.select_related(
            "playlist",
        ),
        pk=pk,
        owner=request.user,
    )

    # --------------------------------------------------------
    # CHECK STATUS
    # --------------------------------------------------------

    if session.status in [
        StreamingSession.Status.STARTING,
        StreamingSession.Status.LIVE,
    ]:
        messages.warning(
            request,
            "This streaming session is already running."
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk,
        )

    # --------------------------------------------------------
    # CHECK YOUTUBE RTMP CONFIGURATION
    # --------------------------------------------------------

    if not session.youtube_rtmp_url:
        messages.error(
            request,
            "YouTube RTMP URL is not configured."
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk,
        )

    if not session.youtube_stream_key:
        messages.error(
            request,
            "YouTube stream key is not configured."
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk,
        )

    # --------------------------------------------------------
    # GET FIRST ENABLED PLAYLIST VIDEO
    # --------------------------------------------------------

    playlist_item = (
        session.playlist.items
        .filter(
            is_enabled=True,
        )
        .select_related(
            "video",
        )
        .order_by(
            "position",
        )
        .first()
    )

    # --------------------------------------------------------
    # CHECK PLAYLIST
    # --------------------------------------------------------

    if not playlist_item:
        messages.error(
            request,
            "The selected playlist has no videos."
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk,
        )

    video = playlist_item.video

    # --------------------------------------------------------
    # CHECK VIDEO
    # --------------------------------------------------------

    if not video:
        messages.error(
            request,
            "Playlist item does not contain a video."
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk,
        )

    # --------------------------------------------------------
    # CHECK VIDEO FILE
    # --------------------------------------------------------

    if not video.video_file:
        messages.error(
            request,
            f'Video "{video.title}" does not have a video file.'
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk,
        )

    # --------------------------------------------------------
    # GET LOCAL FILE PATH
    # --------------------------------------------------------

    try:

        video_path = video.video_file.path

    except Exception as exc:

        messages.error(
            request,
            f"Unable to access video file: {exc}"
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk,
        )

    # --------------------------------------------------------
    # CHECK FILE EXISTS
    # --------------------------------------------------------

    import os

    if not os.path.isfile(video_path):

        messages.error(
            request,
            f"Video file does not exist: {video_path}"
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk,
        )

    # --------------------------------------------------------
    # REBUILD SESSION QUEUE
    # --------------------------------------------------------

    session.items.all().delete()

    # --------------------------------------------------------
    # CREATE CURRENT SESSION ITEM
    # --------------------------------------------------------

    StreamingSessionItem.objects.create(
        session=session,
        video=video,
        position=0,
        status=StreamingSessionItem.Status.QUEUED,
    )

    # --------------------------------------------------------
    # UPDATE SESSION
    # --------------------------------------------------------

    session.current_item_position = 0

    session.status = StreamingSession.Status.STARTING

    session.last_error = ""

    session.save(
        update_fields=[
            "current_item_position",
            "status",
            "last_error",
            "updated_at",
        ]
    )

    # --------------------------------------------------------
    # START CELERY TASK
    # --------------------------------------------------------

    try:

        from .tasks import start_streaming_session

        task = start_streaming_session.delay(
            str(session.pk)
        )

    except Exception as exc:

        session.status = StreamingSession.Status.FAILED

        session.last_error = str(exc)

        session.save(
            update_fields=[
                "status",
                "last_error",
                "updated_at",
            ]
        )

        messages.error(
            request,
            f"Failed to start streaming task: {exc}"
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk,
        )

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    messages.success(
        request,
        f'Streaming started with video "{video.title}".'
    )

    return redirect(
        "streaming:session_detail",
        pk=session.pk,
    )

# ============================================================
# STOP STREAM
# ============================================================

@login_required
def session_stop(request, pk):

    session = get_object_or_404(
        StreamingSession,
        pk=pk,
        owner=request.user
    )

    if request.method != "POST":

        return redirect(
            "streaming:session_detail",
            pk=session.pk
        )

    # --------------------------------------------------------
    # CHECK STATUS
    # --------------------------------------------------------

    if session.status not in [
        StreamingSession.Status.LIVE,
        StreamingSession.Status.STARTING,
        StreamingSession.Status.QUEUED,
    ]:

        messages.warning(
            request,
            "This stream is not currently running."
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk
        )

    # --------------------------------------------------------
    # MARK STOPPING
    # --------------------------------------------------------

    session.status = (
        StreamingSession.Status.STOPPING
    )

    session.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    # --------------------------------------------------------
    # CELERY STOP TASK
    # --------------------------------------------------------

    try:

        from .tasks import (
            stop_streaming_session
        )

        stop_streaming_session.delay(
            str(session.id)
        )

    except ImportError:

        messages.warning(
            request,
            "Stop request recorded, but the streaming worker is not configured yet."
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk
        )

    except Exception as exc:

        messages.error(
            request,
            f"Could not stop stream: {exc}"
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk
        )

    messages.success(
        request,
        "Stop request sent."
    )

    return redirect(
        "streaming:session_detail",
        pk=session.pk
    )


# ============================================================
# SKIP CURRENT VIDEO
# ============================================================

@login_required
def session_skip(request, pk):

    session = get_object_or_404(
        StreamingSession,
        pk=pk,
        owner=request.user
    )

    if request.method != "POST":

        return redirect(
            "streaming:session_detail",
            pk=session.pk
        )

    # --------------------------------------------------------
    # CHECK LIVE
    # --------------------------------------------------------

    if session.status != StreamingSession.Status.LIVE:

        messages.error(
            request,
            "Stream is not currently live."
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk
        )

    # --------------------------------------------------------
    # QUEUE SKIP TASK
    # --------------------------------------------------------

    try:

        from .tasks import (
            skip_streaming_video
        )

        skip_streaming_video.delay(
            str(session.id)
        )

    except ImportError:

        messages.warning(
            request,
            "Skip request recorded, but the streaming worker is not configured yet."
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk
        )

    except Exception as exc:

        messages.error(
            request,
            f"Could not skip video: {exc}"
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk
        )

    messages.success(
        request,
        "Skip request sent."
    )

    return redirect(
        "streaming:session_detail",
        pk=session.pk
    )

# ============================================================
# PLAYLIST EDIT
# ============================================================

@login_required
def playlist_edit(request, pk):

    # --------------------------------------------------------
    # GET PLAYLIST
    # --------------------------------------------------------

    playlist = get_object_or_404(
        Playlist,
        pk=pk,
        owner=request.user,
    )

    # --------------------------------------------------------
    # GET USER'S READY VIDEOS
    # --------------------------------------------------------

    videos = (
        Video.objects
        .filter(
            owner=request.user,
            status=Video.Status.READY,
            is_active=True,
        )
        .order_by("-created_at")
    )

    # --------------------------------------------------------
    # GET CURRENT PLAYLIST ITEMS
    # --------------------------------------------------------

    playlist_items = list(
        PlaylistVideo.objects
        .filter(
            playlist=playlist,
        )
        .select_related("video")
        .order_by("position")
    )

    # --------------------------------------------------------
    # CURRENT SELECTED VIDEO IDS
    # --------------------------------------------------------

    current_video_ids = [
        str(item.video_id)
        for item in playlist_items
    ]

    # ========================================================
    # POST
    # ========================================================

    if request.method == "POST":

        # ----------------------------------------------------
        # BASIC INFORMATION
        # ----------------------------------------------------

        name = request.POST.get(
            "name",
            "",
        ).strip()

        description = request.POST.get(
            "description",
            "",
        ).strip()

        # ----------------------------------------------------
        # IMPORTANT:
        # Playlist field is play_mode
        # NOT mode
        # ----------------------------------------------------

        play_mode = request.POST.get(
            "play_mode",
            Playlist.PlayMode.ONE_BY_ONE,
        ).strip()

        # ----------------------------------------------------
        # ACTIVE STATUS
        # ----------------------------------------------------

        is_active = (
            request.POST.get("is_active") == "on"
        )

        # ----------------------------------------------------
        # SELECTED VIDEOS
        #
        # getlist() preserves the order submitted
        # by the HTML form.
        # ----------------------------------------------------

        video_ids = request.POST.getlist(
            "video_ids"
        )

        # ====================================================
        # VALIDATE NAME
        # ====================================================

        if not name:

            messages.error(
                request,
                "Playlist name is required.",
            )

            return render(
                request,
                "streaming/playlist_form.html",
                {
                    "playlist": playlist,
                    "videos": videos,
                    "playlist_items": playlist_items,
                    "selected_video_ids": video_ids,
                    "is_edit": True,
                },
            )

        # ====================================================
        # VALIDATE PLAY MODE
        # ====================================================

        valid_modes = {
            choice[0]
            for choice in Playlist.PlayMode.choices
        }

        if play_mode not in valid_modes:

            messages.error(
                request,
                "Invalid playlist play mode.",
            )

            return render(
                request,
                "streaming/playlist_form.html",
                {
                    "playlist": playlist,
                    "videos": videos,
                    "playlist_items": playlist_items,
                    "selected_video_ids": video_ids,
                    "is_edit": True,
                },
            )

        # ====================================================
        # REQUIRE AT LEAST ONE VIDEO
        # ====================================================

        if not video_ids:

            messages.error(
                request,
                "Please select at least one video.",
            )

            return render(
                request,
                "streaming/playlist_form.html",
                {
                    "playlist": playlist,
                    "videos": videos,
                    "playlist_items": playlist_items,
                    "selected_video_ids": [],
                    "is_edit": True,
                },
            )

        # ====================================================
        # GET VALID VIDEOS
        # ====================================================

        selected_videos = (
            Video.objects
            .filter(
                owner=request.user,
                pk__in=video_ids,
                status=Video.Status.READY,
                is_active=True,
            )
        )

        selected_video_map = {
            str(video.pk): video
            for video in selected_videos
        }

        # ====================================================
        # PRESERVE USER'S VIDEO ORDER
        # ====================================================

        valid_video_ids = []

        seen_video_ids = set()

        for video_id in video_ids:

            video_id = str(video_id)

            if (
                video_id in selected_video_map
                and video_id not in seen_video_ids
            ):

                valid_video_ids.append(
                    video_id
                )

                seen_video_ids.add(
                    video_id
                )

        # ====================================================
        # CHECK VALID VIDEOS
        # ====================================================

        if not valid_video_ids:

            messages.error(
                request,
                "No valid videos were selected.",
            )

            return render(
                request,
                "streaming/playlist_form.html",
                {
                    "playlist": playlist,
                    "videos": videos,
                    "playlist_items": playlist_items,
                    "selected_video_ids": video_ids,
                    "is_edit": True,
                },
            )

        # ====================================================
        # UPDATE EVERYTHING ATOMICALLY
        # ====================================================

        with transaction.atomic():

            # ------------------------------------------------
            # UPDATE PLAYLIST
            # ------------------------------------------------

            playlist.name = name

            playlist.description = description

            # IMPORTANT
            playlist.play_mode = play_mode

            playlist.is_active = is_active

            playlist.save()

            # ------------------------------------------------
            # DELETE OLD PLAYLIST ITEMS
            # ------------------------------------------------

            PlaylistVideo.objects.filter(
                playlist=playlist,
            ).delete()

            # ------------------------------------------------
            # CREATE NEW PLAYLIST ITEMS
            #
            # position follows the exact submitted order.
            # ------------------------------------------------

            new_items = []

            for position, video_id in enumerate(
                valid_video_ids
            ):

                video = selected_video_map.get(
                    video_id
                )

                if not video:
                    continue

                new_items.append(
                    PlaylistVideo(
                        playlist=playlist,
                        video=video,
                        position=position,
                        is_enabled=True,
                    )
                )

            # ------------------------------------------------
            # SAVE NEW ITEMS
            # ------------------------------------------------

            if new_items:

                PlaylistVideo.objects.bulk_create(
                    new_items
                )

        # ====================================================
        # SUCCESS
        # ====================================================

        messages.success(
            request,
            (
                f'Playlist "{playlist.name}" '
                "updated successfully."
            ),
        )

        return redirect(
            "streaming:playlist_detail",
            pk=playlist.pk,
        )

    # ========================================================
    # GET
    # ========================================================

    return render(
        request,
        "streaming/playlist_form.html",
        {
            "playlist": playlist,
            "videos": videos,
            "playlist_items": playlist_items,
            "selected_video_ids": current_video_ids,
            "is_edit": True,
        },
    )




# ============================================================
# PLAYLIST DELETE
# ============================================================


@login_required
@require_POST
def playlist_delete(request, pk):
    playlist = get_object_or_404(
        Playlist,
        pk=pk,
        owner=request.user,
    )

    playlist_name = playlist.name

    try:
        # Permanently delete the playlist and its PlaylistVideo
        # relationship records. The actual videos remain untouched.
        playlist.delete()

    except ProtectedError:
        messages.error(
            request,
            f'Playlist "{playlist_name}" cannot be permanently '
            "deleted because a streaming session references it. "
            "Remove that protected reference first."
        )
        return redirect("streaming:playlist_list")

    messages.success(
        request,
        f'Playlist "{playlist_name}" permanently deleted.'
    )

    return redirect("streaming:playlist_list")



# ============================================================
# EDIT STREAMING SESSION
# ============================================================

@login_required
def session_edit(request, pk):

    session = get_object_or_404(
        StreamingSession,
        pk=pk,
        owner=request.user,
    )

    # --------------------------------------------------------
    # Don't allow editing an active stream
    # --------------------------------------------------------

    if session.status in [
        StreamingSession.Status.STARTING,
        StreamingSession.Status.LIVE,
        StreamingSession.Status.STOPPING,
    ]:
        messages.error(
            request,
            "You cannot edit a stream while it is running.",
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk,
        )

    # --------------------------------------------------------
    # Get user's playlists
    # --------------------------------------------------------

    playlists = (
        Playlist.objects
        .filter(
            owner=request.user,
            is_active=True,
        )
        .prefetch_related(
            "items__video",
        )
        .order_by("-created_at")
    )

    # --------------------------------------------------------
    # Get user's YouTube channels
    # --------------------------------------------------------

    youtube_channels = (
        YouTubeChannel.objects
        .filter(
            owner=request.user,
        )
        .order_by("-created_at")
    )

    # --------------------------------------------------------
    # Get user's YouTube streams
    # --------------------------------------------------------

    youtube_streams = (
        YouTubeStream.objects
        .filter(
            channel__owner=request.user,
        )
        .order_by("-created_at")
    )

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    if request.method == "POST":

        title = request.POST.get(
            "title",
            "",
        ).strip()

        description = request.POST.get(
            "description",
            "",
        ).strip()

        playlist_id = request.POST.get(
            "playlist",
        )

        mode = request.POST.get(
            "mode",
            StreamingSession.Mode.ONE_BY_ONE,
        )

        start_type = request.POST.get(
            "start_type",
            "now",
        )

        scheduled_start_value = request.POST.get(
            "scheduled_at",
        )

        # ----------------------------------------------------
        # Validate title
        # ----------------------------------------------------

        if not title:

            messages.error(
                request,
                "Stream title is required.",
            )

            return render(
                request,
                "streaming/session_form.html",
                {
                    "session": session,
                    "playlists": playlists,
                    "youtube_channels": youtube_channels,
                    "youtube_streams": youtube_streams,
                    "is_edit": True,
                },
            )

        # ----------------------------------------------------
        # Validate playlist
        # ----------------------------------------------------

        if not playlist_id:

            messages.error(
                request,
                "Please select a playlist.",
            )

            return render(
                request,
                "streaming/session_form.html",
                {
                    "session": session,
                    "playlists": playlists,
                    "youtube_channels": youtube_channels,
                    "youtube_streams": youtube_streams,
                    "is_edit": True,
                },
            )

        playlist = get_object_or_404(
            Playlist,
            pk=playlist_id,
            owner=request.user,
            is_active=True,
        )

        # ----------------------------------------------------
        # Validate mode
        # ----------------------------------------------------

        valid_modes = [
            StreamingSession.Mode.ONE_BY_ONE,
            StreamingSession.Mode.REPEAT,
        ]

        if mode not in valid_modes:

            mode = StreamingSession.Mode.ONE_BY_ONE

        # ----------------------------------------------------
        # Check playlist videos
        # ----------------------------------------------------

        playlist_items = (
            playlist.items
            .filter(
                is_enabled=True,
                video__status=Video.Status.READY,
                video__is_active=True,
            )
            .select_related(
                "video",
            )
            .order_by(
                "position",
            )
        )

        if not playlist_items.exists():

            messages.error(
                request,
                "The selected playlist has no ready videos.",
            )

            return render(
                request,
                "streaming/session_form.html",
                {
                    "session": session,
                    "playlists": playlists,
                    "youtube_channels": youtube_channels,
                    "youtube_streams": youtube_streams,
                    "is_edit": True,
                },
            )

        # ----------------------------------------------------
        # Update session
        # ----------------------------------------------------

        session.title = title

        session.description = description

        session.playlist = playlist

        session.mode = mode

        # ----------------------------------------------------
        # Schedule
        # ----------------------------------------------------

        if (
            start_type == "scheduled"
            and scheduled_start_value
        ):

            parsed_datetime = parse_datetime(
                scheduled_start_value,
            )

            if parsed_datetime:

                session.scheduled_start = (
                    parsed_datetime
                )

                session.status = (
                    StreamingSession.Status.QUEUED
                )

        else:

            session.scheduled_start = None

            if session.status == StreamingSession.Status.QUEUED:
                session.status = (
                    StreamingSession.Status.DRAFT
                )

        session.save()

        # ----------------------------------------------------
        # Rebuild session queue
        # ----------------------------------------------------

        session.items.all().delete()

        StreamingSessionItem.objects.bulk_create(
            [
                StreamingSessionItem(
                    session=session,
                    video=item.video,
                    position=index,
                    status=(
                        StreamingSessionItem
                        .Status
                        .QUEUED
                    ),
                )
                for index, item
                in enumerate(
                    playlist_items
                )
            ]
        )

        messages.success(
            request,
            "Streaming session updated successfully.",
        )

        return redirect(
            "streaming:session_detail",
            pk=session.pk,
        )

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    return render(
        request,
        "streaming/session_form.html",
        {
            "session": session,
            "playlists": playlists,
            "youtube_channels": youtube_channels,
            "youtube_streams": youtube_streams,
            "is_edit": True,
        },
    )