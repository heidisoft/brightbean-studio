from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMultiAlternatives, get_connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.members.decorators import require_org_role
from apps.members.models import OrgMembership

from .forms import SmtpCredentialForm
from .models import PlatformCredential, SmtpCredential, SmtpTestLog

PLATFORM_FIELDS = {
    "facebook": {
        "label": "Facebook",
        "fields": [
            {"name": "app_id", "label": "App ID", "type": "text"},
            {"name": "app_secret", "label": "App Secret", "type": "password"},
        ],
        "help": "Create an app at Meta for Developers. You'll need a Facebook App with Facebook Login enabled.",
        "docs_url": "https://developers.facebook.com/apps/",
        "docs_label": "Meta for Developers",
        "shared_with": ["Instagram", "Threads"],
    },
    "instagram": {
        "label": "Instagram (Business)",
        "fields": [
            {"name": "app_id", "label": "App ID", "type": "text"},
            {"name": "app_secret", "label": "App Secret", "type": "password"},
        ],
        "help": "Uses the same Meta App as Facebook. Enable Instagram Graph API in your app settings.",
        "docs_url": "https://developers.facebook.com/apps/",
        "docs_label": "Meta for Developers",
        "shared_with": ["Facebook", "Threads"],
    },
    "instagram_login": {
        "label": "Instagram (Direct)",
        "fields": [
            {"name": "app_id", "label": "App ID", "type": "text"},
            {"name": "app_secret", "label": "App Secret", "type": "password"},
        ],
        "help": "Requires an Instagram App for direct Instagram Login.",
        "docs_url": "https://developers.facebook.com/apps/",
        "docs_label": "Meta for Developers",
    },
    "threads": {
        "label": "Threads",
        "fields": [
            {"name": "app_id", "label": "App ID", "type": "text"},
            {"name": "app_secret", "label": "App Secret", "type": "password"},
        ],
        "help": "Uses the same Meta App as Facebook. Enable Threads API in your app settings.",
        "docs_url": "https://developers.facebook.com/apps/",
        "docs_label": "Meta for Developers",
        "shared_with": ["Facebook", "Instagram"],
    },
    "linkedin_personal": {
        "label": "LinkedIn (Personal)",
        "fields": [
            {"name": "client_id", "label": "Client ID", "type": "text"},
            {"name": "client_secret", "label": "Client Secret", "type": "password"},
        ],
        "help": "Create an app in the LinkedIn Developer Portal.",
        "docs_url": "https://www.linkedin.com/developers/apps",
        "docs_label": "LinkedIn Developer Portal",
        "shared_with": ["LinkedIn (Company)"],
    },
    "linkedin_company": {
        "label": "LinkedIn (Company)",
        "fields": [
            {"name": "client_id", "label": "Client ID", "type": "text"},
            {"name": "client_secret", "label": "Client Secret", "type": "password"},
        ],
        "help": "Uses the same LinkedIn app as Personal. Ensure Company Page access is approved.",
        "docs_url": "https://www.linkedin.com/developers/apps",
        "docs_label": "LinkedIn Developer Portal",
        "shared_with": ["LinkedIn (Personal)"],
    },
    "tiktok": {
        "label": "TikTok",
        "fields": [
            {"name": "client_key", "label": "Client Key", "type": "text"},
            {"name": "client_secret", "label": "Client Secret", "type": "password"},
        ],
        "help": "Register at the TikTok for Developers portal.",
        "docs_url": "https://developers.tiktok.com/",
        "docs_label": "TikTok for Developers",
    },
    "youtube": {
        "label": "YouTube",
        "fields": [
            {"name": "client_id", "label": "Client ID", "type": "text"},
            {"name": "client_secret", "label": "Client Secret", "type": "password"},
        ],
        "help": "Create OAuth credentials in Google Cloud Console. Enable the YouTube Data API v3.",
        "docs_url": "https://console.cloud.google.com/apis/credentials",
        "docs_label": "Google Cloud Console",
        "shared_with": ["Google Business Profile"],
    },
    "google_business": {
        "label": "Google Business Profile",
        "fields": [
            {"name": "client_id", "label": "Client ID", "type": "text"},
            {"name": "client_secret", "label": "Client Secret", "type": "password"},
        ],
        "help": "Uses the same Google OAuth client as YouTube.",
        "docs_url": "https://console.cloud.google.com/apis/credentials",
        "docs_label": "Google Cloud Console",
        "shared_with": ["YouTube"],
    },
    "pinterest": {
        "label": "Pinterest",
        "fields": [
            {"name": "app_id", "label": "App ID", "type": "text"},
            {"name": "app_secret", "label": "App Secret", "type": "password"},
        ],
        "help": "Create an app at Pinterest for Developers.",
        "docs_url": "https://developers.pinterest.com/apps/",
        "docs_label": "Pinterest for Developers",
    },
    "bluesky": {
        "label": "Bluesky",
        "fields": [],
        "help": "Bluesky uses app passwords instead of OAuth. No developer credentials needed.",
        "no_setup_needed": True,
    },
    "mastodon": {
        "label": "Mastodon",
        "fields": [],
        "help": "Mastodon registers app credentials per server during connection. No org-wide setup is needed.",
        "no_setup_needed": True,
    },
}


