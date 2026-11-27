from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import ApiToken, Community, Flag, Incident, Notification, ReputationEvent, User, Vote


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    ordering = ("email",)
    list_display = ("email", "name", "role", "community", "reputation", "is_staff", "is_active")
    search_fields = ("email", "name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Información personal", {"fields": ("name", "role", "community", "reputation")}),
        ("Permisos", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Fechas", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "name", "role", "password1", "password2"),
            },
        ),
    )


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "status", "veracity", "community", "hidden", "expires_at", "time")
    list_filter = ("status", "veracity", "hidden", "community", "priority", "type")
    search_fields = ("id", "description", "reporter_name")


admin.site.register(ApiToken)
admin.site.register(Notification)


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "lat", "lng", "order", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ReputationEvent)
class ReputationEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "delta", "reason", "incident")
    readonly_fields = ("created_at",)


admin.site.register(Vote)
admin.site.register(Flag)
