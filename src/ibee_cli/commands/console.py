"""Short-lived VM console sessions."""

from __future__ import annotations

from typing import Optional

import typer

from ..context import get_client, get_settings
from ..helpers import compact_payload, confirm_destructive
from ..render import handle_api_errors, print_json, to_data

HIDDEN_URL = "<hidden: use --show-url or --json>"

app = typer.Typer(help="Short-lived VM console sessions", no_args_is_help=True)


@app.command("create")
@handle_api_errors
def create_session(
    ctx: typer.Context,
    vm_id: str = typer.Argument(..., help="VM ID"),
    vm_type: Optional[str] = typer.Option(None, "--vm-type", help="cloud or gpu"),
    console_type: Optional[str] = typer.Option(None, "--console-type", help="For example graphical"),
    requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    user_id: Optional[str] = typer.Option(None, "--user-id"),
    show_url: bool = typer.Option(False, "--show-url", help="Print connect_url (it carries a short-lived token)"),
) -> None:
    """Create a console session.

    connect_url carries a short-lived token, so it is hidden unless you pass --show-url or
    ask for machine-readable output (-o json|yaml|id). Treat it as a secret.
    """
    settings = get_settings(ctx)
    result = get_client(settings).vm_console.create_vm_console_session(
        vm_id=vm_id, **compact_payload(vm_type=vm_type, console_type=console_type, requested_by=requested_by, user_id=user_id)
    )
    data = to_data(result)
    if not (show_url or settings.structured_output) and isinstance(data, dict) and data.get("connect_url"):
        data = {**data, "connect_url": HIDDEN_URL}
        typer.secho("connect_url is a secret; pass --show-url to print it.", fg=typer.colors.YELLOW, err=True)
    print_json(data)


@app.command("get")
@handle_api_errors
def get_session(ctx: typer.Context, session_id: str = typer.Argument(..., help="Console session ID")) -> None:
    """Get a console session."""
    print_json(get_client(get_settings(ctx)).vm_console.get_vm_console_session(session_id))


@app.command("close")
@handle_api_errors
def close_session(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Console session ID"),
    reason: Optional[str] = typer.Option(None, "--reason"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Close a console session."""
    settings = get_settings(ctx)
    confirm_destructive(settings, f"Close console session '{session_id}'?", yes)
    print_json(get_client(settings).vm_console.close_vm_console_session(session_id, **compact_payload(reason=reason)))
