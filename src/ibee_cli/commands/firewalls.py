"""Firewall group, rule, and VM attachment commands."""

from __future__ import annotations

from typing import List, Optional

import typer
from ibee.validation import validate_limit, validate_offset

from ..context import api_request, get_client, get_settings, require_workspace
from ..helpers import compact_payload, confirm_destructive
from ..render import handle_api_errors, print_json

app = typer.Typer(help="Firewall groups, rules, and VM attachments", no_args_is_help=True)
rules_app = typer.Typer(help="Manage firewall rules", no_args_is_help=True)
attachments_app = typer.Typer(help="Attach firewall groups to VMs", no_args_is_help=True)
app.add_typer(rules_app, name="rules")
app.add_typer(attachments_app, name="attachments")


def _call(
    ctx: typer.Context,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    success: str | None = None,
) -> None:
    result = api_request(get_settings(ctx), method, path, json_body=payload)
    if result is not None:
        print_json(result)
    elif success:
        typer.secho(success, fg=typer.colors.GREEN)


def _group_path(group_id: str) -> str:
    return f"networking/firewall-groups/{group_id}"


def _rule_payload(
    *,
    description: Optional[str],
    direction: Optional[str],
    protocol: Optional[str],
    port_start: Optional[int],
    port_end: Optional[int],
    remote_targets: Optional[List[str]],
    action: Optional[str],
    priority: Optional[int],
    enabled: Optional[bool] = None,
) -> dict:
    return compact_payload(
        description=description,
        direction=direction,
        protocol=protocol,
        port_start=port_start,
        port_end=port_end,
        remote_targets=remote_targets or None,
        action=action,
        priority=priority,
        enabled=enabled,
    )


@app.command("list")
@handle_api_errors
def list_firewall_groups(
    ctx: typer.Context,
    limit: Optional[int] = typer.Option(
        None, "--limit", min=1, max=100, help="Return one page of at most N groups (1-100)"
    ),
    offset: Optional[int] = typer.Option(None, "--offset", min=0, help="Skip N groups (one page)"),
) -> None:
    """List firewall groups in the workspace.

    Without --limit/--offset every page is fetched; with either, one page is returned.
    The paging options are not yet part of the published API contract; behaviour may
    change.
    """

    settings = get_settings(ctx)
    paging = {
        "limit": validate_limit(limit, maximum=100),
        "offset": validate_offset(offset),
    }
    workspace = require_workspace(settings)
    client = get_client(settings)
    result = client.firewalls.list_firewall_groups(
        workspace_id=workspace, **{key: value for key, value in paging.items() if value is not None}
    )
    print_json(result)


@app.command("create")
@handle_api_errors
def create_firewall_group(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Firewall group name"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    is_default: bool = typer.Option(False, "--default"),
) -> None:
    """Create a firewall group."""

    _call(
        ctx,
        "POST",
        "networking/firewall-groups",
        payload=compact_payload(
            name=name, description=description, is_default=is_default
        ),
    )


@app.command("get")
@handle_api_errors
def get_firewall_group(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
) -> None:
    """Show a firewall group and its rules."""

    _call(ctx, "GET", _group_path(group_id))


@app.command("delete")
@handle_api_errors
def delete_firewall_group(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a firewall group."""

    confirm_destructive(get_settings(ctx), f"Delete firewall group '{group_id}'?", yes)
    _call(
        ctx,
        "DELETE",
        _group_path(group_id),
        success=f"Firewall group '{group_id}' deleted.",
    )


@rules_app.command("create")
@handle_api_errors
def create_firewall_rule(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    direction: str = typer.Option("ingress", "--direction", help="ingress or egress"),
    protocol: str = typer.Option("tcp", "--protocol", help="tcp, udp, icmp, or any"),
    port_start: Optional[int] = typer.Option(None, "--port-start", min=1, max=65535),
    port_end: Optional[int] = typer.Option(None, "--port-end", min=1, max=65535),
    remote_target: Optional[List[str]] = typer.Option(
        None, "--remote-target", help="Source/destination CIDR or selector (repeatable)"
    ),
    action: str = typer.Option("allow", "--action", help="allow or drop"),
    priority: Optional[int] = typer.Option(None, "--priority"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
) -> None:
    """Create a rule in a firewall group."""

    _call(
        ctx,
        "POST",
        f"{_group_path(group_id)}/rules",
        payload=_rule_payload(
            description=description,
            direction=direction,
            protocol=protocol,
            port_start=port_start,
            port_end=port_end,
            remote_targets=remote_target,
            action=action,
            priority=priority,
        ),
    )


@rules_app.command("update")
@handle_api_errors
def update_firewall_rule(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    rule_id: str = typer.Argument(..., help="Firewall rule ID"),
    direction: Optional[str] = typer.Option(None, "--direction"),
    protocol: Optional[str] = typer.Option(None, "--protocol"),
    port_start: Optional[int] = typer.Option(None, "--port-start", min=1, max=65535),
    port_end: Optional[int] = typer.Option(None, "--port-end", min=1, max=65535),
    remote_target: Optional[List[str]] = typer.Option(None, "--remote-target"),
    action: Optional[str] = typer.Option(None, "--action"),
    priority: Optional[int] = typer.Option(None, "--priority"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled"),
) -> None:
    """Update a firewall rule."""

    payload = _rule_payload(
        description=description,
        direction=direction,
        protocol=protocol,
        port_start=port_start,
        port_end=port_end,
        remote_targets=remote_target,
        action=action,
        priority=priority,
        enabled=enabled,
    )
    if not payload:
        raise typer.BadParameter("Provide at least one update option.")
    _call(
        ctx,
        "PATCH",
        f"{_group_path(group_id)}/rules/{rule_id}",
        payload=payload,
    )


@rules_app.command("delete")
@handle_api_errors
def delete_firewall_rule(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    rule_id: str = typer.Argument(..., help="Firewall rule ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a firewall rule."""

    confirm_destructive(get_settings(ctx), f"Delete firewall rule '{rule_id}'?", yes)
    _call(
        ctx,
        "DELETE",
        f"{_group_path(group_id)}/rules/{rule_id}",
        success=f"Firewall rule '{rule_id}' deleted.",
    )


@attachments_app.command("list")
@handle_api_errors
def list_firewall_attachments(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
) -> None:
    """List VMs attached to a firewall group."""

    _call(ctx, "GET", f"{_group_path(group_id)}/attachments")


@attachments_app.command("attach")
@handle_api_errors
def attach_firewall_group(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
) -> None:
    """Attach a firewall group to a VM."""

    _call(
        ctx,
        "POST",
        f"{_group_path(group_id)}/attachments",
        payload={"vm_id": vm_id},
    )


@attachments_app.command("detach")
@handle_api_errors
def detach_firewall_group(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Detach a firewall group from a VM."""

    confirm_destructive(get_settings(ctx), f"Detach firewall group '{group_id}' from VM '{vm_id}'?", yes)
    _call(
        ctx,
        "DELETE",
        f"{_group_path(group_id)}/attachments/{vm_id}",
        success=f"Firewall group '{group_id}' detached from VM '{vm_id}'.",
    )