def _initial_smtp_data(credential):
    if not credential:
        return {
            "port": 587,
            "use_tls": True,
            "use_ssl": False,
            "timeout": 10,
            "is_configured": True,
        }
    data = credential.credentials or {}
    return {
        "from_email": data.get("from_email", ""),
        "host": data.get("host", ""),
        "port": data.get("port", 587),
        "username": data.get("username", ""),
        "use_tls": data.get("use_tls", True),
        "use_ssl": data.get("use_ssl", False),
        "timeout": data.get("timeout", 10),
        "is_configured": credential.is_configured,
    }


def _save_smtp_credential(org, form, credential=None):
    existing = (credential.credentials or {}) if credential else {}
    data = form.cleaned_data
    password = data.get("password") or existing.get("password", "")
    credential, _created = SmtpCredential.objects.update_or_create(
        organization=org,
        defaults={
            "credentials": {
                "from_email": data["from_email"],
                "host": data["host"],
                "port": data["port"],
                "username": data.get("username", ""),
                "password": password,
                "use_tls": data.get("use_tls", False),
                "use_ssl": data.get("use_ssl", False),
                "timeout": data.get("timeout") or 10,
            },
            "is_configured": data.get("is_configured", False),
            "test_result": SmtpCredential.TestResult.UNTESTED,
            "last_error": "",
        },
    )
    return credential


def _send_smtp_test_email(request, credential):
    data = credential.credentials or {}
    connection = get_connection(**credential.connection_kwargs())
    text_content = render_to_string(
        "credentials/email/smtp_test.txt",
        {
            "user": request.user,
            "org": request.org,
        },
    )
    msg = EmailMultiAlternatives(
        subject="Brightbean SMTP test",
        body=text_content,
        from_email=data["from_email"],
        to=[request.user.email],
        connection=connection,
    )
    msg.send(fail_silently=False)


def _create_smtp_test_log(request, credential, *, status, error=""):
    data = credential.credentials or {}
    return SmtpTestLog.objects.create(
        organization=request.org,
        smtp_credential=credential,
        created_by=request.user,
        recipient_email=request.user.email,
        from_email=data.get("from_email", ""),
        host=data.get("host", ""),
        port=int(data.get("port") or 587),
        status=status,
        error=error,
    )


def _platform_rows(org):
    existing = {c.platform: c for c in PlatformCredential.objects.filter(organization=org)}
    rows = []
    for platform_value, config in PLATFORM_FIELDS.items():
        cred = existing.get(platform_value)
        rows.append(
            {
                "value": platform_value,
                "label": config["label"],
                "is_configured": cred.is_configured if cred else False,
                "test_result": cred.test_result if cred else "untested",
                "masked": cred.masked_credentials if cred else {},
                "config": config,
            }
        )
    return rows


