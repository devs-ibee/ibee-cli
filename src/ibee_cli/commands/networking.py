"""VPC, subnet, node, NAT gateway and port-forwarding commands (``ibee vpcs``).

Each command makes one SDK call and prints the server's answer. The server decides
CIDRs, ports, connectivity and state; the CLI sends only the options you give.
"""

from __future__ import annotations

from typing import Any, List, Optional

import typer

from ..context import get_client, get_settings
from ..helpers import compact_payload, confirm_destructive, load_json_input
from ..render import emit, handle_api_errors, print_json

app = typer.Typer(help="VPC networking, subnets, nodes and NAT", no_args_is_help=True)
subnets_app = typer.Typer(help="Manage VPC subnets", no_args_is_help=True)
nodes_app = typer.Typer(help="Attach VMs to VPCs", no_args_is_help=True)
nat_app = typer.Typer(help="Manage VPC NAT gateways", no_args_is_help=True)
forwarding_app = typer.Typer(help="Manage NAT port-forwarding rules", no_args_is_help=True)
app.add_typer(subnets_app, name="subnets")
app.add_typer(nodes_app, name="nodes")
app.add_typer(nat_app, name="nat")
app.add_typer(forwarding_app, name="forwarding")

SITE_COLUMNS = ("site_id", "site_name", "available", "message")
VPC_COLUMNS = ("vpc_id", "name", "site_id", "cidr", "connectivity_type", "status", "node_count")
SUBNET_COLUMNS = ("subnet_id", "name", "cidr", "gateway", "status")
NODE_COLUMNS = ("allocation_id", "vm_id", "subnet_id", "connectivity", "private_ip", "public_ip", "status")
NAT_COLUMNS = ("nat_gateway_id", "name", "public_ip", "public_ip_source", "status")
RULE_COLUMNS = (
    "port_forward_rule_id", "name", "protocol", "external_port", "internal_ip", "internal_port", "target_type", "enabled", "status",
)
RULE_ID = "port_forward_rule_id"


def _vpcs(ctx: typer.Context) -> tuple[Any, Any]:
    settings = get_settings(ctx)
    return settings, get_client(settings).vpcs


def _done(settings: Any, message: str) -> None:
    if not settings.structured_output:
        typer.secho(message, fg=typer.colors.GREEN)


def _catalog(flag: str) -> Any:
    return typer.Option(None, f"--{flag}", f"--{flag}-json", metavar="JSON", help="Billing catalog JSON object, sent as given")


def _catalog_file(flag: str) -> Any:
    return typer.Option(None, f"--{flag}-file", metavar="PATH", help=f"Read --{flag} from a JSON file")


def _yes() -> Any:
    return typer.Option(False, "--yes", "-y", help="Skip confirmation")


# -- sites and VPCs -----------------------------------------------------------------


@app.command("sites")
@handle_api_errors
def list_sites(ctx: typer.Context) -> None:
    """List sites for VPCs and Reserved IPs (use entries where available is true)."""
    settings, vpcs = _vpcs(ctx)
    emit(vpcs.list_networking_sites(), settings=settings, default="json", columns=SITE_COLUMNS, title="VPC sites", id_field="site_id")


@app.command("list")
@handle_api_errors
def list_vpcs(ctx: typer.Context, site_id: Optional[str] = typer.Option(None, "--site-id", help="Filter by site")) -> None:
    """List VPCs in the workspace."""
    settings, vpcs = _vpcs(ctx)
    vpc_list = vpcs.list_vpcs(**compact_payload(site_id=site_id))
    emit(vpc_list, settings=settings, default="json", columns=VPC_COLUMNS, title="VPCs", id_field="vpc_id")


