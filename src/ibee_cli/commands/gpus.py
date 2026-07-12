"""GPU VM commands."""

from __future__ import annotations

import typer

from ..context import get_client, get_settings, require_workspace
from ..render import handle_api_errors, print_json, print_table

app = typer.Typer(help="GPU VMs", no_args_is_help=True)


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
    vms = getattr(result, "items", None) or getattr(result, "vms", None) or []
    print_table(
        "GPU VMs",
        ["ID", "Name", "Status", "CPU", "RAM (MB)", "Public IP"],
        [
            (v.id, v.name, getattr(v.status, "value", v.status), v.cpu, v.ram_mb, v.public_ip)
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
