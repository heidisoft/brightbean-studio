from django.apps import AppConfig


class ContentRetentionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.content_retention"
    verbose_name = "Content Retention"

    def ready(self):
        from django.db.models.signals import post_migrate

        post_migrate.connect(self._register_tasks, sender=self)

    @staticmethod
    def _register_tasks(sender, **kwargs):
        from apps.common.background import register_recurring_task
        from apps.content_retention.tasks import CONTENT_RETENTION_INTERVAL_SECONDS, run_content_retention_sweep

        register_recurring_task(
            run_content_retention_sweep,
            repeat=CONTENT_RETENTION_INTERVAL_SECONDS,
            verbose_name="run_content_retention_sweep",
        )
