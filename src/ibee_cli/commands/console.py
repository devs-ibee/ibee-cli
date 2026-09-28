"""Short-lived cloud and GPU VM console sessions."""

from __future__ import annotations

from typing import Optional

import typer

from ..context import get_client, get_settings, require_workspace
from ibee.validation import validate_console_target

from ..helpers import check_state_option, compact_payload, confirm_destructive
from ..render import handle_api_errors, print_json, to_data

HIDDEN_URL = "<hidden: use --show-url or --json>"

app = typer.Typer(help="Short-lived VM console sessions", no_args_is_help=True)


@app.command("create")
@handle_api_errors
def create_session(
    ctx: typer.Context,
    vm_id: str = typer.Argument(..., help="Cloud VM ID"),
    vm_type: Optional[str] = typer.Option(
        None, "--vm-type", help="cloud (console sessions are available for cloud VMs only)"
    ),
    console_type: Optional[str] = typer.Option(None, "--console-type", help="graphical"),
    requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    user_id: Optional[str] = typer.Option(None, "--user-id"),
    check_state: bool = check_state_option(),
    show_url: bool = typer.Option(
        False,
        "--show-url",
        help="Print connect_url (it carries a short-lived token); -o json|yaml|id always include it",
    ),
) -> None:
    """Create a session for a running cloud VM.

    The signed connect_url carries a short-lived token, so it is hidden unless you pass
    --show-url or ask for machine-readable output (--json / -o json|yaml|id). Treat it
    as a secret and do not log it.
    """
    if vm_type not in (None, "cloud", "gpu"):
        raise typer.BadParameter("--vm-type must be cloud.")
    if console_type not in (None, "graphical"):
        raise typer.BadParameter("--console-type must be graphical.")
    validate_console_target(vm_type, console_type)
    settings = get_settings(ctx)
    result = get_client(settings).vm_console.create_vm_console_session(
        workspace_id=require_workspace(settings),
        vm_id=vm_id,
        check_state=bool(check_state),
        **compact_payload(
            vm_type=vm_type,
            console_type=console_type,
            requested_by=requested_by,
            user_id=user_id,
        ),
    )
    if show_url or settings.structured_output:
        print_json(result)
        return
    data = to_data(result)
    if isinstance(data, dict) and data.get("connect_url"):
        data = {**data, "connect_url": HIDDEN_URL}
        typer.secho(
            "connect_url is a secret (it carries a short-lived token); pass --show-url to print it.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    print_json(data)


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
