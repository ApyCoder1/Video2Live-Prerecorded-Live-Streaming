from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static


urlpatterns = [

    # ============================================================
    # DJANGO ADMIN
    # ============================================================

    path(
        "admin/",
        admin.site.urls
    ),

    # ============================================================
    # AUTHENTICATION
    # ============================================================

    path(
        "accounts/login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html"
        ),
        name="login",
    ),

    path(
        "accounts/logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),

    # ============================================================
    # STREAMING APP
    # ============================================================

    path(
        "",
        include("streaming.urls")
    ),
]


# ============================================================
# STATIC & MEDIA FILES
# Development only
# ============================================================

if settings.DEBUG:

    # Media files
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT
    )

    # Static files
    urlpatterns += static(
        settings.STATIC_URL,
        document_root=settings.STATIC_ROOT
    )