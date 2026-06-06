from django import forms


class SmtpCredentialForm(forms.Form):
    from_email = forms.EmailField(
        label="Sending email",
        help_text="The From address used for invites and transactional email.",
    )
    host = forms.CharField(label="SMTP host", max_length=255)
    port = forms.IntegerField(label="SMTP port", min_value=1, max_value=65535, initial=587)
    username = forms.CharField(label="Username", max_length=255, required=False)
    password = forms.CharField(
        label="Password",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the existing password.",
    )
    use_tls = forms.BooleanField(label="Use TLS", required=False, initial=True)
    use_ssl = forms.BooleanField(label="Use SSL", required=False)
    timeout = forms.IntegerField(label="Timeout seconds", min_value=1, max_value=120, initial=10)
    is_configured = forms.BooleanField(label="Enable SMTP sending", required=False, initial=True)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("use_tls") and cleaned.get("use_ssl"):
            raise forms.ValidationError("Use either TLS or SSL, not both.")
        return cleaned
