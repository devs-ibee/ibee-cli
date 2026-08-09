"""Compute operation (async task) commands."""

from __future__ import annotations

import typer

from ..context import get_client, get_settings, require_workspace
from ..helpers import wait_for_operation
from ..render import handle_api_errors, print_json

app = typer.Typer(help="Async compute operations for cloud and GPU VMs", no_args_is_help=True)


@app.command("get")
@handle_api_errors
def get_operation(
    ctx: typer.Context,
    operation_id: str = typer.Argument(..., help="Operation ID returned by an asynchronous VM action"),
    wait: bool = typer.Option(False, "--wait", help="Poll until the operation reaches a terminal state"),
) -> None:
    """Show the status of an async compute operation (cloud or GPU VM)."""
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    client = get_client(settings)
    if wait:
        result = wait_for_operation(client, workspace, operation_id)
    else:
        result = client.cloud_vms.get_compute_operation(
            operation_id=operation_id, workspace_id=workspace
        )
    print_json(result)
