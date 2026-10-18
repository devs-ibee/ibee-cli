"""Async compute operations (cloud and GPU VMs)."""

from __future__ import annotations

import typer

from ..context import get_client, get_settings
from ..helpers import finish_operation, poll_interval_option, timeout_option, wait_option
from ..render import cell, emit, handle_api_errors

OPERATION_COLUMNS = ("operation_id", "status", "action", "vm_id", "current_step", "error_code", "error_message")

app = typer.Typer(help="Async compute operations for cloud and GPU VMs", no_args_is_help=True)


def _wait(ctx: typer.Context, operation_id: str, timeout: float, poll_interval: float) -> None:
    settings = get_settings(ctx)
    if settings.output is None:
        settings.output = "json"
    finish_operation(settings, get_client(settings), {"operation_id": operation_id}, "Operation", operation_id, True, timeout, poll_interval)


@app.command("get")
@handle_api_errors
def get_operation(
    ctx: typer.Context,
    operation_id: str = typer.Argument(..., help="Operation ID returned by an asynchronous VM action"),
    wait: bool = wait_option("the operation reaches a terminal state"),
    timeout: float = timeout_option(),
    poll_interval: float = poll_interval_option(),
) -> None:
    """Show an async compute operation."""
    if wait:
        _wait(ctx, operation_id, timeout, poll_interval)
        return
    settings = get_settings(ctx)
    result = get_client(settings).cloud_vms.get_compute_operation(operation_id)
    emit(
        result, settings=settings, default="json", title="Operation", headers=OPERATION_COLUMNS,
        rows=[[cell(result, column) for column in OPERATION_COLUMNS]], id_field="operation_id",
    )


@app.command("wait")
@handle_api_errors
def wait_operation(
    ctx: typer.Context,
    operation_id: str = typer.Argument(..., help="Operation ID returned by an asynchronous VM action"),
    timeout: float = timeout_option(),
    poll_interval: float = poll_interval_option(),
) -> None:
    """Wait for an operation: exit 0 when it succeeds, 1 when it fails, 3 when --timeout passes first."""
    _wait(ctx, operation_id, timeout, poll_interval)