@app.command("create")
@handle_api_errors
def create_vpc(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="VPC name"),
    site_id: str = typer.Option(..., "--site-id", help="Site ID (see 'ibee vpcs sites')"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
    region: Optional[str] = typer.Option(None, "--region"),
    cidr: Optional[str] = typer.Option(None, "--cidr", help="Private CIDR (default: the server allocates one)"),
    auto_cidr: Optional[bool] = typer.Option(None, "--auto-cidr/--no-auto-cidr", show_default=False),
    create_default_subnet: Optional[bool] = typer.Option(None, "--default-subnet/--no-default-subnet", show_default=False),
    default_subnet_cidr: Optional[str] = typer.Option(None, "--default-subnet-cidr"),
    is_default: bool = typer.Option(False, "--default", help="Make this the default VPC"),
    connectivity_type: Optional[str] = typer.Option(None, "--connectivity", help="For example private or nat_gateway"),
    nat_billing_catalog: Optional[str] = _catalog("nat-billing-catalog"),
    nat_billing_catalog_file: Optional[str] = _catalog_file("nat-billing-catalog"),
) -> None:
    """Create a VPC."""
    _settings, vpcs = _vpcs(ctx)
    vpc = vpcs.create_vpc(
        name=name,
        site_id=site_id,
        nat_billing_catalog=load_json_input(nat_billing_catalog, nat_billing_catalog_file, "nat-billing-catalog"),
        **compact_payload(
            description=description, region=region, cidr=cidr, auto_cidr=auto_cidr,
            create_default_subnet=create_default_subnet, default_subnet_cidr=default_subnet_cidr,
            is_default=True if is_default else None, connectivity_type=connectivity_type,
        ),
    )
    print_json(vpc, id_field="vpc_id")


@app.command("get")
@handle_api_errors
def get_vpc(ctx: typer.Context, vpc_id: str = typer.Argument(..., help="VPC ID")) -> None:
    """Show a VPC with its subnets, NAT gateways and attached nodes."""
    print_json(_vpcs(ctx)[1].get_vpc(vpc_id), id_field="vpc_id")


@app.command("update")
@handle_api_errors
def update_vpc(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    description: Optional[str] = typer.Option(None, "--description", "-d"),
) -> None:
    """Update a VPC's name or description."""
    print_json(_vpcs(ctx)[1].update_vpc(vpc_id, **compact_payload(name=name, description=description)), id_field="vpc_id")


@app.command("delete")
@handle_api_errors
def delete_vpc(ctx: typer.Context, vpc_id: str = typer.Argument(..., help="VPC ID"), yes: bool = _yes()) -> None:
    """Delete a VPC."""
    settings, vpcs = _vpcs(ctx)
    confirm_destructive(settings, f"Delete VPC '{vpc_id}'?", yes)
    vpcs.delete_vpc(vpc_id)
    _done(settings, f"VPC '{vpc_id}' deleted.")


# -- subnets ------------------------------------------------------------------------


@subnets_app.command("list")
@handle_api_errors
def list_subnets(ctx: typer.Context, vpc_id: str = typer.Argument(..., help="VPC ID")) -> None:
    """List subnets in a VPC."""
    settings, vpcs = _vpcs(ctx)
    emit(vpcs.list_vpc_subnets(vpc_id), settings=settings, default="json", columns=SUBNET_COLUMNS, title="Subnets", id_field="subnet_id")


@subnets_app.command("create")
@handle_api_errors
def create_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    name: str = typer.Argument(..., help="Subnet name"),
    cidr: Optional[str] = typer.Option(None, "--cidr"),
    auto_cidr: Optional[bool] = typer.Option(None, "--auto-cidr/--no-auto-cidr", show_default=False),
    prefix_length: Optional[int] = typer.Option(None, "--prefix-length", help="Size of an automatic CIDR"),
    dns: Optional[List[str]] = typer.Option(None, "--dns", help="DNS server (repeatable)"),
) -> None:
    """Create a subnet."""
    _settings, vpcs = _vpcs(ctx)
    subnet = vpcs.create_vpc_subnet(
        vpc_id, name=name, **compact_payload(cidr=cidr, auto_cidr=auto_cidr, prefix_length=prefix_length, dns=dns or None)
    )
    print_json(subnet, id_field="subnet_id")


@subnets_app.command("get")
@handle_api_errors
def get_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
) -> None:
    """Show a subnet."""
    print_json(_vpcs(ctx)[1].get_vpc_subnet(vpc_id, subnet_id), id_field="subnet_id")


@subnets_app.command("update")
@handle_api_errors
def update_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    dns: Optional[List[str]] = typer.Option(None, "--dns", help="DNS server (repeatable)"),
) -> None:
    """Update a subnet's name or DNS servers."""
    subnet = _vpcs(ctx)[1].update_vpc_subnet(vpc_id, subnet_id, **compact_payload(name=name, dns=dns or None))
    print_json(subnet, id_field="subnet_id")


