"""Firewall group, rule, and VM attachment commands.

The Python SDK applies the portal's rules: unique group names (any case), no
customer default groups, tcp/udp rules need a port, icmp/any take none, IPv4
sources only (a bare IP becomes /32, default 0.0.0.0/0), and system-managed rules
cannot be changed.
"""

from __future__ import annotations

from typing import Any, List, Optional

import typer
from ibee.validation import (
    IbeeValidationError,
    check_rule_not_system_managed,
    parse_port_range,
    validate_limit,
    validate_offset,
)

from ..context import get_client, get_settings, require_workspace
from ..helpers import api_hints, check_state_option, confirm_destructive
from ..render import emit, handle_api_errors, print_json

UNCONTRACTED = "Not yet part of the published API contract; behaviour may change."
ID_FIELD = "firewall_group_id"
GROUP_COLUMNS = ("firewall_group_id", "name", "status", "is_default", "linked_instance_count")
SUMMARY_COLUMNS = ("firewall_group_id", "name", "status", "rule_count", "linked_instance_count")
ATTACHMENT_COLUMNS = ("vm_id", "vm_name", "private_ip", "public_ip", "status", "network_provider")
ATTACH_HINT = (
    "Firewall groups attach only to VMs whose network is OVS/OVN-backed and active; "
    "check the VM's network in the portal."
)

app = typer.Typer(help="Firewall groups, rules, and VM attachments", no_args_is_help=True)
rules_app = typer.Typer(help="Manage firewall rules", no_args_is_help=True)
attachments_app = typer.Typer(help="Attach firewall groups to VMs", no_args_is_help=True)
app.add_typer(rules_app, name="rules")
app.add_typer(attachments_app, name="attachments")


def _session(ctx: typer.Context) -> tuple[Any, str, Any]:
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    return settings, workspace, get_client(settings)


def _success(settings: Any, message: str) -> None:
    if not settings.structured_output:
        typer.secho(message, fg=typer.colors.GREEN)


@app.command("list")
@handle_api_errors
def list_firewall_groups(
    ctx: typer.Context,
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Return one page of at most N groups (1-100)"
    ),
    offset: Optional[int] = typer.Option(None, "--offset", help="Skip N groups (one page)"),
    all_pages: bool = typer.Option(False, "--all", help="Fetch every page (the default without --limit/--offset)"),
    summary: bool = typer.Option(
        False, "--summary", help=f"List summaries (rule and VM counts) as the portal does. {UNCONTRACTED}"
    ),
) -> None:
    """List firewall groups in the workspace.

    Without --limit/--offset every page is fetched; with either, one page is returned.
    The paging options are not yet part of the published API contract; behaviour may
    change.
    """

    settings = get_settings(ctx)
    page_limit = validate_limit(limit, maximum=100)
    page_offset = validate_offset(offset)
    if all_pages and (page_limit is not None or page_offset is not None):
        raise IbeeValidationError("--all cannot be combined with --limit or --offset.", code="invalid_all", field="all")
    paging = {key: value for key, value in (("limit", page_limit), ("offset", page_offset)) if value is not None}
    workspace = require_workspace(settings)
    client = get_client(settings)
    if not summary:
        result = client.firewalls.list_firewall_groups(workspace_id=workspace, **paging)
        emit(result, settings=settings, default="json", columns=GROUP_COLUMNS, title="Firewall groups")
        return
    more = False
    if paging:
        wanted = page_limit or 10
        probe = dict(paging, limit=min(wanted + 1, 100))
        result = list(client.firewalls.list_firewall_group_summaries(workspace_id=workspace, **probe))
        if wanted == 100 and len(result) == 100:
            # The API caps limit at 100, so the limit+1 probe cannot see past this page.
            more = bool(list(client.firewalls.list_firewall_group_summaries(
                workspace_id=workspace, limit=1, offset=(page_offset or 0) + 100
            )))
        else:
            more = len(result) > wanted
        result = result[:wanted]
    else:
        result = client.firewalls.list_firewall_group_summaries(workspace_id=workspace)
    emit(result, settings=settings, default="json", columns=SUMMARY_COLUMNS, title="Firewall groups")
    if more:
        next_offset = (page_offset or 0) + len(result)
        typer.secho(f"More results: use --offset {next_offset}", fg=typer.colors.YELLOW, err=True)


