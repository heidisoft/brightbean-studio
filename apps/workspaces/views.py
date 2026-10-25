from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.members.decorators import require_org_role
from apps.members.models import OrgMembership, WorkspaceMembership

from .models import Workspace


@login_required
def workspace_list(request):
    memberships = WorkspaceMembership.objects.filter(user=request.user).select_related("workspace")
    workspaces = [m.workspace for m in memberships if not m.workspace.is_archived]
    return render(request, "workspaces/list.html", {"workspaces": workspaces})


@login_required
@require_POST
@require_org_role(OrgMembership.OrgRole.ADMIN)
def workspace_create(request):
    """Create a new workspace in the user's organization."""
    name = request.POST.get("name", "").strip()
    if not name:
        return redirect("dashboard")

    if not request.org:
        return redirect("dashboard")

    workspace = Workspace.objects.create(
        organization=request.org,
        name=name,
    )

    WorkspaceMembership.objects.create(
        user=request.user,
        workspace=workspace,
        workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
    )

    # Set as current workspace
    request.user.last_workspace_id = workspace.id
    request.user.save(update_fields=["last_workspace_id"])

    return redirect("calendar:calendar", workspace_id=workspace.id)


@login_required
@require_http_methods(["GET", "POST"])
def workspace_settings(request, workspace_id):
    try:
        workspace = Workspace.objects.get(id=workspace_id)
    except Workspace.DoesNotExist:
        raise Http404 from None

    membership = WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).first()
    if not membership:
        raise Http404

    is_owner_or_manager = membership.workspace_role in (
        WorkspaceMembership.WorkspaceRole.OWNER,
        WorkspaceMembership.WorkspaceRole.MANAGER,
    )

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "archive_workspace" and is_owner_or_manager:
            with transaction.atomic():
                active_count = (
                    Workspace.objects.select_for_update()
                    .filter(organization=workspace.organization, is_archived=False)
                    .count()
                )
                if active_count <= 1:
                    messages.error(request, "Cannot archive the last workspace in the organization.")
                    return redirect("workspaces:settings", workspace_id=workspace.id)
                workspace.is_archived = True
                workspace.save(update_fields=["is_archived"])
            messages.success(request, f'Workspace "{workspace.name}" has been archived.')
            return redirect("organizations:workspaces")

        if action == "unarchive_workspace" and is_owner_or_manager:
            workspace.is_archived = False
            workspace.save(update_fields=["is_archived"])
            messages.success(request, f'Workspace "{workspace.name}" has been restored.')
            return redirect("workspaces:settings", workspace_id=workspace.id)

        if action == "delete_workspace" and is_owner_or_manager:
            with transaction.atomic():
                active_count = (
                    Workspace.objects.select_for_update()
                    .filter(organization=workspace.organization, is_archived=False)
                    .count()
                )
                if active_count <= 1 and not workspace.is_archived:
                    messages.error(request, "Cannot delete the last workspace in the organization.")
                    return redirect("workspaces:settings", workspace_id=workspace.id)
                workspace_name = workspace.name
                workspace.delete()
            messages.success(request, f'Workspace "{workspace_name}" has been permanently deleted.')
            return redirect("organizations:workspaces")

        name = request.POST.get("name", "").strip()

        if name:
            workspace.name = name

        # Handle logo deletion
        if request.POST.get("delete_icon") == "1":
            if workspace.icon:
                workspace.icon.delete(save=False)
        # Handle logo upload
        elif "icon" in request.FILES:
            icon = request.FILES["icon"]

            # Validate file type
            allowed_types = ("image/jpeg", "image/png", "image/webp", "image/gif")
            if icon.content_type not in allowed_types:
                messages.error(request, "Logo must be a JPEG, PNG, WebP, or GIF image.")
                return redirect("workspaces:settings", workspace_id=workspace.id)

            # Validate file size (2 MB max)
            max_size = 2 * 1024 * 1024
            if icon.size > max_size:
                messages.error(request, "Logo must be under 2 MB.")
                return redirect("workspaces:settings", workspace_id=workspace.id)

            # Delete old icon before saving new one
            if workspace.icon:
                workspace.icon.delete(save=False)
            workspace.icon = icon

        workspace.save()
        messages.success(request, "Workspace settings updated.")
        return redirect("workspaces:settings", workspace_id=workspace.id)

    active_count = Workspace.objects.filter(organization=workspace.organization, is_archived=False).count()
    is_last_active = active_count <= 1 and not workspace.is_archived
    can_archive = is_owner_or_manager and not workspace.is_archived and not is_last_active
    can_delete = is_owner_or_manager and not is_last_active

    return render(
        request,
        "workspaces/settings.html",
        {
            "workspace": workspace,
            "settings_active": "general",
            "is_owner_or_manager": is_owner_or_manager,
            "can_archive": can_archive,
            "can_delete": can_delete,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def approvals_settings(request, workspace_id):
    try:
        workspace = Workspace.objects.get(id=workspace_id)
    except Workspace.DoesNotExist:
        raise Http404 from None

    membership = WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).first()
    if not membership:
        raise Http404

    is_owner_or_manager = membership.workspace_role in (
        WorkspaceMembership.WorkspaceRole.OWNER,
        WorkspaceMembership.WorkspaceRole.MANAGER,
    )

    if request.method == "POST":
        if not is_owner_or_manager:
            raise Http404

        mode = request.POST.get("approval_workflow_mode", "")
        valid_modes = Workspace.ApprovalWorkflowMode.values
        if mode not in valid_modes:
            messages.error(request, "Invalid approval workflow mode.")
            return redirect("workspaces:approvals_settings", workspace_id=workspace.id)

        workspace.approval_workflow_mode = mode
        workspace.save(update_fields=["approval_workflow_mode", "updated_at"])
        messages.success(request, "Approval workflow updated.")
        return redirect("workspaces:approvals_settings", workspace_id=workspace.id)

    return render(
        request,
        "workspaces/approvals_settings.html",
        {
            "workspace": workspace,
            "settings_active": "approvals",
            "is_owner_or_manager": is_owner_or_manager,
            "approval_modes": Workspace.ApprovalWorkflowMode,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def retention_settings(request, workspace_id):
    from apps.content_retention import services as retention
    from apps.content_retention.tasks import run_workspace_content_retention

    try:
        workspace = Workspace.objects.get(id=workspace_id)
    except Workspace.DoesNotExist:
        raise Http404 from None

    membership = WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).first()
    if not membership:
        raise Http404

    is_owner_or_manager = membership.workspace_role in (
        WorkspaceMembership.WorkspaceRole.OWNER,
        WorkspaceMembership.WorkspaceRole.MANAGER,
    )

    if request.method == "POST":
        if not is_owner_or_manager:
            raise Http404

        if request.POST.get("action") == "run_now":
            run_workspace_content_retention(str(workspace.id))
            messages.success(request, "Cleanup queued. Due posts will be processed in the background shortly.")
            return redirect("workspaces:retention_settings", workspace_id=workspace.id)

        periods = {}
        for field in ("retention_days", "draft_retention_days"):
            try:
                periods[field] = int(request.POST.get(field, ""))
            except ValueError:
                periods[field] = 0
            if not retention.MIN_RETENTION_DAYS <= periods[field] <= retention.MAX_RETENTION_DAYS:
                messages.error(
                    request,
                    f"Retention periods must be between {retention.MIN_RETENTION_DAYS} "
                    f"and {retention.MAX_RETENTION_DAYS} days.",
                )
                return redirect("workspaces:retention_settings", workspace_id=workspace.id)

        try:
            redated = retention.save_policy(
                workspace,
                enabled=request.POST.get("retention_enabled") == "1",
                days=periods["retention_days"],
                action=request.POST.get("retention_action", ""),
                drafts_enabled=request.POST.get("drafts_enabled") == "1",
                draft_days=periods["draft_retention_days"],
                apply_to_existing=request.POST.get("apply_to_existing") == "1",
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("workspaces:retention_settings", workspace_id=workspace.id)

        if redated:
            messages.success(request, f"Content retention updated. {redated} existing post(s) re-dated.")
        else:
            messages.success(request, "Content retention updated.")
        return redirect("workspaces:retention_settings", workspace_id=workspace.id)

    return render(
        request,
        "workspaces/retention_settings.html",
        {
            "workspace": workspace,
            "settings_active": "retention",
            "is_owner_or_manager": is_owner_or_manager,
            "policy": retention.get_policy(workspace.id, workspace.organization_id),
            "archive_available": retention.archive_available(),
            "overview": retention.retention_overview(workspace),
            "last_run": retention.get_last_run(workspace.id),
            "min_days": retention.MIN_RETENTION_DAYS,
            "max_days": retention.MAX_RETENTION_DAYS,
        },
    )
