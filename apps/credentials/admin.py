from django.contrib import admin

from .forms import PlatformCredentialAdminForm
from .models import PlatformCredential, SmtpCredential, SmtpTestLog


@admin.register(PlatformCredential)
class PlatformCredentialAdmin(admin.ModelAdmin):
    form = PlatformCredentialAdminForm
    list_display = ("organization", "platform", "is_configured", "test_result", "tested_at")
    list_filter = ("platform", "is_configured", "test_result")
    search_fields = ("organization__name",)
    # is_configured is derived from the credentials on save (see model); the test
    # fields have no flow yet — keep them read-only so they can't be hand-set.
    readonly_fields = ("id", "is_configured", "test_result", "tested_at", "created_at", "updated_at")

    # Editing credentials necessarily reveals decrypted secrets on the change
    # page, so restrict the whole model to superusers (admin already requires
    # is_staff).
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(SmtpCredential)
class SmtpCredentialAdmin(admin.ModelAdmin):
    list_display = ("organization", "is_configured", "test_result", "tested_at")
    list_filter = ("is_configured", "test_result")
    search_fields = ("organization__name",)
    readonly_fields = ("id", "created_at", "updated_at", "tested_at", "credentials")

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(SmtpTestLog)
class SmtpTestLogAdmin(admin.ModelAdmin):
    list_display = ("organization", "recipient_email", "from_email", "host", "port", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("organization__name", "recipient_email", "from_email", "host")
    readonly_fields = tuple(f.name for f in SmtpTestLog._meta.fields)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
