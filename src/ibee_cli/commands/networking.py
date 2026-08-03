"""VPC, subnet, node, NAT, and port-forwarding commands."""

from __future__ import annotations

from typing import List, Optional

import typer

from ..context import api_request, get_client, get_settings, require_workspace
from ..helpers import compact_payload, require_billing_eligibility
from ..render import handle_api_errors, print_json

app = typer.Typer(help="VPC networking, subnets, nodes, and NAT", no_args_is_help=True)
subnets_app = typer.Typer(help="Manage VPC subnets", no_args_is_help=True)
nodes_app = typer.Typer(help="Attach VMs to VPCs", no_args_is_help=True)
nat_app = typer.Typer(help="Manage VPC NAT gateways", no_args_is_help=True)
forwarding_app = typer.Typer(help="Manage NAT port-forwarding rules", no_args_is_help=True)
app.add_typer(subnets_app, name="subnets")
app.add_typer(nodes_app, name="nodes")
app.add_typer(nat_app, name="nat")
app.add_typer(forwarding_app, name="forwarding")


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


def _require_changes(payload: dict) -> dict:
    if not payload:
        raise typer.BadParameter("Provide at least one update option.")
    return payload


@app.command("sites")
@handle_api_errors
def list_sites(ctx: typer.Context) -> None:
    """List sites where VPC networking is available."""

    _call(ctx, "GET", "networking/sites")


@app.command("list")
@handle_api_errors
def list_vpcs(
    ctx: typer.Context,
    site_id: Optional[str] = typer.Option(None, "--site-id", help="Filter by site"),
) -> None:
    """List VPCs in the workspace."""

    _call(ctx, "GET", "networking/vpcs", params={"site_id": site_id})


@app.command("create")
@handle_api_errors
def create_vpc(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="VPC name"),
    site_id: str = typer.Option(..., "--site-id", help="Placement site ID"),
    description: str = typer.Option("", "--description", "-d"),
    region: Optional[str] = typer.Option(None, "--region"),
    cidr: Optional[str] = typer.Option(
        None, "--cidr", help="RFC1918 CIDR with a /22 through /28 prefix"
    ),
    auto_cidr: bool = typer.Option(
        True, "--auto-cidr/--no-auto-cidr", help="Allocate a CIDR automatically"
    ),
    create_default_subnet: bool = typer.Option(
        True,
        "--default-subnet/--no-default-subnet",
        help="Create a default subnet with the VPC",
    ),
    default_subnet_cidr: Optional[str] = typer.Option(None, "--default-subnet-cidr"),
    is_default: bool = typer.Option(False, "--default", help="Make this the default VPC"),
    connectivity_type: str = typer.Option(
        "public", "--connectivity", help="public or nat_gateway"
    ),
) -> None:
    """Create an isolated VPC."""

    _call(
        ctx,
        "POST",
        "networking/vpcs",
        payload=compact_payload(
            name=name,
            description=description,
            site_id=site_id,
            region=region,
            cidr=cidr,
            auto_cidr=auto_cidr,
            create_default_subnet=create_default_subnet,
            default_subnet_cidr=default_subnet_cidr,
            is_default=is_default,
            connectivity_type=connectivity_type,
        ),
    )


@app.command("get")
@handle_api_errors
def get_vpc(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
) -> None:
    """Show a VPC with its subnets, NAT gateways, and attached nodes."""

    _call(ctx, "GET", f"networking/vpcs/{vpc_id}")


@app.command("update")
@handle_api_errors
def update_vpc(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
) -> None:
    """Update mutable VPC metadata."""

    payload = _require_changes(compact_payload(name=name, description=description))
    _call(ctx, "PATCH", f"networking/vpcs/{vpc_id}", payload=payload)


