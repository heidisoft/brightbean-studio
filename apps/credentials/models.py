import uuid

from django.db import models

from apps.common.encryption import EncryptedJSONField
from apps.common.managers import OrgScopedManager


class PlatformCredential(models.Model):
    class Platform(models.TextChoices):
        FACEBOOK = "facebook", "Facebook"
        INSTAGRAM = "instagram", "Instagram"
        INSTAGRAM_LOGIN = "instagram_login", "Instagram (Direct)"
        LINKEDIN_PERSONAL = "linkedin_personal", "LinkedIn (Personal Profile)"
        LINKEDIN_COMPANY = "linkedin_company", "LinkedIn (Company Page)"
        TIKTOK = "tiktok", "TikTok"
        YOUTUBE = "youtube", "YouTube"
        PINTEREST = "pinterest", "Pinterest"
        THREADS = "threads", "Threads"
        BLUESKY = "bluesky", "Bluesky"
        GOOGLE_BUSINESS = "google_business", "Google Business Profile"
        MASTODON = "mastodon", "Mastodon"

    class TestResult(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"
        UNTESTED = "untested", "Untested"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="platform_credentials",
    )
    platform = models.CharField(max_length=30, choices=Platform.choices)
    credentials = EncryptedJSONField(
        default=dict,
        help_text="Encrypted JSON containing platform-specific credential fields",
    )
    is_configured = models.BooleanField(default=False)
    tested_at = models.DateTimeField(blank=True, null=True)
    test_result = models.CharField(
        max_length=20,
        choices=TestResult.choices,
        default=TestResult.UNTESTED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OrgScopedManager()

    class Meta:
        db_table = "credentials_platform_credential"
        unique_together = [("organization", "platform")]

    def __str__(self):
        return f"{self.organization.name} - {self.get_platform_display()}"

    @property
    def masked_credentials(self):
        """Return credentials with secrets masked (last 4 chars only)."""
        masked = {}
        for key, value in (self.credentials or {}).items():
            if isinstance(value, str) and len(value) > 4:
                masked[key] = "****" + value[-4:]
            else:
                masked[key] = "****"
        return masked


class SmtpCredential(models.Model):
    """Organization-scoped SMTP settings for transactional email."""

    class TestResult(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"
        UNTESTED = "untested", "Untested"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="smtp_credential",
    )
    credentials = EncryptedJSONField(
        default=dict,
        help_text="Encrypted JSON containing SMTP host, port, username, password, and sender details.",
    )
    is_configured = models.BooleanField(default=False)
    tested_at = models.DateTimeField(blank=True, null=True)
    test_result = models.CharField(
        max_length=20,
        choices=TestResult.choices,
        default=TestResult.UNTESTED,
    )
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OrgScopedManager()

    class Meta:
        db_table = "credentials_smtp_credential"

    def __str__(self):
        return f"{self.organization.name} - SMTP"

    @property
    def masked_credentials(self):
        masked = {}
        for key, value in (self.credentials or {}).items():
            if key == "password" and value:
                masked[key] = "****" + str(value)[-4:]
            else:
                masked[key] = value
        return masked

    @property
    def from_email(self):
        return (self.credentials or {}).get("from_email", "")

    def connection_kwargs(self):
        data = self.credentials or {}
        return {
            "backend": "django.core.mail.backends.smtp.EmailBackend",
            "host": data.get("host", ""),
            "port": int(data.get("port") or 587),
            "username": data.get("username", ""),
            "password": data.get("password", ""),
            "use_tls": bool(data.get("use_tls", True)),
            "use_ssl": bool(data.get("use_ssl", False)),
            "timeout": int(data.get("timeout") or 10),
        }