@subnets_app.command("delete")
@handle_api_errors
def delete_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
    yes: bool = _yes(),
) -> None:
    """Delete a subnet."""
    settings, vpcs = _vpcs(ctx)
    confirm_destructive(settings, f"Delete subnet '{subnet_id}'?", yes)
    vpcs.delete_vpc_subnet(vpc_id, subnet_id)
    _done(settings, f"Subnet '{subnet_id}' deleted.")


# -- nodes --------------------------------------------------------------------------


@nodes_app.command("list")
@handle_api_errors
def list_nodes(ctx: typer.Context, vpc_id: str = typer.Argument(..., help="VPC ID")) -> None:
    """List VM network allocations in a VPC."""
    settings, vpcs = _vpcs(ctx)
    emit(vpcs.list_vpc_nodes(vpc_id), settings=settings, default="json", columns=NODE_COLUMNS, title="VPC nodes", id_field="allocation_id")


@nodes_app.command("attach")
@handle_api_errors
def attach_node(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
    subnet_id: str = typer.Option(..., "--subnet-id"),
    connectivity: Optional[str] = typer.Option(None, "--connectivity", help="For example private, nat or public_ip"),
    reserved_public_ip_id: Optional[str] = typer.Option(None, "--reserved-ip-id", help="Reserved IP for public_ip connectivity"),
) -> None:
    """Create a VM's network allocation in a VPC subnet."""
    allocation = _vpcs(ctx)[1].attach_vpc_node(
        vpc_id, vm_id=vm_id, subnet_id=subnet_id,
        **compact_payload(connectivity=connectivity, reserved_public_ip_id=reserved_public_ip_id),
    )
    print_json(allocation, id_field="allocation_id")


@nodes_app.command("detach")
@handle_api_errors
def detach_node(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
    yes: bool = _yes(),
) -> None:
    """Release a VM's VPC allocation."""
    settings, vpcs = _vpcs(ctx)
    confirm_destructive(settings, f"Detach VM '{vm_id}' from VPC '{vpc_id}'?", yes)
    vpcs.detach_vpc_node(vpc_id, vm_id)
    _done(settings, f"VM '{vm_id}' detached from VPC '{vpc_id}'.")


# -- NAT gateways -------------------------------------------------------------------


@nat_app.command("list")
@handle_api_errors
def list_nat_gateways(ctx: typer.Context, vpc_id: str = typer.Argument(..., help="VPC ID")) -> None:
    """List NAT gateways in a VPC."""
    settings, vpcs = _vpcs(ctx)
    gateways = vpcs.list_nat_gateways(vpc_id)
    emit(gateways, settings=settings, default="json", columns=NAT_COLUMNS, title="NAT gateways", id_field="nat_gateway_id")


@nat_app.command("create")
@handle_api_errors
def create_nat_gateway(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    subnet_id: Optional[str] = typer.Option(None, "--subnet-id"),
    reserved_public_ip_id: Optional[str] = typer.Option(None, "--reserved-ip-id", help="Use this Reserved IP"),
    billing_catalog: Optional[str] = _catalog("billing-catalog"),
    billing_catalog_file: Optional[str] = _catalog_file("billing-catalog"),
) -> None:
    """Create a VPC's NAT gateway."""
    gateway = _vpcs(ctx)[1].create_nat_gateway(
        vpc_id,
        billing_catalog=load_json_input(billing_catalog, billing_catalog_file, "billing-catalog"),
        **compact_payload(name=name, subnet_id=subnet_id, reserved_public_ip_id=reserved_public_ip_id),
    )
    print_json(gateway, id_field="nat_gateway_id")


@nat_app.command("delete")
@handle_api_errors
def delete_nat_gateway(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    ip_action: Optional[str] = typer.Option(None, "--ip-action", help="What to do with the public IP: reserve or release"),
    billing_catalog: Optional[str] = _catalog("billing-catalog"),
    billing_catalog_file: Optional[str] = _catalog_file("billing-catalog"),
    yes: bool = _yes(),
) -> None:
    """Delete a NAT gateway."""
    settings, vpcs = _vpcs(ctx)
    catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
    confirm_destructive(settings, f"Delete NAT gateway '{nat_gateway_id}'?", yes)
    vpcs.delete_nat_gateway(vpc_id, nat_gateway_id, public_ip_action=ip_action, billing_catalog=catalog)
    _done(settings, f"NAT gateway '{nat_gateway_id}' deletion accepted.")