@app.command("create")
@handle_api_errors
def create_firewall_group(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Firewall group name (1-120 characters, unique in any case)"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    is_default: bool = typer.Option(
        False, "--default", hidden=True, help="Rejected: default groups are platform-managed"
    ),
    check_state: bool = check_state_option(),
) -> None:
    """Create a firewall group (it starts with the platform's baseline rules)."""

    _settings, workspace, client = _session(ctx)
    group = client.firewalls.create_firewall_group(
        workspace_id=workspace,
        name=name,
        description=description,
        is_default=True if is_default else None,
        check_state=check_state,
    )
    print_json(group, id_field=ID_FIELD)


@app.command("get")
@handle_api_errors
def get_firewall_group(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
) -> None:
    """Show a firewall group and its rules."""

    _settings, workspace, client = _session(ctx)
    print_json(client.firewalls.get_firewall_group(group_id, workspace_id=workspace), id_field=ID_FIELD)


@app.command("delete")
@handle_api_errors
def delete_firewall_group(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a firewall group. Attached VMs are detached and get the default group back."""

    settings, workspace, client = _session(ctx)
    confirm_destructive(settings, f"Delete firewall group '{group_id}'? Attached VMs get the default group.", yes)
    client.firewalls.delete_firewall_group(group_id, workspace_id=workspace)
    _success(settings, f"Firewall group '{group_id}' deleted.")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _ports(port: Optional[str], port_start: Optional[int], port_end: Optional[int]) -> tuple[Any, Any]:
    if port is None:
        return port_start, port_end
    if port_start is not None or port_end is not None:
        raise IbeeValidationError("Use --port or --port-start/--port-end, not both.", code="invalid_port", field="port")
    return parse_port_range(port)


def _sources(values: Optional[List[str]]) -> Optional[List[str]]:
    if not values:
        return None
    items: list[str] = []
    for value in values:
        items.extend(value.split(","))
    return items


PORT_HELP = "Single port like 22 or a range like 8000-8080 (tcp/udp only)"
SOURCE_HELP = "IPv4 address or CIDR, comma-separated or repeatable (default 0.0.0.0/0, anywhere)"


@rules_app.command("create")
@handle_api_errors
def create_firewall_rule(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    direction: str = typer.Option(
        "ingress", "--direction", help="ingress or egress (the portal manages ingress rules)"
    ),
    protocol: str = typer.Option("tcp", "--protocol", help="tcp, udp, icmp, or any"),
    port: Optional[str] = typer.Option(None, "--port", help=PORT_HELP),
    port_start: Optional[int] = typer.Option(None, "--port-start", help="First port (1-65535)"),
    port_end: Optional[int] = typer.Option(None, "--port-end", help="Last port (default: --port-start)"),
    remote_target: Optional[List[str]] = typer.Option(
        None, "--source", "--remote-target", help=SOURCE_HELP
    ),
    action: str = typer.Option("allow", "--action", help="allow or drop"),
    priority: Optional[int] = typer.Option(None, "--priority"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
) -> None:
    """Add a rule to a firewall group."""

    _settings, workspace, client = _session(ctx)
    start, end = _ports(port, port_start, port_end)
    group = client.firewalls.create_firewall_rule(
        group_id,
        workspace_id=workspace,
        description=description,
        direction=direction,
        protocol=protocol,
        port_start=start,
        port_end=end,
        remote_targets=_sources(remote_target),
        action=action,
        priority=priority,
    )
    print_json(group, id_field=ID_FIELD)


@rules_app.command("update")
@handle_api_errors
def update_firewall_rule(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    rule_id: str = typer.Argument(..., help="Firewall rule ID"),
    direction: Optional[str] = typer.Option(None, "--direction", help="ingress or egress"),
    protocol: Optional[str] = typer.Option(
        None, "--protocol", help="tcp, udp, icmp, or any (tcp/udp need --port)"
    ),
    port: Optional[str] = typer.Option(None, "--port", help=PORT_HELP),
    port_start: Optional[int] = typer.Option(None, "--port-start"),
    port_end: Optional[int] = typer.Option(None, "--port-end"),
    remote_target: Optional[List[str]] = typer.Option(None, "--source", "--remote-target", help=SOURCE_HELP),
    action: Optional[str] = typer.Option(None, "--action", help="allow or drop"),
    priority: Optional[int] = typer.Option(None, "--priority"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled"),
    check_state: bool = check_state_option(),
) -> None:
    """Update a firewall rule (at least one option; system-managed rules cannot change)."""

    _settings, workspace, client = _session(ctx)
    start, end = _ports(port, port_start, port_end)
    group = client.firewalls.update_firewall_rule(
        group_id,
        rule_id,
        workspace_id=workspace,
        enabled=enabled,
        description=description,
        direction=direction,
        protocol=protocol,
        port_start=start,
        port_end=end,
        remote_targets=_sources(remote_target),
        action=action,
        priority=priority,
        check_state=check_state,
    )
    print_json(group, id_field=ID_FIELD)


@rules_app.command("delete")
@handle_api_errors
def delete_firewall_rule(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    rule_id: str = typer.Argument(..., help="Firewall rule ID"),
    check_state: bool = check_state_option(),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a firewall rule (system-managed rules cannot be removed)."""

    settings, workspace, client = _session(ctx)
    if check_state:
        group = client.firewalls.get_firewall_group(group_id, workspace_id=workspace)
        check_rule_not_system_managed(group, rule_id, deleting=True)
    confirm_destructive(settings, f"Delete firewall rule '{rule_id}'?", yes)
    group = client.firewalls.delete_firewall_rule(group_id, rule_id, workspace_id=workspace, check_state=False)
    print_json(group, id_field=ID_FIELD)


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


@attachments_app.command("list")
@handle_api_errors
def list_firewall_attachments(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    limit: Optional[int] = typer.Option(None, "--limit", help="At most N VMs (1-500; default 500)"),
    skip: Optional[int] = typer.Option(None, "--skip", help="Skip N VMs"),
) -> None:
    """List VMs attached to a firewall group."""

    settings, workspace, client = _session(ctx)
    items = client.firewalls.list_firewall_group_attachments(
        group_id, workspace_id=workspace, limit=limit, skip=skip
    )
    emit(items, settings=settings, default="json", columns=ATTACHMENT_COLUMNS, title="Attached VMs", id_field="vm_id")


@attachments_app.command("attach")
@handle_api_errors
def attach_firewall_group(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
) -> None:
    """Attach a firewall group to a VM (it replaces the VM's current custom group)."""

    _settings, workspace, client = _session(ctx)
    with api_hints({400: ATTACH_HINT}):
        group = client.firewalls.attach_firewall_group(group_id, workspace_id=workspace, vm_id=vm_id)
    print_json(group, id_field=ID_FIELD)


@attachments_app.command("detach")
@handle_api_errors
def detach_firewall_group(
    ctx: typer.Context,
    group_id: str = typer.Argument(..., help="Firewall group ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Detach a firewall group from a VM (the VM gets the default group back)."""

    settings, workspace, client = _session(ctx)
    confirm_destructive(settings, f"Detach firewall group '{group_id}' from VM '{vm_id}'?", yes)
    group = client.firewalls.detach_firewall_group(group_id, vm_id, workspace_id=workspace)
    print_json(group, id_field=ID_FIELD)
