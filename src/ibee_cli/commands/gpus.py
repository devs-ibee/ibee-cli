"""GPU VM commands."""

from __future__ import annotations

from typing import List, Optional

from ibee.validation import SORT_DIRECTIONS, VM_SORT_FIELDS, validate_vm_list_params

import typer

from ..context import get_client, get_settings, require_workspace
from ..helpers import (
    confirm_destructive,
    finish_operation,
    idempotency_key_option,
    poll_interval_option,
    preflight_billing,
    resolve_idempotency_key,
    resolve_wait,
    response_items,
    timeout_option,
)
from ..render import cell, handle_api_errors, print_json, print_table
from .vm_lifecycle import GPU_VM, register_vm_lifecycle

app = typer.Typer(help="GPU VMs", no_args_is_help=True)
register_vm_lifecycle(app, GPU_VM)


@app.command("list")
@handle_api_errors
def list_gpu_vms(
    ctx: typer.Context,
    limit: Optional[int] = typer.Option(
        None, "--limit", min=1, max=100, help="Return one page of at most N VMs (1-100)"
    ),
    offset: Optional[int] = typer.Option(None, "--offset", min=0, help="Skip N VMs (one page)"),
    search: Optional[str] = typer.Option(None, "--search", help="Filter by text (max 120 characters)"),
    sort_by: Optional[str] = typer.Option(
        None, "--sort-by", help="Sort field: " + ", ".join(VM_SORT_FIELDS)
    ),
    sort_direction: Optional[str] = typer.Option(
        None, "--sort-direction", help="Sort direction: " + ", ".join(SORT_DIRECTIONS)
    ),
) -> None:
    """List GPU VMs in the workspace.

    Without --limit/--offset every page is fetched; with either, one page is returned.
    The paging, search and sort options are not yet part of the published API
    contract; behaviour may change.
    """
    settings = get_settings(ctx)
    paging = validate_vm_list_params(
        limit=limit, offset=offset, search=search, sort_by=sort_by, sort_direction=sort_direction
    )
    workspace = require_workspace(settings)
    client = get_client(settings)
    result = client.gpu_vms.list_gpu_vms(workspace_id=workspace, **paging)
    if settings.structured_output:
        print_json(result)
        return
    vms = response_items(result, "items", "vms")
    print_table(
        "GPU VMs",
        ["ID", "Name", "Status", "GPU", "GPUs", "CPU", "RAM (MB)", "Public IP"],
        [
            tuple(
                cell(v, name)
                for name in (
                    "id", "name", "status", "gpu_model", "gpu_count", "cpu", "ram_mb", "public_ip"
                )
            )
            for v in vms
        ],
    )


@app.command("get")
@handle_api_errors
def get_gpu_vm(ctx: typer.Context, vm_id: str = typer.Argument(..., help="GPU VM ID")) -> None:
    """Show one GPU VM."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.gpu_vms.get_gpu_vm(workspace_id=require_workspace(settings), vm_id=vm_id)
    print_json(result)


@app.command("create")
@handle_api_errors
def create_gpu_vm(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Display name for the GPU VM"),
    site_id: Optional[str] = typer.Option(
        None,
        "--site-id",
        help="Optional placement site; omit for automatic placement",
    ),
    gpu_model: str = typer.Option(..., "--gpu-model", help="GPU model (A100, H100, L40S, RTX4090)"),
    gpu_count: int = typer.Option(1, "--gpu-count", help="Number of GPUs to attach"),
    os_distro: str = typer.Option("ubuntu", "--os-distro", help="OS distribution"),
    os_type: str = typer.Option("linux", "--os-type", help="OS family (linux, windows)"),
    cpu: int = typer.Option(8, "--cpu", help="vCPUs (fallback when no plan)"),
    ram_mb: int = typer.Option(32768, "--ram-mb", help="RAM in MB (fallback when no plan)"),
    plan_id: str = typer.Option(..., "--plan-id", help="GPU plan ID. See `ibee compute plans`."),
    template_id: str = typer.Option(..., "--template-id", help="OS template/image ID. See `ibee compute images`."),
    disk_gb: Optional[int] = typer.Option(None, "--disk-gb", help="Root disk size in GB"),
    ssh_key_id: Optional[List[str]] = typer.Option(None, "--ssh-key-id", help="SSH key ID to inject (repeatable)"),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag (repeatable)"),
    wait: bool = typer.Option(False, "--wait", help="Poll until the VM is provisioned"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Create a GPU VM (async — returns an operation)."""
    settings = get_settings(ctx)
    wait_config = resolve_wait(wait, timeout, poll_interval)
    key = resolve_idempotency_key(idempotency_key, "gpu-create", name)
    workspace = require_workspace(settings)
    client = get_client(settings)
    preflight_billing(settings, client, workspace, resource_type="gpu_vm")
    create_args = dict(
        workspace_id=workspace,
        idempotency_key=key,
        name=name,
        os_distro=os_distro,
        os_type=os_type,
        cpu=cpu,
        ram_mb=ram_mb,
        gpu_count=gpu_count,
        gpu_model=gpu_model,
        plan_id=plan_id,
        template_id=template_id,
        disk_gb=disk_gb,
        ssh_key_ids=ssh_key_id or None,
        tags=tag or None,
    )
    if site_id is not None:
        create_args["site_id"] = site_id
    result = client.gpu_vms.create_gpu_vm(**create_args)
    finish_operation(
        settings, client, workspace, result, "Create", name, wait_config, idempotency_key=key
    )


