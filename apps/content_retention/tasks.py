"""Background tasks for content retention."""

import logging

from background_task import background

logger = logging.getLogger(__name__)

# How often expired content is swept; registered on a repeating schedule by
# apps.content_retention.apps.ContentRetentionConfig.
CONTENT_RETENTION_INTERVAL_SECONDS = 24 * 60 * 60  # daily


@background(schedule=0)
def run_content_retention_sweep():
    """Expire due post content in every workspace with retention enabled."""
    from .services import run_retention

    run_retention(log=logger.info)


@background(schedule=0)
def run_workspace_content_retention(workspace_id):
    """One-off sweep of a single workspace (the settings page's "Run now")."""
    from .services import run_retention

    run_retention(workspace_id=workspace_id, log=logger.info)