@app.command("delete")
@handle_api_errors
def delete_vpc(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete an empty VPC."""

    if not yes:
        typer.confirm(f"Delete VPC '{vpc_id}'?", abort=True)
    _call(
        ctx,
        "DELETE",
        f"networking/vpcs/{vpc_id}",
        success=f"VPC '{vpc_id}' deleted.",
    )


@subnets_app.command("list")
@handle_api_errors
def list_subnets(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
) -> None:
    """List subnets in a VPC."""

    _call(ctx, "GET", f"networking/vpcs/{vpc_id}/subnets")


@subnets_app.command("create")
@handle_api_errors
def create_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    name: str = typer.Argument(..., help="Subnet name"),
    cidr: Optional[str] = typer.Option(None, "--cidr"),
    auto_cidr: bool = typer.Option(True, "--auto-cidr/--no-auto-cidr"),
    prefix_length: Optional[int] = typer.Option(
        None, "--prefix-length", min=22, max=29
    ),
    dns: Optional[List[str]] = typer.Option(
        None, "--dns", help="DNS server (repeatable)"
    ),
) -> None:
    """Create a subnet in a VPC."""

    _call(
        ctx,
        "POST",
        f"networking/vpcs/{vpc_id}/subnets",
        payload=compact_payload(
            name=name,
            cidr=cidr,
            auto_cidr=auto_cidr,
            prefix_length=prefix_length,
            dns=dns or None,
        ),
    )


@subnets_app.command("get")
@handle_api_errors
def get_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
) -> None:
    """Show a VPC subnet."""

    _call(ctx, "GET", f"networking/vpcs/{vpc_id}/subnets/{subnet_id}")


@subnets_app.command("update")
@handle_api_errors
def update_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    dns: Optional[List[str]] = typer.Option(None, "--dns", help="DNS server (repeatable)"),
) -> None:
    """Update a VPC subnet."""

    payload = _require_changes(compact_payload(name=name, dns=dns or None))
    _call(
        ctx,
        "PATCH",
        f"networking/vpcs/{vpc_id}/subnets/{subnet_id}",
        payload=payload,
    )


@subnets_app.command("delete")
@handle_api_errors
def delete_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete an unused subnet."""

    if not yes:
        typer.confirm(f"Delete subnet '{subnet_id}'?", abort=True)
    _call(
        ctx,
        "DELETE",
        f"networking/vpcs/{vpc_id}/subnets/{subnet_id}",
        success=f"Subnet '{subnet_id}' deleted.",
    )


@nodes_app.command("list")
@handle_api_errors
def list_nodes(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
) -> None:
    """List VM network allocations in a VPC."""

    _call(ctx, "GET", f"networking/vpcs/{vpc_id}/nodes")


@nodes_app.command("attach")
@handle_api_errors
def attach_node(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
    subnet_id: str = typer.Option(..., "--subnet-id", help="Subnet ID"),
    connectivity: str = typer.Option(
        "private", "--connectivity", help="private, public_ip, or nat"
    ),
    reserved_public_ip_id: Optional[str] = typer.Option(
        None, "--reserved-ip-id", help="Reserved IP to use with public_ip connectivity"
    ),
) -> None:
    """Attach a VM to a VPC subnet."""

    _call(
        ctx,
        "POST",
        f"networking/vpcs/{vpc_id}/nodes",
        payload=compact_payload(
            vm_id=vm_id,
            subnet_id=subnet_id,
            connectivity=connectivity,
            reserved_public_ip_id=reserved_public_ip_id,
        ),
    )


@nodes_app.command("detach")
@handle_api_errors
def detach_node(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Detach a VM from a VPC."""

    if not yes:
        typer.confirm(f"Detach VM '{vm_id}' from VPC '{vpc_id}'?", abort=True)
    _call(
        ctx,
        "DELETE",
        f"networking/vpcs/{vpc_id}/nodes/{vm_id}",
        success=f"VM '{vm_id}' detached from VPC '{vpc_id}'.",
    )


@nat_app.command("list")
@handle_api_errors
def list_nat_gateways(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
) -> None:
    """List NAT gateways in a VPC."""

    _call(ctx, "GET", f"networking/vpcs/{vpc_id}/nat-gateways")


@nat_app.command("create")
@handle_api_errors
def create_nat_gateway(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    name: str = typer.Option("NAT Gateway", "--name"),
    subnet_id: Optional[str] = typer.Option(None, "--subnet-id"),
    reserved_public_ip_id: Optional[str] = typer.Option(None, "--reserved-ip-id"),
) -> None:
    """Create a NAT gateway in a VPC."""

    settings = get_settings(ctx)
    require_billing_eligibility(
        get_client(settings),
        require_workspace(settings),
    )
    _call(
        ctx,
        "POST",
        f"networking/vpcs/{vpc_id}/nat-gateways",
        payload=compact_payload(
            name=name,
            subnet_id=subnet_id,
            reserved_public_ip_id=reserved_public_ip_id,
        ),
    )


@nat_app.command("delete")
@handle_api_errors
def delete_nat_gateway(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a NAT gateway."""

    if not yes:
        typer.confirm(f"Delete NAT gateway '{nat_gateway_id}'?", abort=True)
    _call(
        ctx,
        "DELETE",
        f"networking/vpcs/{vpc_id}/nat-gateways/{nat_gateway_id}",
        success=f"NAT gateway '{nat_gateway_id}' deleted.",
    )


def _forwarding_path(vpc_id: str, nat_gateway_id: str) -> str:
    return (
        f"networking/vpcs/{vpc_id}/nat-gateways/{nat_gateway_id}"
        "/port-forwarding-rules"
    )


@forwarding_app.command("list")
@handle_api_errors
def list_forwarding_rules(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
) -> None:
    """List port-forwarding rules on a NAT gateway."""

    _call(ctx, "GET", _forwarding_path(vpc_id, nat_gateway_id))


@forwarding_app.command("create")
@handle_api_errors
def create_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    name: str = typer.Argument(..., help="Rule name"),
    external_port: int = typer.Option(..., "--external-port", min=1, max=65535),
    internal_ip: str = typer.Option(..., "--internal-ip"),
    internal_port: int = typer.Option(..., "--internal-port", min=1, max=65535),
    protocol: str = typer.Option("tcp", "--protocol", help="tcp or udp"),
    note: str = typer.Option("", "--note"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
) -> None:
    """Create a NAT port-forwarding rule."""

    _call(
        ctx,
        "POST",
        _forwarding_path(vpc_id, nat_gateway_id),
        payload={
            "name": name,
            "external_port": external_port,
            "internal_ip": internal_ip,
            "internal_port": internal_port,
            "protocol": protocol,
            "note": note,
            "enabled": enabled,
        },
    )


@forwarding_app.command("update")
@handle_api_errors
def update_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    rule_id: str = typer.Argument(..., help="Port-forwarding rule ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    external_port: Optional[int] = typer.Option(
        None, "--external-port", min=1, max=65535
    ),
    internal_ip: Optional[str] = typer.Option(None, "--internal-ip"),
    internal_port: Optional[int] = typer.Option(
        None, "--internal-port", min=1, max=65535
    ),
    protocol: Optional[str] = typer.Option(None, "--protocol"),
    note: Optional[str] = typer.Option(None, "--note"),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled"),
) -> None:
    """Update a NAT port-forwarding rule."""

    payload = _require_changes(
        compact_payload(
            name=name,
            external_port=external_port,
            internal_ip=internal_ip,
            internal_port=internal_port,
            protocol=protocol,
            note=note,
            enabled=enabled,
        )
    )
    _call(
        ctx,
        "PATCH",
        f"{_forwarding_path(vpc_id, nat_gateway_id)}/{rule_id}",
        payload=payload,
    )


@forwarding_app.command("delete")
@handle_api_errors
def delete_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    rule_id: str = typer.Argument(..., help="Port-forwarding rule ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a NAT port-forwarding rule."""

    if not yes:
        typer.confirm(f"Delete port-forwarding rule '{rule_id}'?", abort=True)
    _call(
        ctx,
        "DELETE",
        f"{_forwarding_path(vpc_id, nat_gateway_id)}/{rule_id}",
        success=f"Port-forwarding rule '{rule_id}' deleted.",
    )
