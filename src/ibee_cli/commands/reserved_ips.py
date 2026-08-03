"""Reserved public IP commands."""

from __future__ import annotations

from typing import Optional

import typer

from ..context import api_request, get_client, get_settings, require_workspace
from ..helpers import compact_payload, require_billing_eligibility
from ..render import handle_api_errors, print_json

app = typer.Typer(help="Reserve and attach public IP addresses", no_args_is_help=True)


def _call(
    ctx: typer.Context,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
    success: str | None = None,
) -> None:
    result = api_request(
        get_settings(ctx), method, path, params=params, json_body=payload
    )
    if result is not None:
        print_json(result)
    elif success:
        typer.secho(success, fg=typer.colors.GREEN)


@app.command("list")
@handle_api_errors
def list_reserved_ips(
    ctx: typer.Context,
    site_id: Optional[str] = typer.Option(None, "--site-id", help="Filter by site"),
) -> None:
    """List Reserved IPs in the workspace."""

    _call(ctx, "GET", "networking/reserved-ips", params={"site_id": site_id})


@app.command("reserve")
@handle_api_errors
def reserve_ip(
    ctx: typer.Context,
    site_id: str = typer.Option(..., "--site-id", help="Placement site ID"),
    label: str = typer.Option("", "--label"),
) -> None:
    """Reserve a public IP address."""

    settings = get_settings(ctx)
    require_billing_eligibility(
        get_client(settings),
        require_workspace(settings),
    )
    _call(
        ctx,
        "POST",
        "networking/reserved-ips",
        payload={"site_id": site_id, "label": label},
    )


@app.command("get")
@handle_api_errors
def get_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
) -> None:
    """Show a Reserved IP."""

    _call(ctx, "GET", f"networking/reserved-ips/{reserved_ip_id}")


@app.command("update")
@handle_api_errors
def update_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
    label: Optional[str] = typer.Option(None, "--label"),
    reverse_dns: Optional[str] = typer.Option(None, "--reverse-dns"),
) -> None:
    """Update a Reserved IP label or reverse DNS."""

    payload = compact_payload(label=label, reverse_dns=reverse_dns)
    if not payload:
        raise typer.BadParameter("Provide --label and/or --reverse-dns.")
    _call(
        ctx,
        "PATCH",
        f"networking/reserved-ips/{reserved_ip_id}",
        payload=payload,
    )


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

    _call(
        ctx,
        "POST",
        f"networking/reserved-ips/{reserved_ip_id}/attach",
        payload=compact_payload(vm_id=vm_id, vpc_id=vpc_id, subnet_id=subnet_id),
    )


@app.command("detach")
@handle_api_errors
def detach_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
) -> None:
    """Detach a Reserved IP from its current resource."""

    _call(
        ctx,
        "POST",
        f"networking/reserved-ips/{reserved_ip_id}/detach",
        payload={},
    )


@app.command("move")
@handle_api_errors
def move_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
    vm_id: str = typer.Argument(..., help="Destination cloud or GPU VM ID"),
    vpc_id: Optional[str] = typer.Option(None, "--vpc-id"),
    subnet_id: Optional[str] = typer.Option(None, "--subnet-id"),
) -> None:
    """Atomically move a Reserved IP to another VM."""

    _call(
        ctx,
        "POST",
        f"networking/reserved-ips/{reserved_ip_id}/move",
        payload=compact_payload(vm_id=vm_id, vpc_id=vpc_id, subnet_id=subnet_id),
    )


@app.command("release")
@handle_api_errors
def release_reserved_ip(
    ctx: typer.Context,
    reserved_ip_id: str = typer.Argument(..., help="Reserved IP ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Release a Reserved IP."""

    if not yes:
        typer.confirm(f"Release Reserved IP '{reserved_ip_id}'?", abort=True)
    _call(
        ctx,
        "DELETE",
        f"networking/reserved-ips/{reserved_ip_id}",
        success=f"Reserved IP '{reserved_ip_id}' released.",
    )
