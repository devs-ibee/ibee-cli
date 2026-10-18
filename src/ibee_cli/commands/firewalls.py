"""Firewall group, rule and VM attachment commands.

Each command makes one SDK call and prints the server's answer; the server decides
names, protocols, ports, sources and which rules may change. ``list`` returns every
page unless ``--limit`` or ``--offset`` is given (the SDK collects the pages).
"""

from __future__ import annotations

from typing import Any, List, Optional

import typer

from ..context import get_client, get_settings
from ..helpers import compact_payload, confirm_destructive
from ..render import emit, handle_api_errors, print_json

ID_FIELD = "firewall_group_id"
GROUP_COLUMNS = ("firewall_group_id", "name", "status", "is_default", "linked_instance_count")
ATTACHMENT_COLUMNS = ("vm_id", "vm_name", "private_ip", "public_ip", "status", "network_provider")
PORT_HELP = "A port like 22 or a range like 8000-8080 (same as --port-start/--port-end)"
SOURCE_HELP = "Source address or CIDR (comma-separated or repeatable)"

app = typer.Typer(help="Firewall groups, rules and VM attachments", no_args_is_help=True)
rules_app = typer.Typer(help="Manage firewall rules", no_args_is_help=True)
attachments_app = typer.Typer(help="Attach firewall groups to VMs", no_args_is_help=True)
app.add_typer(rules_app, name="rules")
app.add_typer(attachments_app, name="attachments")


def _firewalls(ctx: typer.Context) -> tuple[Any, Any]:
    settings = get_settings(ctx)
    return settings, get_client(settings).firewalls


def _yes() -> Any:
    return typer.Option(False, "--yes", "-y", help="Skip confirmation")


@app.command("list")
@handle_api_errors
def list_firewall_groups(
    ctx: typer.Context,
    limit: Optional[int] = typer.Option(None, "--limit", help="Return one page of at most N groups"),
    offset: Optional[int] = typer.Option(None, "--offset", help="Skip N groups (one page)"),
    all_pages: bool = typer.Option(False, "--all", help="Fetch every page (the default without --limit/--offset)"),
) -> None:
    """List firewall groups in the workspace."""
    if all_pages and (limit is not None or offset is not None):
        raise typer.BadParameter("--all cannot be combined with --limit or --offset.")
    settings, firewalls = _firewalls(ctx)
    groups = firewalls.list_firewall_groups(**compact_payload(limit=limit, offset=offset))
    emit(groups, settings=settings, default="json", columns=GROUP_COLUMNS, title="Firewall groups", id_field=ID_FIELD)


@app.command("create")
@handle_api_errors
def create_firewall_group(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Firewall group name"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    is_default: bool = typer.Option(False, "--default", help="Send is_default=true"),
) -> None:
    """Create a firewall group."""
    group = _firewalls(ctx)[1].create_firewall_group(
        name=name, **compact_payload(description=description, is_default=True if is_default else None)
    )
    print_json(group, id_field=ID_FIELD)


@app.command("get")
@handle_api_errors
def get_firewall_group(ctx: typer.Context, group_id: str = typer.Argument(..., help="Firewall group ID")) -> None:
    """Show a firewall group and its rules."""
    print_json(_firewalls(ctx)[1].get_firewall_group(group_id), id_field=ID_FIELD)


@app.command("delete")
@handle_api_errors
def delete_firewall_group(ctx: typer.Context, group_id: str = typer.Argument(..., help="Firewall group ID"), yes: bool = _yes()) -> None:
    """Delete a firewall group."""
    settings, firewalls = _firewalls(ctx)
    confirm_destructive(settings, f"Delete firewall group '{group_id}'?", yes)
    firewalls.delete_firewall_group(group_id)
    if not settings.structured_output:
        typer.secho(f"Firewall group '{group_id}' deleted.", fg=typer.colors.GREEN)


# -- rules --------------------------------------------------------------------------


