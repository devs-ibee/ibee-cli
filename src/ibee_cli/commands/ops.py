"""Compute operation (async task) commands."""

from __future__ import annotations

from typing import Optional

import typer
from ibee.validation import validate_operation_id

from ..context import get_client, get_settings, require_workspace
from ..helpers import (
    WaitConfig,
    finish_operation,
    poll_interval_option,
    resolve_wait,
    timeout_option,
)
from ..render import handle_api_errors, print_json

app = typer.Typer(help="Async compute operations for cloud and GPU VMs", no_args_is_help=True)


def _wait_for(ctx: typer.Context, operation_id: str, config: WaitConfig) -> None:
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    client = get_client(settings)
    op_id = validate_operation_id(operation_id)
    # Output is JSON by default (the 0.3.0 `ops get` behaviour) unless -o is given.
    if settings.output is None:
        settings.output = "json"
    finish_operation(
        settings,
        client,
        workspace,
        {"operation_id": op_id},
        "Operation",
        op_id,
        config,
    )


@app.command("get")
@handle_api_errors
def get_operation(
    ctx: typer.Context,
    operation_id: str = typer.Argument(..., help="Operation ID returned by an asynchronous VM action"),
    wait: bool = typer.Option(False, "--wait", help="Poll until the operation reaches a terminal state"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
) -> None:
    """Show the status of an async compute operation (cloud or GPU VM)."""
    config = resolve_wait(wait, timeout, poll_interval)
    if config is not None:
        _wait_for(ctx, operation_id, config)
        return
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    client = get_client(settings)
    result = client.cloud_vms.get_compute_operation(
        operation_id=validate_operation_id(operation_id), workspace_id=workspace
    )
    print_json(result)


@app.command("wait")
@handle_api_errors
def wait_operation(
    ctx: typer.Context,
    operation_id: str = typer.Argument(..., help="Operation ID returned by an asynchronous VM action"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
) -> None:
    """Wait for an async compute operation to finish.

    Exits 0 when it succeeds, 1 when it fails, is cancelled or times out on the
    server, and 3 when --timeout passes first (run the command again to resume).
    """
    config = resolve_wait(True, timeout, poll_interval, require_wait=False)
    assert config is not None
    _wait_for(ctx, operation_id, config)
