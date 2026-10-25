from django.urls import path
from django.views.decorators.http import require_POST
from . import views


app_name = "streaming"


urlpatterns = [

    # Dashboard
    path(
        "",
        views.dashboard,
        name="dashboard"
    ),

    # Videos
    path(
        "videos/",
        views.video_list,
        name="video_list"
    ),

    path(
        "videos/upload/",
        views.video_upload,
        name="video_upload"
    ),

    path(
        "videos/<uuid:pk>/",
        views.video_detail,
        name="video_detail"
    ),

    path(
        "videos/<uuid:pk>/delete/",
        views.video_delete,
        name="video_delete"
    ),

    # Playlists
    path(
        "playlists/",
        views.playlist_list,
        name="playlist_list"
    ),

    path(
        "playlists/create/",
        views.playlist_create,
        name="playlist_create"
    ),

    path(
        "playlists/<uuid:pk>/",
        views.playlist_detail,
        name="playlist_detail"
    ),

    path(
        "playlists/<uuid:pk>/add-video/",
        views.playlist_add_video,
        name="playlist_add_video"
    ),

    path(
        "playlists/<uuid:playlist_pk>/remove-video/<uuid:item_pk>/",
        views.playlist_remove_video,
        name="playlist_remove_video"
    ),

    path(
        "playlists/<uuid:pk>/reorder/",
        views.playlist_reorder,
        name="playlist_reorder"
    ),

    # Streaming Sessions
    path(
        "sessions/",
        views.session_list,
        name="session_list"
    ),

    path(
        "sessions/create/",
        views.session_create,
        name="session_create"
    ),

    path(
        "sessions/<uuid:pk>/",
        views.session_detail,
        name="session_detail"
    ),

    path(
        "sessions/<uuid:pk>/start/",
        views.session_start,
        name="session_start"
    ),

    path(
        "sessions/<uuid:pk>/stop/",
        views.session_stop,
        name="session_stop"
    ),

    path(
        "sessions/<uuid:pk>/skip/",
        views.session_skip,
        name="session_skip"
    ),
    path(
    "playlists/<uuid:pk>/edit/",
    views.playlist_edit,
    name="playlist_edit",
),

path(
    "playlists/<uuid:pk>/delete/",
    views.playlist_delete,
    name="playlist_delete",
),

path(
    "sessions/<uuid:pk>/edit/",
    views.session_edit,
    name="session_edit",
),

]