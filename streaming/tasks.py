# streaming/tasks.py

import json
import logging
import os
import queue
import signal
import subprocess
import threading
import time
import uuid

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.core.files import File
from django.utils import timezone

from .models import (
    StreamingLog,
    StreamingSession,
    StreamingSessionItem,
)


logger = logging.getLogger(__name__)

try:
    import psutil  # Optional: enables per-FFmpeg CPU/RAM measurements.
except ImportError:  # Keep streaming functional without psutil installed.
    psutil = None


# ============================================================
# FFMPEG CONFIGURATION
# ============================================================

FFMPEG_BINARY = getattr(settings, "FFMPEG_BINARY", "ffmpeg")
FFPROBE_BINARY = getattr(settings, "FFPROBE_BINARY", "ffprobe")


# ============================================================
# CONSTANTS
# ============================================================

FFMPEG_STARTUP_WAIT = 2.0

PROCESS_CHECK_INTERVAL = 0.5

MAX_FFMPEG_LOG_LINES = 100

MAX_ERROR_LENGTH = 10000
RESOURCE_LOG_INTERVAL = 30.0
TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080
TARGET_FPS = 30
TARGET_VIDEO_BITRATE = getattr(settings, "YOUTUBE_VIDEO_BITRATE", "6000k")
TARGET_VIDEO_MAXRATE = getattr(settings, "YOUTUBE_VIDEO_MAXRATE", "6800k")
TARGET_VIDEO_BUFSIZE = getattr(settings, "YOUTUBE_VIDEO_BUFSIZE", "12000k")
TARGET_AUDIO_BITRATE = getattr(settings, "YOUTUBE_AUDIO_BITRATE", "128k")
TARGET_GOP_SECONDS = max(1, int(getattr(settings, "YOUTUBE_GOP_SECONDS", 2)))
TARGET_GOP_FRAMES = TARGET_FPS * TARGET_GOP_SECONDS
STREAM_IO_TIMEOUT_US = max(1, int(getattr(settings, "STREAM_IO_TIMEOUT_SECONDS", 15))) * 1_000_000


