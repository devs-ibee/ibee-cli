"""Cloud VM commands."""

from __future__ import annotations

from typing import List, Optional

import typer

from ..context import get_client, get_settings, require_workspace
from ..helpers import (
    finish_operation,
    new_idempotency_key,
    preflight_compute_plan,
    response_items,
)
from ..render import handle_api_errors, print_json, print_table

app = typer.Typer(help="Cloud VMs", no_args_is_help=True)


@app.command("list")
@handle_api_errors
def list_vms(ctx: typer.Context) -> None:
    """List cloud VMs in the workspace."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.cloud_vms.list_cloud_vms(workspace_id=require_workspace(settings))
    if settings.as_json:
        print_json(result)
        return
    vms = response_items(result, "items", "vms")
    print_table(
        "Cloud VMs",
        ["ID", "Name", "Status", "CPU", "RAM (MB)", "Public IP"],
        [
            (v.id, v.name, getattr(v.status, "value", v.status), v.cpu, v.ram_mb, v.public_ip)
            for v in vms
        ],
    )


@app.command("get")
@handle_api_errors
def get_vm(ctx: typer.Context, vm_id: str = typer.Argument(..., help="VM ID")) -> None:
    """Show one cloud VM."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.cloud_vms.get_cloud_vm(workspace_id=require_workspace(settings), vm_id=vm_id)
    print_json(result)


@app.command("create")
@handle_api_errors
def create_vm(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Display name for the VM"),
    site_id: Optional[str] = typer.Option(
        None,
        "--site-id",
        help="Optional placement site; omit for automatic placement",
    ),
    os_distro: str = typer.Option("ubuntu", "--os-distro", help="OS distribution (ubuntu, debian, rocky, windows)"),
    os_type: str = typer.Option("linux", "--os-type", help="OS family (linux, windows)"),
    cpu: int = typer.Option(2, "--cpu", help="vCPUs (fallback when no plan)"),
    ram_mb: int = typer.Option(4096, "--ram-mb", help="RAM in MB (fallback when no plan)"),
    plan_id: str = typer.Option(..., "--plan-id", help="Plan ID. See `ibee compute plans`."),
    template_id: str = typer.Option(..., "--template-id", help="OS template/image ID. See `ibee compute images`."),
    disk_gb: Optional[int] = typer.Option(None, "--disk-gb", help="Root disk size in GB"),
    ssh_key_id: Optional[List[str]] = typer.Option(None, "--ssh-key-id", help="SSH key ID to inject (repeatable)"),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag (repeatable)"),
    wait: bool = typer.Option(False, "--wait", help="Poll until the VM is provisioned"),
) -> None:
    """Create a cloud VM (async — returns an operation)."""
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    client = get_client(settings)
    preflight_compute_plan(
        client,
        workspace,
        vm_type="cloud",
        site_id=site_id,
        plan_id=plan_id,
    )
    create_args = dict(
        workspace_id=workspace,
        idempotency_key=new_idempotency_key("vm-create", name),
        name=name,
        os_distro=os_distro,
        os_type=os_type,
        cpu=cpu,
        ram_mb=ram_mb,
        plan_id=plan_id,
        template_id=template_id,
        disk_gb=disk_gb,
        ssh_key_ids=ssh_key_id or None,
        tags=tag or None,
    )
    if site_id is not None:
        create_args["site_id"] = site_id
    result = client.cloud_vms.create_cloud_vm(**create_args)
    finish_operation(settings, client, workspace, result, "Create", name, wait)


@app.command("delete")
@handle_api_errors
def delete_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(..., help="VM ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    wait: bool = typer.Option(False, "--wait", help="Poll until the VM is deleted"),
) -> None:
    """Delete a cloud VM (async — returns an operation)."""
    settings = get_settings(ctx)
    if not yes:
        typer.confirm(f"Delete cloud VM '{vm_id}'?", abort=True)
    workspace = require_workspace(settings)
    client = get_client(settings)
    result = client.cloud_vms.delete_cloud_vm(
        vm_id=vm_id,
        workspace_id=workspace,
        idempotency_key=new_idempotency_key("vm-delete", vm_id),
    )
    finish_operation(settings, client, workspace, result, "Delete", vm_id, wait)


@app.command("metrics")
@handle_api_errors
def vm_metrics(ctx: typer.Context, vm_id: str = typer.Argument(..., help="VM ID")) -> None:
    """Show current resource-usage metrics for a cloud VM."""
    settings = get_settings(ctx)
    client = get_client(settings)
    # SDK method was renamed get_cloud_vm_metrics -> get_cloud_vm_metrics_overview
    # (ibee 0.2.0); accept either so the CLI works across SDK versions.
    method = getattr(client.cloud_vms, "get_cloud_vm_metrics_overview", None) or client.cloud_vms.get_cloud_vm_metrics
    result = method(workspace_id=require_workspace(settings), vm_id=vm_id)
    print_json(result)


def _power_action(ctx: typer.Context, vm_id: str, action: str, wait: bool) -> None:
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    client = get_client(settings)
    method = getattr(client.cloud_vms, f"{action}_cloud_vm")
    result = method(
        workspace_id=workspace,
        vm_id=vm_id,
        idempotency_key=new_idempotency_key(action, vm_id),
    )
    finish_operation(settings, client, workspace, result, action.capitalize(), vm_id, wait)


@app.command("start")
@handle_api_errors
def start_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    wait: bool = typer.Option(False, "--wait", help="Poll until started"),
) -> None:
    """Start a cloud VM."""
    _power_action(ctx, vm_id, "start", wait)


@app.command("stop")
@handle_api_errors
def stop_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    wait: bool = typer.Option(False, "--wait", help="Poll until stopped"),
) -> None:
    """Stop a cloud VM."""
    _power_action(ctx, vm_id, "stop", wait)


@app.command("reboot")
@handle_api_errors
def reboot_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    wait: bool = typer.Option(False, "--wait", help="Poll until rebooted"),
) -> None:
    """Reboot a cloud VM."""
    _power_action(ctx, vm_id, "reboot", wait)
