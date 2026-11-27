from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import path

from incidents import views


urlpatterns = [
    path("admin/", admin.site.urls),
    path("administracion/", views.admin_panel, name="admin_panel"),
    path("", views.home, name="home"),
    path("api/docs", views.api_docs, name="api_docs"),
    path("api/meta", views.meta, name="meta"),
    path("api/auth/register", views.register, name="register"),
    path("api/auth/login", views.login, name="login"),
    path("api/auth/me", views.current_user, name="current_user"),
    path("api/auth/logout", views.logout, name="logout"),
    path("api/auth/password-reset/request", views.password_reset_request, name="password_reset_request"),
    path("api/auth/password-reset/confirm", views.password_reset_confirm, name="password_reset_confirm"),
    path("api/incidents", views.incident_collection, name="incident_collection"),
    path("api/incidents/<str:incident_id>/vote", views.incident_vote, name="incident_vote"),
    path("api/incidents/<str:incident_id>/flag", views.incident_flag, name="incident_flag"),
    path("api/incidents/<str:incident_id>", views.incident_detail, name="incident_detail"),
    path("api/admin/stats", views.admin_stats, name="admin_stats"),
    path("api/admin/incidents", views.admin_incidents, name="admin_incidents"),
    path("api/users", views.users, name="users"),
    path("api/users/<str:user_id>/role", views.user_role, name="user_role"),
    path("api/notifications", views.notifications, name="notifications"),
    path("api/notifications/read", views.mark_notifications_read, name="mark_notifications_read"),
    path("api/<path:remaining>", views.api_not_found, name="api_not_found"),
]

urlpatterns += staticfiles_urlpatterns()
