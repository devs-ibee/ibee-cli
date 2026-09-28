"""Short-lived cloud and GPU VM console sessions."""

from __future__ import annotations

from typing import Optional

import typer

from ..context import get_client, get_settings, require_workspace
from ..helpers import compact_payload, confirm_destructive
from ..render import handle_api_errors, print_json

app = typer.Typer(help="Short-lived VM console sessions", no_args_is_help=True)


@app.command("create")
@handle_api_errors
def create_session(
    ctx: typer.Context,
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
    vm_type: Optional[str] = typer.Option(None, "--vm-type", help="cloud or gpu"),
    console_type: Optional[str] = typer.Option(None, "--console-type", help="graphical"),
    requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    user_id: Optional[str] = typer.Option(None, "--user-id"),
) -> None:
    """Create a session and return its short-lived signed connection URL."""
    if vm_type not in (None, "cloud", "gpu"):
        raise typer.BadParameter("--vm-type must be cloud or gpu.")
    if console_type not in (None, "graphical"):
        raise typer.BadParameter("--console-type must be graphical.")
    settings = get_settings(ctx)
    result = get_client(settings).vm_console.create_vm_console_session(
        workspace_id=require_workspace(settings),
        vm_id=vm_id,
        **compact_payload(
            vm_type=vm_type,
            console_type=console_type,
            requested_by=requested_by,
            user_id=user_id,
        ),
    )
    print_json(result)


@app.command("get")
@handle_api_errors
def get_session(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Console session ID"),
) -> None:
    """Get a workspace-owned console session and its current status."""
    settings = get_settings(ctx)
    result = get_client(settings).vm_console.get_vm_console_session(
        session_id,
        workspace_id=require_workspace(settings),
    )
    print_json(result)


@app.command("close")
@handle_api_errors
def close_session(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Console session ID"),
    reason: Optional[str] = typer.Option(None, "--reason"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Close a console session immediately."""
    confirm_destructive(get_settings(ctx), f"Close console session '{session_id}'?", yes)
    settings = get_settings(ctx)
    result = get_client(settings).vm_console.close_vm_console_session(
        session_id,
        workspace_id=require_workspace(settings),
        **compact_payload(reason=reason),
    )
    print_json(result)