@app.command("delete")
@handle_api_errors
def delete_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(..., help="GPU VM ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    wait: bool = typer.Option(False, "--wait", help="Poll until the VM is deleted"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Delete a GPU VM (async — returns an operation)."""
    settings = get_settings(ctx)
    wait_config = resolve_wait(wait, timeout, poll_interval)
    key = resolve_idempotency_key(idempotency_key, "gpu-delete", vm_id)
    confirm_destructive(settings, f"Delete GPU VM '{vm_id}'?", yes)
    workspace = require_workspace(settings)
    client = get_client(settings)
    result = client.gpu_vms.delete_gpu_vm(
        vm_id=vm_id,
        workspace_id=workspace,
        idempotency_key=key,
    )
    finish_operation(
        settings, client, workspace, result, "Delete", vm_id, wait_config, idempotency_key=key
    )


@app.command("metrics")
@handle_api_errors
def gpu_metrics(ctx: typer.Context, vm_id: str = typer.Argument(..., help="GPU VM ID")) -> None:
    """Show current resource-usage metrics for a GPU VM."""
    settings = get_settings(ctx)
    client = get_client(settings)
    # SDK method was renamed get_gpu_vm_metrics -> get_gpu_vm_metrics_overview
    # (ibee 0.2.0); accept either so the CLI works across SDK versions.
    method = getattr(client.gpu_vms, "get_gpu_vm_metrics_overview", None) or client.gpu_vms.get_gpu_vm_metrics
    result = method(workspace_id=require_workspace(settings), vm_id=vm_id)
    print_json(result)


def _power_action(
    ctx: typer.Context,
    vm_id: str,
    action: str,
    wait: bool,
    force: Optional[bool] = None,
    timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
    idempotency_key: Optional[str] = None,
) -> None:
    settings = get_settings(ctx)
    wait_config = resolve_wait(wait, timeout, poll_interval)
    key = resolve_idempotency_key(idempotency_key, action, vm_id)
    workspace = require_workspace(settings)
    client = get_client(settings)
    method = getattr(client.gpu_vms, f"{action}_gpu_vm")
    power_args = dict(
        workspace_id=workspace,
        vm_id=vm_id,
        idempotency_key=key,
    )
    if force is not None:
        power_args["force"] = force
    result = method(**power_args)
    finish_operation(
        settings, client, workspace, result, action.capitalize(), vm_id, wait_config,
        idempotency_key=key,
    )


@app.command("start")
@handle_api_errors
def start_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    force: Optional[bool] = typer.Option(None, "--force/--no-force"),
    wait: bool = typer.Option(False, "--wait", help="Poll until started"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Start a GPU VM."""
    _power_action(ctx, vm_id, "start", wait, force, timeout, poll_interval, idempotency_key)


@app.command("stop")
@handle_api_errors
def stop_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    force: Optional[bool] = typer.Option(None, "--force/--no-force"),
    wait: bool = typer.Option(False, "--wait", help="Poll until stopped"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Stop a GPU VM."""
    _power_action(ctx, vm_id, "stop", wait, force, timeout, poll_interval, idempotency_key)


@app.command("reboot")
@handle_api_errors
def reboot_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    force: Optional[bool] = typer.Option(None, "--force/--no-force"),
    wait: bool = typer.Option(False, "--wait", help="Poll until rebooted"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Reboot a GPU VM."""
    _power_action(ctx, vm_id, "reboot", wait, force, timeout, poll_interval, idempotency_key)
