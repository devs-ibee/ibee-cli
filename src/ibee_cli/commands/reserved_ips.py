"""Reserved public IP commands.

The Python SDK applies the portal's rules: label and reverse-DNS formats, no release
while attached, attach versus move, no detach of a converted address that is still
the VM's active IP, and upstream admission for conversions.
"""

from __future__ import annotations

from typing import Any, Optional

import typer
from ibee.errors import ForbiddenError
from ibee.validation import check_reserved_ip_releasable

from ..context import get_client, get_settings, require_workspace
from ..helpers import check_state_option, confirm_destructive, load_json_input
from ..render import emit, handle_api_errors, print_json

UNCONTRACTED = "Not yet part of the published API contract; behaviour may change."
ID_FIELD = "public_ip_id"
COLUMNS = ("public_ip_id", "address", "site_id", "label", "status", "attached_resource_type", "attached_resource_id")
CATALOG_HELP = (
    "RESERVED-IP billing catalog object (sku_id and sku_code required). Copy it from your IBEE "
    f"pricing; there is no public pricing route yet. {UNCONTRACTED}"
)

app = typer.Typer(help="Reserve and attach public IP addresses", no_args_is_help=True)


def _session(ctx: typer.Context) -> tuple[Any, str, Any]:
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    return settings, workspace, get_client(settings)


@app.command("list")
@handle_api_errors
def list_reserved_ips(
    ctx: typer.Context,
    site_id: Optional[str] = typer.Option(None, "--site-id", help="Filter by site"),
) -> None:
    """List Reserved IPs in the workspace."""

    settings, workspace, client = _session(ctx)
    items = client.reserved_ips.list_reserved_ips(workspace_id=workspace, site_id=site_id)
    emit(items, settings=settings, default="json", columns=COLUMNS, title="Reserved IPs", id_field=ID_FIELD)


@app.command("reserve")
@handle_api_errors
def reserve_ip(
    ctx: typer.Context,
    site_id: str = typer.Option(..., "--site-id", help="Location of the Reserved IP"),
    label: Optional[str] = typer.Option(None, "--label", help="Name, up to 120 characters"),
    billing_catalog: Optional[str] = typer.Option(
        None, "--billing-catalog", "--billing-catalog-json", metavar="JSON", help=CATALOG_HELP
    ),
    billing_catalog_file: Optional[str] = typer.Option(
        None, "--billing-catalog-file", metavar="PATH", help="Read --billing-catalog from a JSON file"
    ),
    check_billing: bool = typer.Option(
        False, "--check-billing", help="Deprecated no-op; upstream decides billing and lifecycle admission."
    ),
) -> None:
    """Reserve a new public IP address (billed while reserved)."""

    settings, workspace, client = _session(ctx)
    catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
    reserved = client.reserved_ips.reserve_ip(
        workspace_id=workspace,
        site_id=site_id,
        label=label,
        billing_catalog=catalog,
        check_billing=False,
    )
    print_json(reserved, id_field=ID_FIELD)


@app.command("convert")
@handle_api_errors
def convert_vm_public_ip(
    ctx: typer.Context,
    vm_id: str = typer.Option(..., "--vm-id", help="VM whose current public IPv4 becomes a Reserved IP"),
    site_id: str = typer.Option(..., "--site-id", help="The VM's site"),
    label: Optional[str] = typer.Option(None, "--label", help="Name, up to 120 characters"),
    billing_catalog: Optional[str] = typer.Option(
        None, "--billing-catalog", "--billing-catalog-json", metavar="JSON", help=CATALOG_HELP
    ),
    billing_catalog_file: Optional[str] = typer.Option(
        None, "--billing-catalog-file", metavar="PATH", help="Read --billing-catalog from a JSON file"
    ),
    billing_check: bool = typer.Option(
        True,
        "--billing-check/--no-billing-check",
        help="Deprecated no-op; upstream decides billing and lifecycle admission.",
    ),
) -> None:
    """Keep a VM's current public IPv4 as a Reserved IP (VMs outside a VPC).

    Not yet part of the published API contract; behaviour may change.
    """

    _settings, workspace, client = _session(ctx)
    catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
    reserved = client.reserved_ips.convert_vm_public_ip_to_reserved_ip(
        workspace_id=workspace,
        vm_id=vm_id,
        site_id=site_id,
        label=label,
        billing_catalog=catalog,
        billing_check=False,
    )
    print_json(reserved, id_field=ID_FIELD)


@app.command("get")
@handle_api_errors
def get_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
) -> None:
    """Show a Reserved IP."""

    _settings, workspace, client = _session(ctx)
    print_json(client.reserved_ips.get_reserved_ip(reserved_ip_id, workspace_id=workspace), id_field=ID_FIELD)


