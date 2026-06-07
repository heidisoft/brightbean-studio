from django.contrib import admin

from .models import PlatformCredential, SmtpCredential, SmtpTestLog


@admin.register(PlatformCredential)
class PlatformCredentialAdmin(admin.ModelAdmin):
    list_display = ("organization", "platform", "is_configured", "test_result", "tested_at")
    list_filter = ("platform", "is_configured", "test_result")
    search_fields = ("organization__name",)
    readonly_fields = ("id", "created_at", "updated_at", "tested_at")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        # Never show raw encrypted credentials in admin
        if "credentials" not in fields:
            fields.append("credentials")
        return fields


@admin.register(SmtpCredential)
class SmtpCredentialAdmin(admin.ModelAdmin):
    list_display = ("organization", "is_configured", "test_result", "tested_at")
    list_filter = ("is_configured", "test_result")
    search_fields = ("organization__name",)
    readonly_fields = ("id", "created_at", "updated_at", "tested_at", "credentials")


@admin.register(SmtpTestLog)
class SmtpTestLogAdmin(admin.ModelAdmin):
    list_display = ("organization", "recipient_email", "from_email", "host", "port", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("organization__name", "recipient_email", "from_email", "host")
    readonly_fields = tuple(f.name for f in SmtpTestLog._meta.fields)
