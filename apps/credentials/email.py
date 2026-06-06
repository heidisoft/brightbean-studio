"""Email helpers backed by organization SMTP credentials."""

from django.conf import settings
from django.core.mail import get_connection

from .models import SmtpCredential


def get_smtp_credential(organization):
    """Return the configured SMTP credential for an organization, if present."""
    try:
        credential = SmtpCredential.objects.get(organization=organization, is_configured=True)
    except SmtpCredential.DoesNotExist:
        return None

    data = credential.credentials or {}
    if not data.get("host") or not data.get("from_email"):
        return None
    return credential


def get_email_connection_for_org(organization):
    """Return an SMTP connection for org credentials, or Django's default."""
    credential = get_smtp_credential(organization)
    if not credential:
        return get_connection()
    return get_connection(**credential.connection_kwargs())


def get_from_email_for_org(organization):
    """Return the sender address configured for an org, or the app default."""
    credential = get_smtp_credential(organization)
    if credential and credential.from_email:
        return credential.from_email
    return getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@localhost")
