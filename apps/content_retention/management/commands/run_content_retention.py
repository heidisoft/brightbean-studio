"""Expire post content whose retention date has passed.

The same code path as the daily ``run_content_retention_sweep`` background
task (``apps.content_retention.services.run_retention``).

Usage:
    python manage.py run_content_retention --dry-run
    python manage.py run_content_retention --workspace <uuid>
"""

from django.core.management.base import BaseCommand

from apps.content_retention.services import run_retention


class Command(BaseCommand):
    help = "Archive or purge post content past its workspace's retention period (analytics are kept)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report due posts without changing anything.")
        parser.add_argument("--workspace", help="Only process this workspace ID.")
        parser.add_argument("--batch-size", type=int, default=200, help="Posts per batch (default: 200).")

    def handle(self, *args, **options):
        results = run_retention(
            workspace_id=options["workspace"],
            dry_run=options["dry_run"],
            batch_size=options["batch_size"],
            log=lambda msg: self.stdout.write(f"[retention] {msg}"),
        )
        if options["dry_run"]:
            due = sum(r["due"] for r in results.values())
            self.stdout.write(
                self.style.WARNING(f"[retention] DRY RUN: {due} post(s) due across {len(results)} workspace(s)")
            )
            return
        purged = sum(r["purged"] for r in results.values())
        deleted = sum(r["deleted"] for r in results.values())
        errors = sum(r["errors"] for r in results.values())
        mb = sum(r["bytes_freed"] for r in results.values()) / (1024 * 1024)
        self.stdout.write(
            self.style.SUCCESS(
                f"[retention] {purged} purged, {deleted} deleted, {errors} errors, ~{mb:.1f} MB of media freed"
            )
        )
