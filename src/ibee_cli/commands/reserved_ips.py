"""Reserved public IP commands.

Each command makes one SDK call and prints the server's answer; the server decides
labels, reverse DNS, attachments and whether an address can be released.
"""

from __future__ import annotations

from typing import Any, Optional

import typer

from ..context import get_client, get_settings
from ..helpers import compact_payload, confirm_destructive, load_json_input
from ..render import emit, handle_api_errors, print_json

ID_FIELD = "public_ip_id"
COLUMNS = ("public_ip_id", "address", "site_id", "label", "status", "attached_resource_type", "attached_resource_id")

app = typer.Typer(help="Reserve and attach public IP addresses", no_args_is_help=True)


def _ips(ctx: typer.Context) -> tuple[Any, Any]:
    settings = get_settings(ctx)
    return settings, get_client(settings).reserved_ips


@app.command("list")
@handle_api_errors
def list_reserved_ips(ctx: typer.Context, site_id: Optional[str] = typer.Option(None, "--site-id", help="Filter by site")) -> None:
    """List Reserved IPs in the workspace."""
    settings, ips = _ips(ctx)
    emit(ips.list_reserved_ips(**compact_payload(site_id=site_id)), settings=settings, default="json", columns=COLUMNS,
         title="Reserved IPs", id_field=ID_FIELD)


@app.command("reserve")
@handle_api_errors
def reserve_ip(
    ctx: typer.Context,
    site_id: str = typer.Option(..., "--site-id", help="Location of the Reserved IP (see 'ibee vpcs sites')"),
    label: Optional[str] = typer.Option(None, "--label"),
    billing_catalog: Optional[str] = typer.Option(
        None, "--billing-catalog", "--billing-catalog-json", metavar="JSON", help="Reserved IP billing SKU as JSON"
    ),
    billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file", metavar="PATH"),
) -> None:
    """Reserve a new public IP address (billed while reserved)."""
    catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
    result = _ips(ctx)[1].reserve_ip(site_id=site_id, **compact_payload(label=label, billing_catalog=catalog))
    print_json(result, id_field=ID_FIELD)


@app.command("get")
@handle_api_errors
def get_reserved_ip(ctx: typer.Context, reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID")) -> None:
    """Show a Reserved IP."""
    print_json(_ips(ctx)[1].get_reserved_ip(reserved_ip_id), id_field=ID_FIELD)


@app.command("update")
@handle_api_errors
def update_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
    label: Optional[str] = typer.Option(None, "--label"),
    reverse_dns: Optional[str] = typer.Option(None, "--reverse-dns", help="Hostname; an empty string clears it"),
) -> None:
    """Update a Reserved IP's label or reverse DNS."""
    print_json(_ips(ctx)[1].update_reserved_ip(reserved_ip_id, **compact_payload(label=label, reverse_dns=reverse_dns)), id_field=ID_FIELD)


@app.command("attach")
@handle_api_errors
def attach_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
    vpc_id: Optional[str] = typer.Option(None, "--vpc-id"),
    subnet_id: Optional[str] = typer.Option(None, "--subnet-id"),
) -> None:
    """Attach a Reserved IP to a VM."""
    reserved = _ips(ctx)[1].attach_reserved_ip(reserved_ip_id, vm_id=vm_id, **compact_payload(vpc_id=vpc_id, subnet_id=subnet_id))
    print_json(reserved, id_field=ID_FIELD)


@app.command("detach")
@handle_api_errors
def detach_reserved_ip(ctx: typer.Context, reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID")) -> None:
    """Detach a Reserved IP from what it is attached to (it stays reserved)."""
    print_json(_ips(ctx)[1].detach_reserved_ip(reserved_ip_id), id_field=ID_FIELD)


@app.command("move")
@handle_api_errors
def move_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
    vm_id: str = typer.Argument(..., help="Destination cloud or GPU VM ID"),
    vpc_id: Optional[str] = typer.Option(None, "--vpc-id"),
    subnet_id: Optional[str] = typer.Option(None, "--subnet-id"),
) -> None:
    """Move a Reserved IP to another VM."""
    reserved = _ips(ctx)[1].move_reserved_ip(reserved_ip_id, vm_id=vm_id, **compact_payload(vpc_id=vpc_id, subnet_id=subnet_id))
    print_json(reserved, id_field=ID_FIELD)


@app.command("release")
@handle_api_errors
def release_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Release a Reserved IP."""
    settings, ips = _ips(ctx)
    confirm_destructive(settings, f"Release Reserved IP '{reserved_ip_id}'?", yes)
    ips.release_reserved_ip(reserved_ip_id)
    if not settings.structured_output:
        typer.secho(f"Reserved IP '{reserved_ip_id}' released.", fg=typer.colors.GREEN)