# -- port forwarding ----------------------------------------------------------------


@forwarding_app.command("list")
@handle_api_errors
def list_forwarding_rules(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
) -> None:
    """List port-forwarding rules on a NAT gateway."""
    settings, vpcs = _vpcs(ctx)
    rules = vpcs.list_nat_port_forwarding_rules(vpc_id, nat_gateway_id)
    emit(rules, settings=settings, default="json", columns=RULE_COLUMNS, title="Port-forwarding rules", id_field=RULE_ID)


@forwarding_app.command("create")
@handle_api_errors
def create_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    name: str = typer.Argument(..., help="Rule name"),
    external_port: int = typer.Option(..., "--external-port"),
    internal_ip: str = typer.Option(..., "--internal-ip"),
    internal_port: int = typer.Option(..., "--internal-port"),
    protocol: Optional[str] = typer.Option(None, "--protocol", help="For example tcp or udp"),
    note: Optional[str] = typer.Option(None, "--note"),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled", show_default=False),
) -> None:
    """Create a NAT port-forwarding rule."""
    rule = _vpcs(ctx)[1].create_nat_port_forwarding_rule(
        vpc_id, nat_gateway_id, name=name, external_port=external_port, internal_ip=internal_ip,
        internal_port=internal_port, **compact_payload(protocol=protocol, note=note, enabled=enabled),
    )
    print_json(rule, id_field=RULE_ID)


def _update_rule(ctx: typer.Context, vpc_id: str, nat_gateway_id: str, rule_id: str, **fields: Any) -> None:
    rule = _vpcs(ctx)[1].update_nat_port_forwarding_rule(vpc_id, nat_gateway_id, rule_id, **compact_payload(**fields))
    print_json(rule, id_field=RULE_ID)


@forwarding_app.command("update")
@handle_api_errors
def update_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    rule_id: str = typer.Argument(..., help="Port-forwarding rule ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    external_port: Optional[int] = typer.Option(None, "--external-port"),
    internal_ip: Optional[str] = typer.Option(None, "--internal-ip"),
    internal_port: Optional[int] = typer.Option(None, "--internal-port"),
    protocol: Optional[str] = typer.Option(None, "--protocol"),
    note: Optional[str] = typer.Option(None, "--note"),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled", show_default=False),
) -> None:
    """Update a NAT port-forwarding rule."""
    _update_rule(
        ctx, vpc_id, nat_gateway_id, rule_id, name=name, external_port=external_port, internal_ip=internal_ip,
        internal_port=internal_port, protocol=protocol, note=note, enabled=enabled,
    )


@forwarding_app.command("enable")
@handle_api_errors
def enable_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    rule_id: str = typer.Argument(..., help="Port-forwarding rule ID"),
) -> None:
    """Enable a port-forwarding rule (an update with enabled=true)."""
    _update_rule(ctx, vpc_id, nat_gateway_id, rule_id, enabled=True)


@forwarding_app.command("disable")
@handle_api_errors
def disable_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    rule_id: str = typer.Argument(..., help="Port-forwarding rule ID"),
) -> None:
    """Disable a port-forwarding rule (an update with enabled=false)."""
    _update_rule(ctx, vpc_id, nat_gateway_id, rule_id, enabled=False)


@forwarding_app.command("delete")
@handle_api_errors
def delete_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    rule_id: str = typer.Argument(..., help="Port-forwarding rule ID"),
    yes: bool = _yes(),
) -> None:
    """Delete a NAT port-forwarding rule."""
    settings, vpcs = _vpcs(ctx)
    confirm_destructive(settings, f"Delete port-forwarding rule '{rule_id}'?", yes)
    vpcs.delete_nat_port_forwarding_rule(vpc_id, nat_gateway_id, rule_id)
    _done(settings, f"Port-forwarding rule '{rule_id}' deleted.")