def get_ffmpeg_thread_count():
    """Return a conservative encoder thread limit to avoid saturating the VPS CPU.

    Override with FFMPEG_VIDEO_THREADS in Django settings. Stream-copy mode does not
    encode video and therefore does not benefit from increasing this value.
    """
    configured = getattr(settings, "FFMPEG_VIDEO_THREADS", None)
    if configured is not None:
        try:
            return max(1, min(32, int(configured)))
        except (TypeError, ValueError):
            logger.warning("Invalid FFMPEG_VIDEO_THREADS=%r; using automatic safe default", configured)

    cpu_count = os.cpu_count() or 2
    return max(1, min(4, cpu_count // 2 or 1))


def probe_media(path):
    """Return FFprobe metadata for a media file without loading the video into RAM."""
    result = subprocess.run(
        [
            FFPROBE_BINARY,
            "-v", "error",
            "-show_entries",
            "format=duration,size,format_name,bit_rate:stream=index,codec_type,codec_name,width,height,pix_fmt,r_frame_rate,avg_frame_rate,sample_rate,channels,profile",
            "-of", "json",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=600,
    )
    return json.loads(result.stdout)


def _parse_frame_rate(value):
    try:
        numerator, denominator = str(value or "0/1").split("/", 1)
        denominator = float(denominator)
        return float(numerator) / denominator if denominator else 0.0
    except (ValueError, TypeError, ZeroDivisionError):
        return 0.0


def get_media_streams(metadata):
    streams = metadata.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return video_stream, audio_stream


def is_stream_copy_compatible(metadata):
    """Check that a normalized file can be sent to RTMP/FLV without re-encoding.

    Compatibility is intentionally strict: H.264 1920x1080 yuv420p at about 30 fps,
    plus AAC audio. Older/unusual uploads fall back to the transcoding path.
    """
    video_stream, audio_stream = get_media_streams(metadata)
    if not video_stream or not audio_stream:
        return False
    fps = _parse_frame_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
    return (
        video_stream.get("codec_name") == "h264"
        and int(video_stream.get("width") or 0) == TARGET_WIDTH
        and int(video_stream.get("height") or 0) == TARGET_HEIGHT
        and video_stream.get("pix_fmt") == "yuv420p"
        and 29.0 <= fps <= 31.0
        and audio_stream.get("codec_name") == "aac"
        and int(audio_stream.get("sample_rate") or 0) in (44100, 48000)
    )


def build_stream_command(video_path, rtmp_url, duration_seconds, metadata=None):
    """Build a YouTube-friendly real-time FFmpeg command.

    Always transcode the outgoing stream by default. Stream-copy mode cannot
    enforce the target bitrate or keyframe interval, which caused the YouTube
    ingestion warnings reported by this application. If an operator explicitly
    disables YOUTUBE_FORCE_TRANSCODE, they accept responsibility for source GOP
    and bitrate compliance.
    """
    metadata = metadata or probe_media(video_path)
    duration = str(max(1, int(float(duration_seconds or 0))))
    force_transcode = getattr(settings, "YOUTUBE_FORCE_TRANSCODE", True)

    if not force_transcode and is_stream_copy_compatible(metadata):
        # Opt-in only: this path cannot rewrite keyframes or clamp bitrate.
        return [
            FFMPEG_BINARY, "-hide_banner", "-nostdin", "-loglevel", "warning",
            "-rw_timeout", str(STREAM_IO_TIMEOUT_US),
            "-re", "-i", video_path,
            "-t", duration,
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c", "copy",
            "-rw_timeout", str(STREAM_IO_TIMEOUT_US),
            "-f", "flv", rtmp_url,
        ], "copy_opt_in"

    # Always normalize output encoding so GOP, bitrate, FPS, pixel format, and
    # audio format are controlled for YouTube. This also protects older files
    # that were previously accepted by the looser stream-copy compatibility test.
    _, audio_stream = get_media_streams(metadata)
    command = [
        FFMPEG_BINARY, "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-rw_timeout", str(STREAM_IO_TIMEOUT_US),
        "-re", "-i", video_path,
    ]
    if audio_stream is None:
        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        audio_map = ["-map", "1:a:0"]
    else:
        audio_map = ["-map", "0:a:0?"]

    command += [
        "-t", duration,
        "-map", "0:v:0", *audio_map,
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", getattr(settings, "YOUTUBE_X264_PRESET", "veryfast"),
        "-pix_fmt", "yuv420p", "-r", str(TARGET_FPS),
        "-b:v", TARGET_VIDEO_BITRATE,
        "-maxrate", TARGET_VIDEO_MAXRATE,
        "-bufsize", TARGET_VIDEO_BUFSIZE,
        "-g", str(TARGET_GOP_FRAMES),
        "-keyint_min", str(TARGET_GOP_FRAMES),
        "-sc_threshold", "0",
        "-force_key_frames", f"expr:gte(t,n_forced*{TARGET_GOP_SECONDS})",
        "-threads", str(get_ffmpeg_thread_count()),
        "-c:a", "aac", "-b:a", TARGET_AUDIO_BITRATE, "-ar", "44100", "-ac", "2",
        "-max_muxing_queue_size", "1024",
        "-rw_timeout", str(STREAM_IO_TIMEOUT_US),
        "-f", "flv", rtmp_url,
    ]
    if audio_stream is None:
        # Keep the generated silent audio track bounded by the video duration.
        command.insert(command.index("-max_muxing_queue_size"), "-shortest")
    return command, "youtube_transcode"


def log_ffmpeg_resource_usage(process, session_id, process_handle=None):
    """Log one CPU/RAM sample when psutil is installed; never fail a stream for metrics."""
    if psutil is None or process is None:
        return
    try:
        # Reuse the same psutil.Process instance so CPU percentages measure an interval.
        p = process_handle or psutil.Process(process.pid)
        # First call primes psutil's non-blocking CPU counter; later calls report a sample.
        cpu_percent = p.cpu_percent(interval=None)
        rss_mb = p.memory_info().rss / (1024 * 1024)
        logger.info(
            "FFMPEG_RESOURCE session=%s pid=%s cpu_percent=%s rss_mb=%.1f",
            session_id, process.pid, cpu_percent, rss_mb,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        pass
    except Exception:
        logger.debug("Could not collect FFmpeg resource metrics for session %s", session_id, exc_info=True)


# ============================================================
# STATUS HELPERS
# ============================================================

def get_session_status(name, fallback=None):

    status_class = getattr(
        StreamingSession,
        "Status",
        None,
    )

    if status_class is None:
        return fallback or name.lower()

    value = getattr(
        status_class,
        name,
        None,
    )

    if value is None:
        return fallback or name.lower()

    return value


def get_item_status(name, fallback=None):

    status_class = getattr(
        StreamingSessionItem,
        "Status",
        None,
    )

    if status_class is None:
        return fallback or name.lower()

    value = getattr(
        status_class,
        name,
        None,
    )

    if value is None:
        return fallback or name.lower()

    return value


# ============================================================
# STREAMING LOG
# ============================================================

def create_stream_log(
    session,
    level,
    message,
    data=None,
):
    """
    Create a StreamingLog entry.

    IMPORTANT:
    Never put the YouTube stream key into `data`.
    """

    try:

        StreamingLog.objects.create(
            session=session,
            level=level,
            message=str(message)[:50000],
            data=data or {},
        )

    except Exception:

        logger.exception(
            "Could not create StreamingLog for session %s.",
            getattr(
                session,
                "id",
                None,
            ),
        )


def log_info(
    session,
    message,
    data=None,
):

    create_stream_log(
        session=session,
        level=StreamingLog.Level.INFO,
        message=message,
        data=data,
    )


def log_warning(
    session,
    message,
    data=None,
):

    create_stream_log(
        session=session,
        level=StreamingLog.Level.WARNING,
        message=message,
        data=data,
    )


def log_error(
    session,
    message,
    data=None,
):

    create_stream_log(
        session=session,
        level=StreamingLog.Level.ERROR,
        message=message,
        data=data,
    )


# ============================================================
# SAVE SESSION ERROR
# ============================================================

def save_session_error(
    session,
    error_message,
    *,
    item=None,
):

    error_message = str(
        error_message
    )[:MAX_ERROR_LENGTH]

    try:

        failed_status = get_session_status(
            "FAILED",
            fallback="failed",
        )

        now = timezone.now()

        session.status = failed_status

        session.last_error = error_message

        session.ended_at = now

        session.ffmpeg_pid = None

        session.save(
            update_fields=[
                "status",
                "last_error",
                "ended_at",
                "ffmpeg_pid",
                "updated_at",
            ]
        )

        if item is not None:

            failed_item_status = get_item_status(
                "FAILED",
                fallback="failed",
            )

            item.status = failed_item_status

            item.ended_at = now

            item.error_message = error_message

            item.save(
                update_fields=[
                    "status",
                    "ended_at",
                    "error_message",
                    "updated_at",
                ]
            )

        log_error(
            session,
            error_message,
            data={
                "video_id": (
                    str(item.video_id)
                    if item is not None
                    else None
                ),
                "position": (
                    item.position
                    if item is not None
                    else None
                ),
            },
        )

    except Exception:

        logger.exception(
            "Could not save session error."
        )



def schedule_stream_recovery(session, error_message, position=None):
    """Schedule a bounded retry after a transient FFmpeg exit.

    Recovery attempts are counted from recent session logs. A successful LIVE
    event resets the consecutive-failure count. Configuration:
      STREAM_AUTO_RECOVERY_ENABLED (default True)
      STREAM_MAX_RECOVERY_ATTEMPTS (default 3 retries)
      STREAM_RECOVERY_BASE_DELAY_SECONDS (default 15)
      STREAM_RECOVERY_MAX_DELAY_SECONDS (default 180)
    """
    if not getattr(settings, "STREAM_AUTO_RECOVERY_ENABLED", True):
        log_warning(
            session,
            "Automatic stream recovery is disabled by configuration.",
            data={"position": position},
        )
        return False

    try:
        max_attempts = max(0, int(getattr(settings, "STREAM_MAX_RECOVERY_ATTEMPTS", 3)))
        base_delay = max(1, int(getattr(settings, "STREAM_RECOVERY_BASE_DELAY_SECONDS", 15)))
        max_delay = max(base_delay, int(getattr(settings, "STREAM_RECOVERY_MAX_DELAY_SECONDS", 180)))
    except (TypeError, ValueError):
        max_attempts, base_delay, max_delay = 3, 15, 180

    if max_attempts <= 0:
        return False

    # Count retries since the last successful LIVE transition. Using the log
    # primary key avoids depending on a particular timestamp field name.
    attempts = 0
    try:
        recent_messages = (
            StreamingLog.objects.filter(session=session)
            .order_by("-pk")
            .values_list("message", flat=True)[:100]
        )
        for message in recent_messages:
            message = str(message or "")
            if message.startswith("Session is LIVE.") or message == "Streaming session stopped.":
                break
            if message.startswith("Automatic recovery attempt "):
                attempts += 1
    except Exception:
        logger.exception("Could not count recovery attempts for session %s", session.pk)

    if attempts >= max_attempts:
        log_error(
            session,
            "Automatic stream recovery limit reached; manual intervention is required.",
            data={
                "position": position,
                "attempts": attempts,
                "max_attempts": max_attempts,
                "error": str(error_message)[:2000],
            },
        )
        return False

    attempt_number = attempts + 1
    delay = min(base_delay * (2 ** (attempt_number - 1)), max_delay)

    log_warning(
        session,
        f"Automatic recovery attempt {attempt_number}/{max_attempts} scheduled.",
        data={
            "position": position,
            "attempt": attempt_number,
            "max_attempts": max_attempts,
            "retry_in_seconds": delay,
            "error": str(error_message)[:2000],
        },
    )

    try:
        start_streaming_session.apply_async(
            args=[str(session.pk)],
            countdown=delay,
        )
        logger.warning(
            "Scheduled stream recovery: session=%s attempt=%s/%s delay=%ss",
            session.pk, attempt_number, max_attempts, delay,
        )
        return True
    except Exception:
        logger.exception("Could not schedule stream recovery for session %s", session.pk)
        log_error(session, "Could not schedule automatic stream recovery.")
        return False



# ============================================================
# PROCESS CHECK
# ============================================================

def is_process_running(pid):

    if not pid:
        return False

    try:

        pid = int(pid)

    except (
        TypeError,
        ValueError,
    ):

        return False

    try:

        if os.name == "nt":

            result = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    f"PID eq {pid}",
                    "/NH",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            output = (
                result.stdout or ""
            ).strip()

            if not output:
                return False

            if (
                "No tasks are running"
                in output
            ):
                return False

            for line in output.splitlines():

                parts = line.split()

                if (
                    len(parts) >= 2
                    and parts[1] == str(pid)
                ):
                    return True

            return False

        os.kill(
            pid,
            0,
        )

        return True

    except (
        ProcessLookupError,
        PermissionError,
        OSError,
    ):

        return False

    except Exception:

        logger.exception(
            "Could not check FFmpeg PID=%s.",
            pid,
        )

        return False


# ============================================================
# TERMINATE FFMPEG
# ============================================================

def terminate_ffmpeg_process(
    process=None,
    pid=None,
):

    if process is not None:

        try:

            if process.poll() is None:

                pid = process.pid

        except Exception:

            pass

    if not pid:
        return

    try:

        pid = int(pid)

    except (
        TypeError,
        ValueError,
    ):

        return

    try:

        if os.name == "nt":

            result = subprocess.run(
                [
                    "taskkill",
                    "/PID",
                    str(pid),
                    "/T",
                    "/F",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )

            logger.info(
                "taskkill PID=%s return=%s",
                pid,
                result.returncode,
            )

        else:

            try:

                os.kill(
                    pid,
                    signal.SIGTERM,
                )

            except ProcessLookupError:

                pass

    except Exception:

        logger.exception(
            "Could not terminate FFmpeg PID=%s.",
            pid,
        )


# ============================================================
# VIDEO PATH
# ============================================================

def get_video_path(video):

    if not video:

        raise ValueError(
            "Playlist item does not contain a video."
        )

    if not video.video_file:

        raise ValueError(
            f'Video "{video.title}" '
            "does not have a video file."
        )

    try:

        path = video.video_file.path

    except Exception as exc:

        raise ValueError(
            f"Unable to access video file: {exc}"
        ) from exc

    if not os.path.isfile(path):

        raise ValueError(
            f"Video file does not exist: {path}"
        )

    return path


# ============================================================
# YOUTUBE RTMP
# ============================================================

def build_rtmp_url(session):

    ingestion_address = (
        session.youtube_rtmp_url or ""
    ).strip()

    stream_key = (
        session.youtube_stream_key or ""
    ).strip()

    if not ingestion_address:

        raise ValueError(
            "YouTube RTMP URL is not configured."
        )

    if not stream_key:

        raise ValueError(
            "YouTube stream key is not configured."
        )

    return (
        ingestion_address.rstrip("/")
        + "/"
        + stream_key
    )


# ============================================================
# SAFE RTMP URL
# ============================================================

def get_safe_rtmp_url(session):

    url = (
        session.youtube_rtmp_url or ""
    ).strip()

    if not url:
        return ""

    return (
        url.rstrip("/")
        + "/********"
    )


# ============================================================
# GET PLAYLIST ITEMS
# ============================================================

def get_playlist_items(session):

    return list(
        session.playlist.items
        .filter(
            is_enabled=True,
            video__is_active=True,
            video__status="ready",
        )
        .select_related(
            "video",
        )
        .order_by(
            "position",
        )
    )


# ============================================================
# GET SESSION ITEM
# ============================================================

def get_session_item(
    session,
    position,
):

    return (
        StreamingSessionItem.objects
        .select_related("video")
        .filter(
            session=session,
            position=position,
        )
        .first()
    )


# ============================================================
# MARK ITEM PLAYING
# ============================================================

def mark_item_playing(
    session,
    playlist_item,
    position,
):

    now = timezone.now()

    queued_status = get_item_status(
        "QUEUED",
        fallback="queued",
    )

    playing_status = get_item_status(
        "PLAYING",
        fallback="playing",
    )

    with transaction.atomic():

        # ----------------------------------------------------
        # Make every other item queued unless it is already
        # completed from an earlier play.
        # ----------------------------------------------------

        StreamingSessionItem.objects.filter(
            session=session,
            status=playing_status,
        ).exclude(
            position=position,
        ).update(
            status=queued_status,
            updated_at=now,
        )

        # ----------------------------------------------------
        # IMPORTANT:
        # Use session + POSITION.
        #
        # Do NOT create a new item for REPEAT mode.
        # ----------------------------------------------------

        item = (
            StreamingSessionItem.objects
            .select_for_update()
            .filter(
                session=session,
                position=position,
            )
            .first()
        )

        if item is None:

            item = (
                StreamingSessionItem.objects
                .create(
                    session=session,
                    video=playlist_item.video,
                    position=position,
                    status=playing_status,
                    started_at=now,
                    ended_at=None,
                    play_count=1,
                    error_message="",
                )
            )

        else:

            item.video = playlist_item.video

            item.status = playing_status

            item.started_at = now

            item.ended_at = None

            item.play_count += 1

            item.error_message = ""

            item.save(
                update_fields=[
                    "video",
                    "status",
                    "started_at",
                    "ended_at",
                    "play_count",
                    "error_message",
                    "updated_at",
                ]
            )

    return item


# ============================================================
# MARK ITEM COMPLETED
# ============================================================

def mark_item_completed(
    session,
    position,
):

    completed_status = get_item_status(
        "COMPLETED",
        fallback="completed",
    )

    now = timezone.now()

    item = (
        StreamingSessionItem.objects
        .filter(
            session=session,
            position=position,
        )
        .first()
    )

    if not item:
        return None

    item.status = completed_status

    item.ended_at = now

    item.save(
        update_fields=[
            "status",
            "ended_at",
            "updated_at",
        ]
    )

    return item


# ============================================================
# MARK ITEM FAILED
# ============================================================

def mark_item_failed(
    session,
    position,
    error_message,
):

    failed_status = get_item_status(
        "FAILED",
        fallback="failed",
    )

    now = timezone.now()

    item = (
        StreamingSessionItem.objects
        .filter(
            session=session,
            position=position,
        )
        .first()
    )

    if not item:
        return None

    item.status = failed_status

    item.ended_at = now

    item.error_message = str(
        error_message
    )[:MAX_ERROR_LENGTH]

    item.save(
        update_fields=[
            "status",
            "ended_at",
            "error_message",
            "updated_at",
        ]
    )

    return item


# ============================================================
# MARK CURRENT ITEM SKIPPED
# ============================================================

def mark_item_skipped(
    session,
    position,
    reason="",
):

    skipped_status = get_item_status(
        "SKIPPED",
        fallback="skipped",
    )

    now = timezone.now()

    item = (
        StreamingSessionItem.objects
        .filter(
            session=session,
            position=position,
        )
        .first()
    )

    if not item:
        return None

    item.status = skipped_status

    item.ended_at = now

    item.error_message = str(
        reason
    )[:MAX_ERROR_LENGTH]

    item.save(
        update_fields=[
            "status",
            "ended_at",
            "error_message",
            "updated_at",
        ]
    )

    return item


# ============================================================
# RESET ITEMS FOR REPEAT
# ============================================================
#
# IMPORTANT:
#
# We DO NOT reset play_count.
#
# We only prepare the items for another cycle.
#
# Example:
#
# Cycle 1:
# A = 1
# B = 1
# C = 1
#
# Cycle 2:
# A = 2
# B = 2
# C = 2
#
# ============================================================

def reset_session_items_for_repeat(
    session,
):

    queued_status = get_item_status(
        "QUEUED",
        fallback="queued",
    )

    StreamingSessionItem.objects.filter(
        session=session,
    ).update(
        status=queued_status,
        started_at=None,
        ended_at=None,
        error_message="",
        updated_at=timezone.now(),
    )


# ============================================================
# FFMPEG STDERR READER
# ============================================================

def read_ffmpeg_stderr(
    process,
    output_queue,
):

    try:

        if not process.stderr:
            return

        for line in process.stderr:

            line = (
                line or ""
            ).strip()

            if not line:
                continue

            try:

                output_queue.put_nowait(
                    line
                )

            except queue.Full:

                try:
                    output_queue.get_nowait()
                except queue.Empty:
                    pass

                try:
                    output_queue.put_nowait(
                        line
                    )
                except queue.Full:
                    pass

    except Exception:

        logger.exception(
            "FFmpeg stderr reader failed."
        )


# ============================================================
# DRAIN FFMPEG OUTPUT
# ============================================================

def drain_ffmpeg_output(
    output_queue,
    lines,
):

    while True:

        try:

            line = output_queue.get_nowait()

        except queue.Empty:

            break

        if line:

            logger.info(
                "FFmpeg: %s",
                line,
            )

            lines.append(line)

            if len(lines) > MAX_FFMPEG_LOG_LINES:

                del lines[
                    :len(lines)
                    - MAX_FFMPEG_LOG_LINES
                ]


# ============================================================
# START STREAMING SESSION
# ============================================================

@shared_task(
    bind=True,
    name="streaming.start_streaming_session",
)
def start_streaming_session(
    self,
    session_id,
):

    logger.info(
        "============================================================"
    )

    logger.info(
        "START STREAMING SESSION: %s",
        session_id,
    )

    logger.info(
        "============================================================"
    )

    process = None

    session = None

    current_item = None

    position = 0

    try:

        # ====================================================
        # GET SESSION
        # ====================================================

        try:

            session = (
                StreamingSession.objects
                .select_related(
                    "playlist",
                )
                .get(
                    id=session_id,
                )
            )

        except StreamingSession.DoesNotExist:

            logger.error(
                "Streaming session does not exist: %s",
                session_id,
            )

            return {
                "success": False,
                "error": (
                    "Streaming session does not exist."
                ),
            }

        # ====================================================
        # CHECK WHETHER SESSION IS ALREADY STOPPING/STOPPED
        # ====================================================

        stopped_status = get_session_status(
            "STOPPED",
            fallback="stopped",
        )

        stopping_status = get_session_status(
            "STOPPING",
            fallback="stopping",
        )

        if session.status in (
            stopped_status,
            stopping_status,
        ):

            return {
                "success": False,
                "session_id": str(session.id),
                "error": (
                    "Session has already been stopped."
                ),
            }

        # ====================================================
        # CHECK EXISTING FFMPEG
        # ====================================================

        if session.ffmpeg_pid:

            if is_process_running(
                session.ffmpeg_pid
            ):

                logger.warning(
                    "Session %s already has FFmpeg "
                    "running. PID=%s",
                    session_id,
                    session.ffmpeg_pid,
                )

                return {
                    "success": False,
                    "session_id": str(
                        session_id
                    ),
                    "error": (
                        "FFmpeg is already running."
                    ),
                }

            session.ffmpeg_pid = None

            session.save(
                update_fields=[
                    "ffmpeg_pid",
                    "updated_at",
                ]
            )

        # ====================================================
        # RTMP
        # ====================================================

        try:

            rtmp_url = build_rtmp_url(
                session
            )

        except ValueError as exc:

            save_session_error(
                session,
                exc,
            )

            return {
                "success": False,
                "session_id": str(
                    session_id
                ),
                "error": str(exc),
            }

        logger.info(
            "RTMP server: %s",
            session.youtube_rtmp_url,
        )

        # NEVER LOG rtmp_url.
        # It contains the private stream key.

        # ====================================================
        # PLAYLIST
        # ====================================================

        if not session.playlist:

            error = (
                "Streaming session does not "
                "have a playlist."
            )

            save_session_error(
                session,
                error,
            )

            return {
                "success": False,
                "session_id": str(
                    session_id
                ),
                "error": error,
            }

        items = get_playlist_items(
            session
        )

        if not items:

            error = (
                "The selected playlist has "
                "no enabled ready videos."
            )

            save_session_error(
                session,
                error,
            )

            return {
                "success": False,
                "session_id": str(
                    session_id
                ),
                "error": error,
            }

        total_items = len(items)

        # ====================================================
        # POSITION
        # ====================================================

        position = (
            session.current_item_position
            or 0
        )

        if position < 0:

            position = 0

        if position >= total_items:

            position = 0

        session.current_item_position = (
            position
        )

        # ====================================================
        # CURRENT PLAYLIST ITEM
        # ====================================================

        playlist_item = items[position]

        video = playlist_item.video

        if not video:

            error = (
                "Playlist item does not contain a video."
            )

            save_session_error(
                session,
                error,
            )

            return {
                "success": False,
                "session_id": str(
                    session_id
                ),
                "error": error,
            }

        # ====================================================
        # VIDEO PATH
        # ====================================================

        try:

            video_path = get_video_path(
                video
            )

        except ValueError as exc:

            current_item = (
                get_session_item(
                    session,
                    position,
                )
            )

            if current_item:

                mark_item_failed(
                    session,
                    position,
                    str(exc),
                )

            save_session_error(
                session,
                exc,
                item=current_item,
            )

            return {
                "success": False,
                "session_id": str(
                    session_id
                ),
                "error": str(exc),
            }

        # ====================================================
        # MARK ITEM PLAYING
        # ====================================================

        current_item = mark_item_playing(
            session=session,
            playlist_item=playlist_item,
            position=position,
        )

        if current_item is None:

            error = (
                "Could not create or update "
                "the streaming session item."
            )

            save_session_error(
                session,
                error,
            )

            return {
                "success": False,
                "session_id": str(
                    session_id
                ),
                "error": error,
            }

        # ====================================================
        # SESSION STARTING
        # ====================================================

        starting_status = get_session_status(
            "STARTING",
            fallback="starting",
        )

        now = timezone.now()

        session.status = starting_status

        session.current_item_position = (
            position
        )

        session.current_video_started_at = (
            now
        )

        session.current_video_ended_at = None

        # A restarted/recovered session is active again; clear the previous
        # terminal timestamp while preserving the original started_at value.
        session.ended_at = None
        session.last_error = ""

        session.save(
            update_fields=[
                "status",
                "current_item_position",
                "current_video_started_at",
                "current_video_ended_at",
                "ended_at",
                "last_error",
                "updated_at",
            ]
        )

        log_info(
            session,
            f"Starting video: {video.title}",
            data={
                "video_id": str(video.id),
                "position": position,
                "play_count": current_item.play_count,
            },
        )

        # ====================================================
        # FFMPEG COMMAND
        # ====================================================

        # ====================================================
        # VIDEO DURATION
        # ====================================================
        #
        # Duration is calculated during upload by FFprobe and
        # stored in Video.duration_seconds.
        #
        # FFmpeg is explicitly limited to that duration.
        #
        # ====================================================

        try:

            video_duration = int(
                video.duration_seconds or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            video_duration = 0

        if video_duration <= 0:

            error = (
                f'Video "{video.title}" has an invalid '
                f'duration: {video.duration_seconds!r} seconds.'
            )

            logger.error(
                "Invalid video duration. "
                "Session=%s Video=%s Duration=%s",
                session_id,
                video.id,
                video.duration_seconds,
            )

            mark_item_failed(
                session,
                position,
                error,
            )

            save_session_error(
                session,
                error,
                item=current_item,
            )

            return {
                "success": False,
                "session_id": str(
                    session_id
                ),
                "error": error,
            }

        logger.info(
            "Starting video: %s | duration=%s seconds",
            video.title,
            video_duration,
        )

        log_info(
            session,
            (
                f"Video duration: "
                f"{video_duration} seconds."
            ),
            data={
                "video_id": str(video.id),
                "position": position,
                "duration_seconds": video_duration,
            },
        )

        # ====================================================
        # FFMPEG COMMAND
        # ====================================================

        # Probe the actual media before choosing the streaming mode. Normalized uploads
        # should use -c copy; legacy/un-normalized files use a safe transcode fallback.
        media_metadata = probe_media(video_path)
        command, stream_mode = build_stream_command(
            video_path=video_path,
            rtmp_url=rtmp_url,
            duration_seconds=video_duration,
            metadata=media_metadata,
        )
        logger.info("Session %s selected FFmpeg mode=%s", session_id, stream_mode)
        log_info(
            session,
            f"FFmpeg mode selected: {stream_mode}",
            data={"video_id": str(video.id), "position": position, "mode": stream_mode},
        )

        # SAFE COMMAND FOR DATABASE/LOGS
        # ====================================================

        safe_command = list(
            command
        )

        if safe_command:

            safe_command[-1] = (
                get_safe_rtmp_url(
                    session
                )
            )

        logger.info(
            "FFmpeg command: %s",
            " ".join(
                safe_command
            ),
        )

        # ====================================================
        # START FFMPEG
        # ====================================================

        try:

            creation_flags = getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )

            process = subprocess.Popen(

                command,

                stdout=subprocess.DEVNULL,

                stderr=subprocess.PIPE,

                stdin=subprocess.DEVNULL,

                text=True,

                bufsize=1,

                creationflags=creation_flags,
            )

        except FileNotFoundError:

            error = (
                "FFmpeg executable not found: "
                f"{FFMPEG_BINARY}"
            )

            mark_item_failed(
                session,
                position,
                error,
            )

            save_session_error(
                session,
                error,
                item=current_item,
            )

            return {
                "success": False,
                "session_id": str(
                    session_id
                ),
                "error": error,
            }

        except Exception as exc:

            mark_item_failed(
                session,
                position,
                str(exc),
            )

            save_session_error(
                session,
                exc,
                item=current_item,
            )

            return {
                "success": False,
                "session_id": str(
                    session_id
                ),
                "error": str(exc),
            }

        # ====================================================
        # SAVE FFMPEG PID
        # ====================================================

        session.ffmpeg_pid = process.pid

        session.ffmpeg_command = (
            " ".join(
                safe_command
            )
        )

        session.last_error = ""

        session.save(
            update_fields=[
                "ffmpeg_pid",
                "ffmpeg_command",
                "last_error",
                "updated_at",
            ]
        )

        logger.info(
            "FFmpeg started. PID=%s",
            process.pid,
        )

        log_info(
            session,
            "FFmpeg process started.",
            data={
                "pid": process.pid,
                "video_id": str(video.id),
                "position": position,
            },
        )

        # ====================================================
        # NON-BLOCKING STDERR READER
        # ====================================================

        output_queue = queue.Queue(
            maxsize=500
        )

        ffmpeg_output = []

        stderr_thread = threading.Thread(
            target=read_ffmpeg_stderr,
            args=(
                process,
                output_queue,
            ),
            daemon=True,
        )

        stderr_thread.start()

        # ====================================================
        # STARTUP CHECK
        # ====================================================

        time.sleep(
            FFMPEG_STARTUP_WAIT
        )

        drain_ffmpeg_output(
            output_queue,
            ffmpeg_output,
        )

        return_code = process.poll()

        # A very short clip may finish successfully during the startup wait.
        # Only treat a non-zero early exit as an FFmpeg startup failure.
        if return_code is not None and return_code != 0:

            drain_ffmpeg_output(
                output_queue,
                ffmpeg_output,
            )

            error_message = (
                "FFmpeg exited immediately "
                f"with code {return_code}."
            )

            if ffmpeg_output:

                error_message += (
                    "\nFFmpeg output:\n"
                    + "\n".join(
                        ffmpeg_output[-50:]
                    )
                )

            error_message = (
                error_message[-MAX_ERROR_LENGTH:]
            )

            mark_item_failed(
                session,
                position,
                error_message,
            )

            save_session_error(
                session,
                error_message,
                item=current_item,
            )
            recovery_scheduled = schedule_stream_recovery(
                session,
                error_message,
                position=position,
            )

            return {
                "success": False,
                "session_id": str(
                    session_id
                ),
                "error": error_message,
                "return_code": return_code,
                "recovery_scheduled": recovery_scheduled,
            }

        # ====================================================
        # MARK LIVE
        # ====================================================

        live_status = get_session_status(
            "LIVE",
            fallback="live",
        )

        now = timezone.now()

        session.status = live_status

        if not session.started_at:

            session.started_at = now

        session.last_error = ""

        session.save(
            update_fields=[
                "status",
                "started_at",
                "last_error",
                "updated_at",
            ]
        )

        log_info(
            session,
            f"Session is LIVE. Playing: {video.title}",
            data={
                "video_id": str(video.id),
                "position": position,
                "play_count": current_item.play_count,
            },
        )

        logger.info(
            "Session %s is LIVE.",
            session_id,
        )

        # ====================================================
        # MONITOR FFMPEG
        # ====================================================

        manually_stopped = False
        last_resource_log = time.monotonic()
        ffmpeg_resource_process = None
        if psutil is not None:
            try:
                ffmpeg_resource_process = psutil.Process(process.pid)
                ffmpeg_resource_process.cpu_percent(interval=None)  # Prime CPU sampler.
            except Exception:
                ffmpeg_resource_process = None

        while True:

            drain_ffmpeg_output(
                output_queue,
                ffmpeg_output,
            )

            return_code = process.poll()

            now_monotonic = time.monotonic()
            if now_monotonic - last_resource_log >= RESOURCE_LOG_INTERVAL:
                log_ffmpeg_resource_usage(process, session_id, ffmpeg_resource_process)
                last_resource_log = now_monotonic

            # ------------------------------------------------
            # FFMPEG FINISHED
            # ------------------------------------------------

            if return_code is not None:

                break

            # ------------------------------------------------
            # CHECK SESSION STATUS
            # ------------------------------------------------

            try:

                session.refresh_from_db(
                    fields=[
                        "status",
                        "ffmpeg_pid",
                        "auto_continue",
                        "auto_stop",
                        "mode",
                        "current_item_position",
                    ]
                )

                if session.status in (
                    stopped_status,
                    stopping_status,
                ):

                    manually_stopped = True

                    logger.info(
                        "Session %s was stopped externally.",
                        session_id,
                    )

                    terminate_ffmpeg_process(
                        process=process,
                    )

                    break

            except Exception:

                logger.exception(
                    "Could not refresh session status."
                )

            time.sleep(
                PROCESS_CHECK_INTERVAL
            )

        # ====================================================
        # DRAIN FINAL OUTPUT
        # ====================================================

        drain_ffmpeg_output(
            output_queue,
            ffmpeg_output,
        )

        try:

            stderr_thread.join(
                timeout=1
            )

        except Exception:

            pass

        # ====================================================
        # MANUAL STOP
        # ====================================================

        if manually_stopped:

            stopped_status = get_session_status(
                "STOPPED",
                fallback="stopped",
            )

            now = timezone.now()

            mark_item_skipped(
                session,
                position,
                "Streaming session stopped.",
            )

            session.status = stopped_status

            session.ffmpeg_pid = None

            session.current_video_ended_at = (
                now
            )

            session.ended_at = now

            session.save(
                update_fields=[
                    "status",
                    "ffmpeg_pid",
                    "current_video_ended_at",
                    "ended_at",
                    "updated_at",
                ]
            )

            log_info(
                session,
                "Streaming session stopped.",
                data={
                    "position": position,
                    "video_id": str(video.id),
                },
            )

            return {
                "success": True,
                "session_id": str(
                    session_id
                ),
                "stopped": True,
            }

        # ====================================================
        # FFMPEG RETURN CODE
        # ====================================================

        return_code = (
            process.returncode
        )

        logger.info(
            "FFmpeg stopped for session %s. "
            "Return code=%s",
            session_id,
            return_code,
        )

        # ====================================================
        # NORMAL VIDEO COMPLETION
        # ====================================================

        if return_code == 0:

            completed_item = (
                mark_item_completed(
                    session,
                    position,
                )
            )

            now = timezone.now()

            session.current_video_ended_at = (
                now
            )

            session.ffmpeg_pid = None

            session.save(
                update_fields=[
                    "current_video_ended_at",
                    "ffmpeg_pid",
                    "updated_at",
                ]
            )

            log_info(
                session,
                f"Completed video: {video.title}",
                data={
                    "video_id": str(video.id),
                    "position": position,
                    "play_count": (
                        completed_item.play_count
                        if completed_item
                        else None
                    ),
                },
            )

            # =================================================
            # REFRESH
            # =================================================

            session.refresh_from_db()

            items = get_playlist_items(
                session
            )

            total_items = len(
                items
            )

            if total_items == 0:

                error = (
                    "Playlist became empty "
                    "while streaming."
                )

                save_session_error(
                    session,
                    error,
                )

                return {
                    "success": False,
                    "session_id": str(
                        session_id
                    ),
                    "error": error,
                }

            # =================================================
            # NEXT POSITION
            # =================================================

            next_position = (
                position + 1
            )

            repeat_mode = getattr(
                StreamingSession.Mode,
                "REPEAT",
                "repeat",
            )

            is_repeat = (
                session.mode
                == repeat_mode
            )

            # =================================================
            # AUTO CONTINUE CHECK
            # =================================================
            # REPEAT mode is controlled by the playback mode and
            # must continue to the next video / next cycle even if
            # auto_continue=False. For ONE_BY_ONE mode,
            # auto_continue=False still means stop after the
            # current video.
            # =================================================

            if not is_repeat and not session.auto_continue:

                stopped_status = get_session_status(
                    "STOPPED",
                    fallback="stopped",
                )

                now = timezone.now()

                session.status = (
                    stopped_status
                )

                session.ended_at = now

                session.ffmpeg_pid = None

                session.save(
                    update_fields=[
                        "status",
                        "ended_at",
                        "ffmpeg_pid",
                        "updated_at",
                    ]
                )

                log_info(
                    session,
                    (
                        "Playback stopped because "
                        "auto_continue is disabled."
                    ),
                    data={
                        "position": position,
                        "video_id": str(video.id),
                    },
                )

                return {
                    "success": True,
                    "session_id": str(
                        session_id
                    ),
                    "video": video.title,
                    "completed": True,
                    "stopped": True,
                    "reason": "auto_continue_disabled",
                }

            # =================================================
            # MORE VIDEOS
            # =================================================

            if next_position < total_items:

                session.current_item_position = (
                    next_position
                )

                session.ffmpeg_pid = None

                session.save(
                    update_fields=[
                        "current_item_position",
                        "ffmpeg_pid",
                        "updated_at",
                    ]
                )

                log_info(
                    session,
                    (
                        f"Moving to next video: "
                        f"{items[next_position].video.title}"
                    ),
                    data={
                        "previous_position": position,
                        "next_position": next_position,
                    },
                )

                start_streaming_session.delay(
                    str(session.id)
                )

                return {
                    "success": True,
                    "session_id": str(
                        session_id
                    ),
                    "video": video.title,
                    "next_position": next_position,
                    "mode": session.mode,
                }

            # =================================================
            # END OF PLAYLIST
            # =================================================

            # -------------------------------------------------
            # REPEAT MODE
            # -------------------------------------------------

            if is_repeat:

                # ---------------------------------------------
                # REPEAT FOREVER
                # ---------------------------------------------
                # In REPEAT mode, auto_stop and auto_continue do
                # not terminate playback at the end of a cycle.
                # The session continues until it is manually
                # stopped (or another explicit scheduler/stop
                # control changes the session status).
                # ---------------------------------------------

                reset_session_items_for_repeat(
                    session
                )

                session.current_item_position = 0

                session.ffmpeg_pid = None

                session.status = live_status

                session.save(
                    update_fields=[
                        "current_item_position",
                        "ffmpeg_pid",
                        "status",
                        "updated_at",
                    ]
                )

                log_info(
                    session,
                    (
                        "Playlist completed. REPEAT mode restarting "
                        "from the first video. auto_stop and "
                        "auto_continue do not end REPEAT mode."
                    ),
                    data={
                        "completed_position": position,
                        "next_position": 0,
                        "total_items": total_items,
                    },
                )

                start_streaming_session.delay(
                    str(session.id)
                )

                return {
                    "success": True,
                    "session_id": str(
                        session_id
                    ),
                    "video": video.title,
                    "repeated": True,
                    "next_position": 0,
                    "mode": session.mode,
                }

            # -------------------------------------------------
            # ONE BY ONE COMPLETE
            # -------------------------------------------------

            stopped_status = get_session_status(
                "STOPPED",
                fallback="stopped",
            )

            now = timezone.now()

            session.status = (
                stopped_status
            )

            session.ended_at = now

            session.ffmpeg_pid = None

            session.current_item_position = (
                total_items - 1
            )

            session.save(
                update_fields=[
                    "status",
                    "ended_at",
                    "ffmpeg_pid",
                    "current_item_position",
                    "updated_at",
                ]
            )

            log_info(
                session,
                (
                    "ONE_BY_ONE playlist completed. "
                    "Session stopped."
                ),
                data={
                    "total_items": total_items,
                },
            )

            return {
                "success": True,
                "session_id": str(
                    session_id
                ),
                "video": video.title,
                "completed": True,
                "mode": session.mode,
            }

        # ====================================================
        # FFMPEG ERROR
        # ====================================================

        error_message = (
            "FFmpeg exited with code "
            f"{return_code}."
        )

        if ffmpeg_output:

            error_message += (
                "\nFFmpeg output:\n"
                + "\n".join(
                    ffmpeg_output[-50:]
                )
            )

        error_message = (
            error_message[-MAX_ERROR_LENGTH:]
        )

        logger.error(
            "FFmpeg failed for session %s:\n%s",
            session_id,
            error_message,
        )

        mark_item_failed(
            session,
            position,
            error_message,
        )

        save_session_error(
            session,
            error_message,
            item=current_item,
        )
        recovery_scheduled = schedule_stream_recovery(
            session,
            error_message,
            position=position,
        )

        return {
            "success": False,
            "session_id": str(
                session_id
            ),
            "error": error_message,
            "return_code": return_code,
            "recovery_scheduled": recovery_scheduled,
        }

    # ========================================================
    # UNEXPECTED ERROR
    # ========================================================

    except Exception as exc:

        logger.exception(
            "Unexpected streaming error for session %s.",
            session_id,
        )

        if process is not None:

            try:

                if process.poll() is None:

                    terminate_ffmpeg_process(
                        process=process
                    )

            except Exception:

                logger.exception(
                    "Could not clean up FFmpeg."
                )

        if session is not None:

            if current_item is not None:

                try:

                    mark_item_failed(
                        session,
                        position,
                        str(exc),
                    )

                except Exception:

                    logger.exception(
                        "Could not mark item failed."
                    )

            save_session_error(
                session,
                exc,
                item=current_item,
            )

        return {
            "success": False,
            "session_id": str(
                session_id
            ),
            "error": str(exc),
        }


# ============================================================
# STOP STREAMING SESSION
# ============================================================

@shared_task(
    bind=True,
    name="streaming.stop_streaming_session",
)
def stop_streaming_session(
    self,
    session_id,
):

    logger.info(
        "Stopping streaming session: %s",
        session_id,
    )

    try:

        session = (
            StreamingSession.objects
            .get(
                id=session_id,
            )
        )

    except StreamingSession.DoesNotExist:

        logger.error(
            "Session does not exist: %s",
            session_id,
        )

        return {
            "success": False,
            "error": (
                "Session does not exist."
            ),
        }

    # ========================================================
    # STOPPING
    # ========================================================

    stopping_status = get_session_status(
        "STOPPING",
        fallback="stopping",
    )

    try:

        session.status = (
            stopping_status
        )

        session.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

    except Exception:

        logger.exception(
            "Could not set STOPPING status."
        )

    # ========================================================
    # LOG
    # ========================================================

    log_info(
        session,
        "Stop requested for streaming session.",
    )

    # ========================================================
    # PID
    # ========================================================

    pid = session.ffmpeg_pid

    # ========================================================
    # STOP FFMPEG
    # ========================================================

    if pid:

        logger.info(
            "Stopping FFmpeg PID=%s",
            pid,
        )

        terminate_ffmpeg_process(
            pid=pid,
        )

    # ========================================================
    # FINAL STATUS
    # ========================================================

    try:

        stopped_status = get_session_status(
            "STOPPED",
            fallback="stopped",
        )

        now = timezone.now()

        # ----------------------------------------------------
        # Mark currently playing item skipped
        # ----------------------------------------------------

        current_position = (
            session.current_item_position
            or 0
        )

        current_item = get_session_item(
            session,
            current_position,
        )

        if (
            current_item is not None
            and current_item.status
            == get_item_status(
                "PLAYING",
                fallback="playing",
            )
        ):

            mark_item_skipped(
                session,
                current_position,
                "Session manually stopped.",
            )

        # ----------------------------------------------------
        # Session
        # ----------------------------------------------------

        session.status = (
            stopped_status
        )

        session.ffmpeg_pid = None

        session.current_video_ended_at = (
            now
        )

        session.ended_at = now

        session.save(
            update_fields=[
                "status",
                "ffmpeg_pid",
                "current_video_ended_at",
                "ended_at",
                "updated_at",
            ]
        )

    except Exception as exc:

        logger.exception(
            "Could not update session after stopping."
        )

        return {
            "success": False,
            "session_id": str(
                session_id
            ),
            "error": str(exc),
        }

    log_info(
        session,
        "Streaming session stopped.",
    )

    logger.info(
        "Streaming session %s stopped.",
        session_id,
    )

    return {
        "success": True,
        "session_id": str(
            session_id
        ),
    }


# ============================================================
# START SCHEDULED STREAMS
# ============================================================

@shared_task(
    name="streaming.start_scheduled_streams",
)
def start_scheduled_streams():

    now = timezone.now()

    logger.info(
        "Checking scheduled streaming sessions. "
        "NOW=%s",
        now,
    )

    queued_status = get_session_status(
        "QUEUED",
        fallback="queued",
    )

    starting_status = get_session_status(
        "STARTING",
        fallback="starting",
    )

    sessions = (
        StreamingSession.objects
        .filter(
            status=queued_status,
            auto_start=True,
            scheduled_start__isnull=False,
            scheduled_start__lte=now,
        )
        .order_by(
            "scheduled_start",
        )
    )

    logger.info(
        "Scheduled sessions found: %s",
        sessions.count(),
    )

    started_count = 0

    for session in sessions:

        try:

            # =================================================
            # ATOMIC CLAIM
            # =================================================
            #
            # Prevents Celery Beat from launching the same
            # session twice.
            #
            # =================================================

            updated = (
                StreamingSession.objects
                .filter(
                    pk=session.pk,
                    status=queued_status,
                    auto_start=True,
                )
                .update(
                    status=starting_status,
                    updated_at=timezone.now(),
                )
            )

            if not updated:

                continue

            logger.info(
                "Starting scheduled session: %s",
                session.pk,
            )

            log_info(
                session,
                "Scheduled start triggered.",
                data={
                    "scheduled_start": (
                        session.scheduled_start.isoformat()
                        if session.scheduled_start
                        else None
                    ),
                },
            )

            start_streaming_session.delay(
                str(session.pk)
            )

            started_count += 1

        except Exception as exc:

            logger.exception(
                "Scheduled stream failed: %s",
                session.pk,
            )

            save_session_error(
                session,
                exc,
            )

    logger.info(
        "Scheduled streaming check completed. "
        "Started=%s",
        started_count,
    )

    return {
        "success": True,
        "started": started_count,
        "checked_at": now.isoformat(),
    }



from .models import Video


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="streaming.process_uploaded_video",
)
def process_uploaded_video(self, video_id):
    """Validate and normalize an uploaded video to a streaming-ready 1080p MP4.

    Output profile: H.264, 1920x1080 canvas (aspect ratio preserved with padding),
    yuv420p, 30 fps, AAC 44.1 kHz stereo. The source file is retained until the
    normalized file is saved and the database update succeeds.
    """
    normalized_temp_path = None
    saved_normalized_name = None
    original_name = None
    video = None

    try:
        video = Video.objects.get(pk=video_id)
        if not video.video_file:
            raise ValueError("Video file is missing.")

        storage = video.video_file.storage
        source_path = video.video_file.path  # Current project uses local filesystem storage.
        original_name = video.video_file.name
        if not os.path.isfile(source_path):
            raise FileNotFoundError(f"Video file not found: {source_path}")

        source_metadata = probe_media(source_path)
        source_video, source_audio = get_media_streams(source_metadata)
        if source_video is None:
            raise ValueError("No video stream found in uploaded file.")

        # If a previous attempt already produced a compliant file, do not re-encode it.
        if is_stream_copy_compatible(source_metadata):
            normalized_path = source_path
            normalized_name = original_name
            normalized_metadata = source_metadata
        else:
            source_dir = os.path.dirname(source_path)
            normalized_temp_path = os.path.join(
                source_dir,
                f".{uuid.uuid4().hex}.streaming_1080p.tmp.mp4",
            )

            command = [
                FFMPEG_BINARY,
                "-hide_banner", "-nostdin", "-y", "-i", source_path,
            ]
            if source_audio is None:
                command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
                audio_map = ["-map", "1:a:0"]
            else:
                audio_map = ["-map", "0:a:0?"]

            command += [
                "-map", "0:v:0", *audio_map,
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30",
                "-c:v", "libx264", "-preset", "veryfast",
                "-b:v", TARGET_VIDEO_BITRATE, "-maxrate", TARGET_VIDEO_MAXRATE,
                "-bufsize", TARGET_VIDEO_BUFSIZE,
                "-pix_fmt", "yuv420p", "-r", str(TARGET_FPS),
                "-g", str(TARGET_GOP_FRAMES), "-keyint_min", str(TARGET_GOP_FRAMES),
                "-sc_threshold", "0",
                "-force_key_frames", f"expr:gte(t,n_forced*{TARGET_GOP_SECONDS})",
                "-threads", str(get_ffmpeg_thread_count()),
                "-c:a", "aac", "-b:a", TARGET_AUDIO_BITRATE, "-ar", "44100", "-ac", "2",
                "-movflags", "+faststart",
            ]
            if source_audio is None:
                command += ["-shortest"]
            command += ["-f", "mp4", normalized_temp_path]

            logger.info("Normalizing uploaded video %s to 1080p H.264/AAC", video_id)
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=getattr(settings, "VIDEO_NORMALIZATION_TIMEOUT", 24 * 60 * 60),
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "Unknown FFmpeg error")[-4000:]
                raise RuntimeError(f"Video normalization failed: {detail}")
            if not os.path.isfile(normalized_temp_path) or os.path.getsize(normalized_temp_path) == 0:
                raise RuntimeError("FFmpeg did not produce a valid normalized video file.")

            normalized_metadata = probe_media(normalized_temp_path)
            if not is_stream_copy_compatible(normalized_metadata):
                raise RuntimeError("Normalized output failed the H.264/AAC 1080p compatibility check.")

            # Save a new MP4 through Django storage; do not overwrite the source before DB success.
            base, _ext = os.path.splitext(original_name)
            normalized_storage_name = f"{base}_streaming_1080p_{uuid.uuid4().hex[:10]}.mp4"
            with open(normalized_temp_path, "rb") as normalized_file:
                saved_normalized_name = storage.save(normalized_storage_name, File(normalized_file))
            normalized_path = storage.path(saved_normalized_name)
            normalized_name = saved_normalized_name

        # Populate model metadata from the actual streaming-ready file.
        fmt = normalized_metadata.get("format", {})
        video_stream, audio_stream = get_media_streams(normalized_metadata)
        if video_stream is None:
            raise ValueError("Normalized video has no video stream.")

        video.duration_seconds = round(float(fmt.get("duration", 0) or 0))
        video.file_size = int(fmt.get("size", 0) or os.path.getsize(normalized_path))
        video.width = int(video_stream.get("width", 0) or 0)
        video.height = int(video_stream.get("height", 0) or 0)
        video.codec = video_stream.get("codec_name", "")
        video.frame_rate = _parse_frame_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
        bitrate_value = video_stream.get("bit_rate") or fmt.get("bit_rate") or 0
        try:
            video.bitrate = int(float(bitrate_value))
        except (ValueError, TypeError):
            video.bitrate = 0

        if normalized_name != original_name:
            video.video_file.name = normalized_name
        video.status = Video.Status.READY
        video.processing_error = ""
        video.processing_completed_at = timezone.now()
        video.save(update_fields=[
            "video_file", "duration_seconds", "file_size", "width", "height",
            "frame_rate", "codec", "bitrate", "status", "processing_error",
            "processing_completed_at", "updated_at",
        ])

        # Only delete the original after the database now points at the normalized file.
        if normalized_name != original_name:
            try:
                storage.delete(original_name)
            except Exception:
                logger.warning("Normalized video saved, but old source file could not be deleted: %s", original_name, exc_info=True)

        logger.info(
            "Video %s is READY: %sx%s %s, streaming copy eligible=%s",
            video_id, video.width, video.height, video.codec,
            is_stream_copy_compatible(normalized_metadata),
        )
        return {
            "video_id": str(video_id),
            "status": "READY",
            "normalized": normalized_name != original_name,
            "width": video.width,
            "height": video.height,
            "codec": video.codec,
            "stream_copy_eligible": is_stream_copy_compatible(normalized_metadata),
        }

    except Video.DoesNotExist:
        logger.error("Video %s does not exist", video_id)
        raise
    except Exception as exc:
        logger.exception("Video processing/normalization failed: %s", video_id)
        # Clean up only our generated files. Preserve original upload on any failure.
        if saved_normalized_name and video is not None:
            try:
                video.video_file.storage.delete(saved_normalized_name)
            except Exception:
                logger.warning("Could not clean up failed normalized file %s", saved_normalized_name, exc_info=True)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        try:
            video = Video.objects.get(pk=video_id)
            video.status = Video.Status.FAILED
            video.processing_error = str(exc)[:4000]
            video.save(update_fields=["status", "processing_error", "updated_at"])
        except Video.DoesNotExist:
            pass
        raise
    finally:
        if normalized_temp_path and os.path.exists(normalized_temp_path):
            try:
                os.remove(normalized_temp_path)
            except OSError:
                logger.warning("Could not remove temporary normalized file %s", normalized_temp_path, exc_info=True)