@login_required
@require_org_role("admin")
def credentials_list(request):
    return render(
        request,
        "credentials/list.html",
        {
            "platforms": _platform_rows(request.org),
            "settings_active": "credentials",
        },
    )


@login_required
@require_org_role("admin")
def smtp_settings(request):
    credential = SmtpCredential.objects.filter(organization=request.org).first()

    if request.method == "POST":
        form = SmtpCredentialForm(request.POST)
        if form.is_valid():
            credential = _save_smtp_credential(request.org, form, credential)
            if request.POST.get("action") == "test":
                try:
                    _send_smtp_test_email(request, credential)
                except Exception as exc:
                    credential.test_result = SmtpCredential.TestResult.FAILURE
                    credential.tested_at = timezone.now()
                    credential.last_error = str(exc)
                    credential.save(update_fields=["test_result", "tested_at", "last_error", "updated_at"])
                    _create_smtp_test_log(
                        request,
                        credential,
                        status=SmtpTestLog.Status.FAILURE,
                        error=str(exc),
                    )
                    messages.error(request, "SMTP settings were saved, but the test email failed.")
                else:
                    credential.test_result = SmtpCredential.TestResult.SUCCESS
                    credential.tested_at = timezone.now()
                    credential.last_error = ""
                    credential.save(update_fields=["test_result", "tested_at", "last_error", "updated_at"])
                    _create_smtp_test_log(request, credential, status=SmtpTestLog.Status.SUCCESS)
                    messages.success(request, f"SMTP settings saved. Test email sent to {request.user.email}.")
            else:
                messages.success(request, "SMTP settings saved.")
            return redirect("credentials:smtp")
    else:
        form = SmtpCredentialForm(initial=_initial_smtp_data(credential))

    return render(
        request,
        "credentials/smtp.html",
        {
            "form": form,
            "smtp_credential": credential,
            "smtp_logs": SmtpTestLog.objects.filter(organization=request.org).select_related("created_by")[:20],
            "settings_active": "smtp",
        },
    )


@login_required
@require_POST
def credentials_save(request, platform):
    org = request.org
    if not org:
        return JsonResponse({"error": "No organization"}, status=400)

    if request.org_membership.org_role not in (
        OrgMembership.OrgRole.OWNER,
        OrgMembership.OrgRole.ADMIN,
    ):
        return JsonResponse({"error": "Permission denied"}, status=403)

    config = PLATFORM_FIELDS.get(platform)
    if not config:
        return JsonResponse({"error": "Unknown platform"}, status=400)

    existing_credential = PlatformCredential.objects.filter(organization=org, platform=platform).first()
    existing_data = (existing_credential.credentials or {}) if existing_credential else {}
    credentials = {}
    for field in config.get("fields", []):
        value = request.POST.get(field["name"], "").strip()
        credentials[field["name"]] = value or existing_data.get(field["name"], "")

    has_all = all(credentials.get(f["name"]) for f in config.get("fields", []))
    PlatformCredential.objects.update_or_create(
        organization=org,
        platform=platform,
        defaults={
            "credentials": credentials,
            "is_configured": has_all and bool(credentials),
            "test_result": PlatformCredential.TestResult.UNTESTED,
        },
    )

    if has_all and credentials:
        messages.success(request, f"{config['label']} credentials saved successfully.")
    else:
        messages.warning(request, f"Some fields are missing for {config['label']}.")

    return redirect("credentials:list")


@login_required
@require_POST
def credentials_remove(request, platform):
    org = request.org
    if not org:
        return JsonResponse({"error": "No organization"}, status=400)

    if request.org_membership.org_role not in (
        OrgMembership.OrgRole.OWNER,
        OrgMembership.OrgRole.ADMIN,
    ):
        return JsonResponse({"error": "Permission denied"}, status=403)

    PlatformCredential.objects.filter(organization=org, platform=platform).delete()
    messages.success(request, "Credentials removed.")
    return redirect("credentials:list")