def _int(text: str, raw: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        raise typer.BadParameter(f"--port expects N or N-M, got {raw!r}.") from None


def _ports(port: Optional[str], port_start: Optional[int], port_end: Optional[int]) -> dict:
    """``--port N`` or ``--port N-M`` read as port_start/port_end (sent as typed)."""
    if port is None:
        return compact_payload(port_start=port_start, port_end=port_end)
    if port_start is not None or port_end is not None:
        raise typer.BadParameter("Use --port or --port-start/--port-end, not both.")
    start, sep, end = port.partition("-")
    return {"port_start": _int(start, port), **({"port_end": _int(end, port)} if sep else {})}


def _sources(values: Optional[List[str]]) -> Optional[List[str]]:
    return [item for value in values for item in value.split(",")] if values else None


def _rule_fields(
    port: Optional[str], port_start: Optional[int], port_end: Optional[int], sources: Optional[List[str]], **fields: Any
) -> dict:
    return {**compact_payload(remote_targets=_sources(sources), **fields), **_ports(port, port_start, port_end)}


@rules_app.command("create")
@handle_api_errors
def create_firewall_rule(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    direction: Optional[str] = typer.Option(None, "--direction", help="For example ingress or egress"),
    protocol: Optional[str] = typer.Option(None, "--protocol", help="For example tcp, udp, icmp or any"),
    port: Optional[str] = typer.Option(None, "--port", help=PORT_HELP),
    port_start: Optional[int] = typer.Option(None, "--port-start"),
    port_end: Optional[int] = typer.Option(None, "--port-end"),
    remote_target: Optional[List[str]] = typer.Option(None, "--source", "--remote-target", help=SOURCE_HELP),
    action: Optional[str] = typer.Option(None, "--action", help="For example allow or drop"),
    priority: Optional[int] = typer.Option(None, "--priority"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
) -> None:
    """Add a rule to a firewall group."""
    fields = _rule_fields(
        port, port_start, port_end, remote_target,
        direction=direction, protocol=protocol, action=action, priority=priority, description=description,
    )
    print_json(_firewalls(ctx)[1].create_firewall_rule(group_id, **fields), id_field=ID_FIELD)


@rules_app.command("update")
@handle_api_errors
def update_firewall_rule(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    rule_id: str = typer.Argument(..., help="Firewall rule ID"),
    direction: Optional[str] = typer.Option(None, "--direction"),
    protocol: Optional[str] = typer.Option(None, "--protocol"),
    port: Optional[str] = typer.Option(None, "--port", help=PORT_HELP),
    port_start: Optional[int] = typer.Option(None, "--port-start"),
    port_end: Optional[int] = typer.Option(None, "--port-end"),
    remote_target: Optional[List[str]] = typer.Option(None, "--source", "--remote-target", help=SOURCE_HELP),
    action: Optional[str] = typer.Option(None, "--action"),
    priority: Optional[int] = typer.Option(None, "--priority"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled", show_default=False),
) -> None:
    """Update a firewall rule."""
    fields = _rule_fields(
        port, port_start, port_end, remote_target, direction=direction, protocol=protocol, action=action,
        priority=priority, description=description, enabled=enabled,
    )
    print_json(_firewalls(ctx)[1].update_firewall_rule(group_id, rule_id, **fields), id_field=ID_FIELD)


@rules_app.command("delete")
@handle_api_errors
def delete_firewall_rule(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    rule_id: str = typer.Argument(..., help="Firewall rule ID"),
    yes: bool = _yes(),
) -> None:
    """Delete a firewall rule."""
    settings, firewalls = _firewalls(ctx)
    confirm_destructive(settings, f"Delete firewall rule '{rule_id}'?", yes)
    print_json(firewalls.delete_firewall_rule(group_id, rule_id), id_field=ID_FIELD)


# -- attachments --------------------------------------------------------------------


@attachments_app.command("list")
@handle_api_errors
def list_firewall_attachments(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    limit: Optional[int] = typer.Option(None, "--limit", help="At most N VMs"),
    skip: Optional[int] = typer.Option(None, "--skip", help="Skip N VMs"),
) -> None:
    """List VMs attached to a firewall group."""
    settings, firewalls = _firewalls(ctx)
    items = firewalls.list_firewall_group_attachments(group_id, **compact_payload(limit=limit, skip=skip))
    emit(items, settings=settings, default="json", columns=ATTACHMENT_COLUMNS, title="Attached VMs", id_field="vm_id")


@attachments_app.command("attach")
@handle_api_errors
def attach_firewall_group(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
) -> None:
    """Attach a firewall group to a VM."""
    print_json(_firewalls(ctx)[1].attach_firewall_group(group_id, vm_id=vm_id), id_field=ID_FIELD)


@attachments_app.command("detach")
@handle_api_errors
def detach_firewall_group(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
    yes: bool = _yes(),
) -> None:
    """Detach a firewall group from a VM."""
    settings, firewalls = _firewalls(ctx)
    confirm_destructive(settings, f"Detach firewall group '{group_id}' from VM '{vm_id}'?", yes)
    print_json(firewalls.detach_firewall_group(group_id, vm_id), id_field=ID_FIELD)
