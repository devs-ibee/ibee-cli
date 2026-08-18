"""GPU VM commands."""

from __future__ import annotations

from typing import List, Optional

import typer

from ..context import get_client, get_settings, require_workspace
from ..helpers import (
    finish_operation,
    new_idempotency_key,
    response_items,
)
from ..render import handle_api_errors, print_json, print_table
from .vm_lifecycle import GPU_VM, register_vm_lifecycle

app = typer.Typer(help="GPU VMs", no_args_is_help=True)
register_vm_lifecycle(app, GPU_VM)


@app.command("list")
@handle_api_errors
def list_gpu_vms(ctx: typer.Context) -> None:
    """List GPU VMs in the workspace."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.gpu_vms.list_gpu_vms(workspace_id=require_workspace(settings))
    if settings.as_json:
        print_json(result)
        return
    vms = response_items(result, "items", "vms")
    print_table(
        "GPU VMs",
        ["ID", "Name", "Status", "GPU", "GPUs", "CPU", "RAM (MB)", "Public IP"],
        [
            (
                v.id, v.name, getattr(v.status, "value", v.status),
                getattr(v, "gpu_model", None), getattr(v, "gpu_count", None),
                v.cpu, v.ram_mb, v.public_ip,
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
) -> None:
    """Create a GPU VM (async — returns an operation)."""
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    client = get_client(settings)
    create_args = dict(
        workspace_id=workspace,
        idempotency_key=new_idempotency_key("gpu-create", name),
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
    finish_operation(settings, client, workspace, result, "Create", name, wait)


@app.command("delete")
@handle_api_errors
def delete_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(..., help="GPU VM ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    wait: bool = typer.Option(False, "--wait", help="Poll until the VM is deleted"),
) -> None:
    """Delete a GPU VM (async — returns an operation)."""
    settings = get_settings(ctx)
    if not yes:
        typer.confirm(f"Delete GPU VM '{vm_id}'?", abort=True)
    workspace = require_workspace(settings)
    client = get_client(settings)
    result = client.gpu_vms.delete_gpu_vm(
        vm_id=vm_id,
        workspace_id=workspace,
        idempotency_key=new_idempotency_key("gpu-delete", vm_id),
    )
    finish_operation(settings, client, workspace, result, "Delete", vm_id, wait)


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
) -> None:
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    client = get_client(settings)
    method = getattr(client.gpu_vms, f"{action}_gpu_vm")
    power_args = dict(
        workspace_id=workspace,
        vm_id=vm_id,
        idempotency_key=new_idempotency_key(action, vm_id),
    )
    if force is not None:
        power_args["force"] = force
    result = method(**power_args)
    finish_operation(settings, client, workspace, result, action.capitalize(), vm_id, wait)


@app.command("start")
@handle_api_errors
def start_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    force: Optional[bool] = typer.Option(None, "--force/--no-force"),
    wait: bool = typer.Option(False, "--wait", help="Poll until started"),
) -> None:
    """Start a GPU VM."""
    _power_action(ctx, vm_id, "start", wait, force)


@app.command("stop")
@handle_api_errors
def stop_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    force: Optional[bool] = typer.Option(None, "--force/--no-force"),
    wait: bool = typer.Option(False, "--wait", help="Poll until stopped"),
) -> None:
    """Stop a GPU VM."""
    _power_action(ctx, vm_id, "stop", wait, force)


@app.command("reboot")
@handle_api_errors
def reboot_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    force: Optional[bool] = typer.Option(None, "--force/--no-force"),
    wait: bool = typer.Option(False, "--wait", help="Poll until rebooted"),
) -> None:
    """Reboot a GPU VM."""
    _power_action(ctx, vm_id, "reboot", wait, force)