@app.command("update")
@handle_api_errors
def update_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
    label: Optional[str] = typer.Option(None, "--label", help="Name, up to 120 characters"),
    reverse_dns: Optional[str] = typer.Option(
        None, "--reverse-dns", help="Fully qualified hostname (up to 253 characters); an empty string clears it"
    ),
) -> None:
    """Update a Reserved IP label or reverse DNS (at least one)."""

    _settings, workspace, client = _session(ctx)
    reserved = client.reserved_ips.update_reserved_ip(
        reserved_ip_id, workspace_id=workspace, label=label, reverse_dns=reverse_dns
    )
    print_json(reserved, id_field=ID_FIELD)


@app.command("attach")
@handle_api_errors
def attach_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID (attached to a VPC, same site)"),
    vpc_id: Optional[str] = typer.Option(None, "--vpc-id", help="Needed when the VM is in several VPCs"),
    subnet_id: Optional[str] = typer.Option(None, "--subnet-id", help="Needed when the VM is in several VPCs"),
    detach_from_service: bool = typer.Option(
        False,
        "--detach-from-service",
        help="If the IP is on a NAT gateway or virtual IP, detach it from there first",
    ),
    check_state: bool = check_state_option(),
) -> None:
    """Attach a Reserved IP to a VPC-attached VM (use 'move' if it is on another VM)."""

    _settings, workspace, client = _session(ctx)
    reserved = client.reserved_ips.attach_reserved_ip(
        reserved_ip_id,
        workspace_id=workspace,
        vm_id=vm_id,
        vpc_id=vpc_id,
        subnet_id=subnet_id,
        detach_from_service=detach_from_service,
        check_state=check_state,
    )
    print_json(reserved, id_field=ID_FIELD)


def _attach_virtual_ip(ctx: typer.Context, reserved_ip_id: str, virtual_ip_id: str, check_state: bool) -> None:
    _settings, workspace, client = _session(ctx)
    reserved = client.reserved_ips.attach_reserved_ip_to_virtual_ip(
        reserved_ip_id, workspace_id=workspace, virtual_ip_id=virtual_ip_id, check_state=check_state
    )
    print_json(reserved, id_field=ID_FIELD)


@app.command("attach-virtual-ip")
@handle_api_errors
def attach_virtual_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Unattached Reserved IP ID"),
    virtual_ip_id: str = typer.Option(..., "--virtual-ip-id", help="VPC virtual IP ID (same site, available)"),
    check_state: bool = check_state_option(),
) -> None:
    """Attach a Reserved IP to a VPC virtual IP (1:1 NAT).

    Not yet part of the published API contract; behaviour may change.
    """

    _attach_virtual_ip(ctx, reserved_ip_id, virtual_ip_id, check_state)


@app.command("attach-vip", hidden=True)
@handle_api_errors
def attach_vip_alias(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Unattached Reserved IP ID"),
    virtual_ip_id: str = typer.Option(..., "--virtual-ip-id", help="VPC virtual IP ID"),
    check_state: bool = check_state_option(),
) -> None:
    """Alias of attach-virtual-ip."""

    _attach_virtual_ip(ctx, reserved_ip_id, virtual_ip_id, check_state)


@app.command("detach")
@handle_api_errors
def detach_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
    check_state: bool = check_state_option(),
) -> None:
    """Detach a Reserved IP from its VM, NAT gateway or virtual IP (it stays reserved)."""

    _settings, workspace, client = _session(ctx)
    reserved = client.reserved_ips.detach_reserved_ip(
        reserved_ip_id, workspace_id=workspace, check_state=check_state
    )
    print_json(reserved, id_field=ID_FIELD)


@app.command("move")
@handle_api_errors
def move_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID (attached to a VPC VM)"),
    vm_id: str = typer.Argument(..., help="Destination cloud or GPU VM ID (a different VM)"),
    vpc_id: Optional[str] = typer.Option(None, "--vpc-id"),
    subnet_id: Optional[str] = typer.Option(None, "--subnet-id"),
    check_state: bool = check_state_option(),
) -> None:
    """Atomically move a Reserved IP to another VPC-attached VM."""

    _settings, workspace, client = _session(ctx)
    reserved = client.reserved_ips.move_reserved_ip(
        reserved_ip_id,
        workspace_id=workspace,
        vm_id=vm_id,
        vpc_id=vpc_id,
        subnet_id=subnet_id,
        check_state=check_state,
    )
    print_json(reserved, id_field=ID_FIELD)


@app.command("release")
@handle_api_errors
def release_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
    check_state: bool = check_state_option(),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Release a Reserved IP (it must be detached first; billing stops)."""

    settings, workspace, client = _session(ctx)
    if check_state:
        current = client.reserved_ips.get_reserved_ip(reserved_ip_id, workspace_id=workspace)
        check_reserved_ip_releasable(current)
    confirm_destructive(settings, f"Release Reserved IP '{reserved_ip_id}'?", yes)
    client.reserved_ips.release_reserved_ip(reserved_ip_id, workspace_id=workspace, check_state=False)
    if not settings.structured_output:
        typer.secho(f"Reserved IP '{reserved_ip_id}' released.", fg=typer.colors.GREEN)
