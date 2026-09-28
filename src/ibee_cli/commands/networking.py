"""VPC, subnet, node, NAT gateway, port-forwarding and virtual-IP commands.

Every command calls the Python SDK, which applies the portal's rules (CIDR ranges,
host addresses, ports, NAT only in NAT-mode VPCs, duplicate forwarding ports, the
public-IP choice on NAT delete, virtual-IP announcers) before any request is sent.
"""

from __future__ import annotations

from typing import Any, List, Optional

import typer
from ibee.validation import (
    IbeeValidationError,
    available_nat_gateway,
    build_nat_delete_body,
    check_nat_reserved_ip,
    check_nat_vpc,
    check_virtual_ip_deletable,
    check_vpc_deletable,
    record_get,
)

from ..context import get_client, get_settings, require_workspace
from ..helpers import (
    api_hints,
    check_state_option,
    confirm_destructive,
    load_json_input,
    sdk_warnings,
)
from ..render import EXIT_WAIT_TIMEOUT, emit, handle_api_errors, print_json

UNCONTRACTED = "Not yet part of the published API contract; behaviour may change."

app = typer.Typer(help="VPC networking, subnets, nodes, NAT and virtual IPs", no_args_is_help=True)
subnets_app = typer.Typer(help="Manage VPC subnets", no_args_is_help=True)
nodes_app = typer.Typer(help="Attach VMs to VPCs", no_args_is_help=True)
nat_app = typer.Typer(help="Manage VPC NAT gateways", no_args_is_help=True)
forwarding_app = typer.Typer(help="Manage NAT port-forwarding rules", no_args_is_help=True)
virtual_ips_app = typer.Typer(
    help=f"Manage VPC virtual IPs (MetalLB / custom). {UNCONTRACTED}", no_args_is_help=True
)
app.add_typer(subnets_app, name="subnets")
app.add_typer(nodes_app, name="nodes")
app.add_typer(nat_app, name="nat")
app.add_typer(forwarding_app, name="forwarding")
app.add_typer(virtual_ips_app, name="virtual-ips")

VPC_COLUMNS = ("vpc_id", "name", "site_id", "cidr", "connectivity_type", "status", "node_count")
SUBNET_COLUMNS = ("subnet_id", "name", "cidr", "gateway", "status")
NODE_COLUMNS = ("allocation_id", "vm_id", "subnet_id", "connectivity", "private_ip", "public_ip", "status")
NAT_COLUMNS = ("nat_gateway_id", "name", "public_ip", "public_ip_source", "status")
RULE_COLUMNS = (
    "port_forward_rule_id",
    "name",
    "protocol",
    "external_port",
    "internal_ip",
    "internal_port",
    "target_type",
    "enabled",
    "status",
)
VIP_COLUMNS = ("virtual_ip_id", "subnet_id", "private_ip", "purpose", "public_ip", "status")
NAT_DELETE_WAIT_ATTEMPTS = 20
NAT_DELETE_WAIT_INTERVAL = 0.5

VPC_DELETE_HINT = "Detach nodes and remove subnets before deleting this VPC."
SUBNET_DELETE_HINT = "Detach nodes before deleting this subnet."
NODE_DETACH_HINT = (
    "The VM's network interface is still plugged into this VPC. The VM-side detach the "
    "portal uses is not yet available in the public API; detach the VM from the VPC in the portal."
)


def _session(ctx: typer.Context) -> tuple[Any, str, Any]:
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    return settings, workspace, get_client(settings)


def _items(value: Any) -> list[Any]:
    return list(value or [])


def _value(record: Any, name: str) -> str:
    value = record_get(record, name)
    value = getattr(value, "value", value)
    return str(value or "").strip()


def _status(value: Any) -> str:
    return _value(value, "status").lower()


def _success(settings: Any, message: str) -> None:
    if not settings.structured_output:
        typer.secho(message, fg=typer.colors.GREEN)


# ---------------------------------------------------------------------------
# Sites and VPCs
# ---------------------------------------------------------------------------


@app.command("sites")
@handle_api_errors
def list_sites(
    ctx: typer.Context,
    available_only: bool = typer.Option(
        False, "--available-only", help="Only sites where VPCs can be created now (available is true)"
    ),
) -> None:
    """List sites where VPC networking is available."""

    settings, workspace, client = _session(ctx)
    sites = client.vpcs.list_networking_sites(workspace_id=workspace, available_only=available_only)
    emit(sites, settings=settings, default="json", columns=("site_id", "site_name", "available", "message"),
         title="VPC sites", id_field="site_id")


@app.command("list")
@handle_api_errors
def list_vpcs(
    ctx: typer.Context,
    site_id: Optional[str] = typer.Option(None, "--site-id", help="Filter by site"),
) -> None:
    """List VPCs in the workspace (nat_pricing is an estimate, not a bill)."""

    settings, workspace, client = _session(ctx)
    vpcs = client.vpcs.list_vpcs(workspace_id=workspace, site_id=site_id)
    emit(vpcs, settings=settings, default="json", columns=VPC_COLUMNS, title="VPCs", id_field="vpc_id")


@app.command("create")
@handle_api_errors
def create_vpc(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="VPC name (1-80 characters)"),
    site_id: str = typer.Option(..., "--site-id", help="Site ID from 'ibee vpcs sites --available-only'"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Up to 500 characters"),
    region: Optional[str] = typer.Option(None, "--region", help="Region name sent with the VPC (optional)"),
    cidr: Optional[str] = typer.Option(
        None,
        "--cidr",
        help="Custom private CIDR: aligned, /22-/28, inside 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16 "
        "(default: allocated automatically)",
    ),
    auto_cidr: Optional[bool] = typer.Option(
        None,
        "--auto-cidr/--no-auto-cidr",
        show_default=False,
        help="Allocate the CIDR automatically (default: on unless --cidr is given)",
    ),
    create_default_subnet: bool = typer.Option(
        True, "--default-subnet/--no-default-subnet", help="Create a default subnet with the VPC"
    ),
    default_subnet_cidr: Optional[str] = typer.Option(
        None, "--default-subnet-cidr", help="CIDR of the default subnet (inside --cidr)"
    ),
    is_default: bool = typer.Option(False, "--default", help="Make this the default VPC"),
    connectivity_type: str = typer.Option(
        "private",
        "--connectivity",
        help="private (no internet egress) or nat_gateway (managed NAT gateway); 'public' is deprecated",
    ),
    nat_billing_catalog: Optional[str] = typer.Option(
        None,
        "--nat-billing-catalog",
        "--nat-billing-catalog-json",
        metavar="JSON",
        help="NAT-GATEWAY billing catalog object (nat_gateway only; needs sku_code). "
        f"Copy it from your IBEE pricing; there is no public pricing route yet. {UNCONTRACTED}",
    ),
    nat_billing_catalog_file: Optional[str] = typer.Option(
        None, "--nat-billing-catalog-file", metavar="PATH", help="Read --nat-billing-catalog from a JSON file"
    ),
    check_site: bool = typer.Option(
        True, "--check-site/--no-check-site", help="Check that the site supports VPCs before creating (default: on)"
    ),
) -> None:
    """Create a VPC the way the portal does (private by default, or with a managed NAT gateway).

    A nat_gateway VPC created here is not billing-admitted at the API edge; the gateway is
    only metered when the NAT-GATEWAY billing catalog is sent.
    """

    settings, workspace, client = _session(ctx)
    catalog = load_json_input(nat_billing_catalog, nat_billing_catalog_file, "nat-billing-catalog")
    if cidr is None and auto_cidr is None:
        auto_cidr = True  # the portal always sends auto_cidr
    with sdk_warnings():
        vpc = client.vpcs.create_vpc(
            workspace_id=workspace,
            name=name,
            site_id=site_id,
            description=description,
            region=region,
            cidr=cidr,
            auto_cidr=auto_cidr,
            create_default_subnet=create_default_subnet,
            default_subnet_cidr=default_subnet_cidr,
            is_default=is_default or None,
            connectivity_type=connectivity_type,
            nat_billing_catalog=catalog,
            check_site=check_site,
        )
    print_json(vpc, id_field="vpc_id")


@app.command("get")
@handle_api_errors
def get_vpc(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
) -> None:
    """Show a VPC with its subnets, NAT gateways, and attached nodes."""

    _settings, workspace, client = _session(ctx)
    print_json(client.vpcs.get_vpc(vpc_id, workspace_id=workspace), id_field="vpc_id")


@app.command("update")
@handle_api_errors
def update_vpc(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    name: Optional[str] = typer.Option(None, "--name", help="1-80 characters"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Up to 500 characters"),
) -> None:
    """Update the VPC name or description (at least one)."""

    _settings, workspace, client = _session(ctx)
    vpc = client.vpcs.update_vpc(vpc_id, workspace_id=workspace, name=name, description=description)
    print_json(vpc, id_field="vpc_id")


@app.command("delete")
@handle_api_errors
def delete_vpc(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    delete_nat_gateway: bool = typer.Option(
        False, "--delete-nat-gateway", help="Delete the VPC's NAT gateway first, wait for it, then delete the VPC"
    ),
    nat_ip_action: Optional[str] = typer.Option(
        None,
        "--nat-ip-action",
        help="With --delete-nat-gateway: reserve or release the gateway's public IP "
        "(default: a platform IP is released, a Reserved IP stays reserved)",
    ),
    nat_billing_catalog: Optional[str] = typer.Option(
        None, "--nat-billing-catalog", "--nat-billing-catalog-json", metavar="JSON",
        help="RESERVED-IP billing catalog, needed to reserve a platform NAT IP",
    ),
    nat_billing_catalog_file: Optional[str] = typer.Option(
        None, "--nat-billing-catalog-file", metavar="PATH", help="Read --nat-billing-catalog from a JSON file"
    ),
    check_state: bool = check_state_option(),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a VPC. Nodes must be detached and the NAT gateway and virtual IPs removed first."""

    settings, workspace, client = _session(ctx)
    catalog = load_json_input(nat_billing_catalog, nat_billing_catalog_file, "nat-billing-catalog")
    build_nat_delete_body(public_ip_action=nat_ip_action, billing_catalog=catalog)
    if (nat_ip_action is not None or catalog is not None) and not delete_nat_gateway:
        raise IbeeValidationError(
            "--nat-ip-action and --nat-billing-catalog need --delete-nat-gateway.",
            code="invalid_option",
            field="nat_public_ip_action",
        )
    prompt = f"Delete VPC '{vpc_id}'?"
    if check_state:
        vpc = client.vpcs.get_vpc(vpc_id, workspace_id=workspace)
        try:
            check_vpc_deletable(vpc, deleting_nat_gateway=delete_nat_gateway)
        except IbeeValidationError as exc:
            if exc.code == "vpc_has_nat_gateway":
                raise IbeeValidationError(
                    "Delete the NAT gateway first (or pass --delete-nat-gateway).",
                    code=exc.code,
                    field="vpc_id",
                ) from None
            raise
        gateways = _items(record_get(vpc, "nat_gateways"))
        for gateway in gateways if delete_nat_gateway else []:
            build_nat_delete_body(public_ip_action=nat_ip_action, billing_catalog=catalog, gateway=gateway)
        virtual_ips = client.vpcs.list_vpc_virtual_ips(vpc_id, workspace_id=workspace)
        if virtual_ips:
            raise IbeeValidationError(
                "Delete all virtual IP reservations before deleting the VPC.",
                code="vpc_has_virtual_ips",
                field="vpc_id",
            )
        if delete_nat_gateway and gateways:
            prompt = f"Delete NAT gateway(s) and then VPC '{vpc_id}'?"
    confirm_destructive(settings, prompt, yes)
    with api_hints({409: VPC_DELETE_HINT}):
        client.vpcs.delete_vpc(
            vpc_id,
            workspace_id=workspace,
            check_state=check_state,
            delete_nat_gateway=delete_nat_gateway,
            nat_public_ip_action=nat_ip_action,
            nat_billing_catalog=catalog,
        )
    _success(settings, f"VPC '{vpc_id}' deleted.")


# ---------------------------------------------------------------------------
# Subnets
# ---------------------------------------------------------------------------


@subnets_app.command("list")
@handle_api_errors
def list_subnets(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
) -> None:
    """List subnets in a VPC."""

    settings, workspace, client = _session(ctx)
    subnets = client.vpcs.list_vpc_subnets(vpc_id, workspace_id=workspace)
    emit(subnets, settings=settings, default="json", columns=SUBNET_COLUMNS, title="Subnets", id_field="subnet_id")


@subnets_app.command("create")
@handle_api_errors
def create_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    name: str = typer.Argument(..., help="Subnet name (1-80 characters)"),
    cidr: Optional[str] = typer.Option(
        None, "--cidr", help="Sub-range of the VPC CIDR that does not overlap other subnets (/29 or larger)"
    ),
    auto_cidr: Optional[bool] = typer.Option(
        None, "--auto-cidr/--no-auto-cidr", show_default=False,
        help="Allocate the CIDR automatically (default: on unless --cidr is given)",
    ),
    prefix_length: Optional[int] = typer.Option(
        None, "--prefix-length", min=22, max=29, help="Size of an automatic CIDR (22-29, not with --cidr)"
    ),
    dns: Optional[List[str]] = typer.Option(
        None, "--dns", help="IPv4 DNS server (repeatable; default 1.1.1.1 and 8.8.8.8)"
    ),
    check_state: bool = check_state_option(),
) -> None:
    """Create a subnet (at most 10 per VPC)."""

    _settings, workspace, client = _session(ctx)
    subnet = client.vpcs.create_vpc_subnet(
        vpc_id,
        workspace_id=workspace,
        name=name,
        cidr=cidr,
        auto_cidr=auto_cidr,
        prefix_length=prefix_length,
        dns=dns or None,
        check_state=check_state,
    )
    print_json(subnet, id_field="subnet_id")


@subnets_app.command("get")
@handle_api_errors
def get_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
) -> None:
    """Show a VPC subnet."""

    _settings, workspace, client = _session(ctx)
    print_json(client.vpcs.get_vpc_subnet(vpc_id, subnet_id, workspace_id=workspace), id_field="subnet_id")


@subnets_app.command("update")
@handle_api_errors
def update_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
    name: Optional[str] = typer.Option(None, "--name", help="1-80 characters"),
    dns: Optional[List[str]] = typer.Option(None, "--dns", help="IPv4 DNS server (repeatable)"),
) -> None:
    """Update a subnet's name or DNS servers (at least one)."""

    _settings, workspace, client = _session(ctx)
    subnet = client.vpcs.update_vpc_subnet(
        vpc_id, subnet_id, workspace_id=workspace, name=name, dns=dns or None
    )
    print_json(subnet, id_field="subnet_id")


@subnets_app.command("delete")
@handle_api_errors
def delete_subnet(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    subnet_id: str = typer.Argument(..., help="Subnet ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete an unused subnet."""

    settings, workspace, client = _session(ctx)
    confirm_destructive(settings, f"Delete subnet '{subnet_id}'?", yes)
    with api_hints({409: SUBNET_DELETE_HINT}):
        client.vpcs.delete_vpc_subnet(vpc_id, subnet_id, workspace_id=workspace)
    _success(settings, f"Subnet '{subnet_id}' deleted.")


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


@nodes_app.command("list")
@handle_api_errors
def list_nodes(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
) -> None:
    """List VM network allocations in a VPC."""

    settings, workspace, client = _session(ctx)
    nodes = client.vpcs.list_vpc_nodes(vpc_id, workspace_id=workspace)
    emit(nodes, settings=settings, default="json", columns=NODE_COLUMNS, title="VPC nodes", id_field="allocation_id")


@nodes_app.command("attach")
@handle_api_errors
def attach_node(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID (same site as the VPC)"),
    subnet_id: str = typer.Option(..., "--subnet-id", help="Subnet ID"),
    connectivity: Optional[str] = typer.Option(
        None,
        "--connectivity",
        help="private, nat or public_ip (default: nat in a nat_gateway VPC, otherwise private)",
    ),
    reserved_public_ip_id: Optional[str] = typer.Option(
        None, "--reserved-ip-id", help="Reserved IP to use with public_ip connectivity"
    ),
    private_ip: Optional[str] = typer.Option(
        None,
        "--private-ip",
        help=f"Request this private IPv4 (a usable host of the subnet, not its gateway). {UNCONTRACTED}",
    ),
    check_state: bool = check_state_option(),
) -> None:
    """Create a VM's network allocation in a VPC subnet.

    This allocates the address (and NAT or public IP); it does not plug a NIC into the VM.
    The portal's VM-side attach is not yet available in the public API.
    """

    _settings, workspace, client = _session(ctx)
    allocation = client.vpcs.attach_vpc_node(
        vpc_id,
        workspace_id=workspace,
        vm_id=vm_id,
        subnet_id=subnet_id,
        connectivity=connectivity,
        reserved_public_ip_id=reserved_public_ip_id,
        requested_private_ip=private_ip,
        check_state=check_state,
    )
    print_json(allocation, id_field="allocation_id")


@nodes_app.command("detach")
@handle_api_errors
def detach_node(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Release a VM's VPC allocation (forwarding rules that target it are removed)."""

    settings, workspace, client = _session(ctx)
    confirm_destructive(settings, f"Detach VM '{vm_id}' from VPC '{vpc_id}'?", yes)
    with api_hints({409: NODE_DETACH_HINT}):
        client.vpcs.detach_vpc_node(vpc_id, vm_id, workspace_id=workspace)
    _success(settings, f"VM '{vm_id}' detached from VPC '{vpc_id}'.")


# ---------------------------------------------------------------------------
# NAT gateways
# ---------------------------------------------------------------------------


@nat_app.command("list")
@handle_api_errors
def list_nat_gateways(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
) -> None:
    """List NAT gateways in a VPC."""

    settings, workspace, client = _session(ctx)
    gateways = client.vpcs.list_nat_gateways(vpc_id, workspace_id=workspace)
    emit(gateways, settings=settings, default="json", columns=NAT_COLUMNS, title="NAT gateways",
         id_field="nat_gateway_id")


@nat_app.command("create")
@handle_api_errors
def create_nat_gateway(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID (connectivity nat_gateway)"),
    name: str = typer.Option("NAT Gateway", "--name", help="1-80 characters"),
    subnet_id: Optional[str] = typer.Option(None, "--subnet-id"),
    reserved_public_ip_id: Optional[str] = typer.Option(
        None, "--reserved-ip-id", help="Use this Reserved IP (same site, unattached, status reserved)"
    ),
    billing_catalog: Optional[str] = typer.Option(
        None,
        "--billing-catalog",
        "--billing-catalog-json",
        metavar="JSON",
        help="NAT-GATEWAY billing catalog object (needs sku_code); without it the gateway is not metered. "
        f"{UNCONTRACTED}",
    ),
    billing_catalog_file: Optional[str] = typer.Option(
        None, "--billing-catalog-file", metavar="PATH", help="Read --billing-catalog from a JSON file"
    ),
    preflight: bool = typer.Option(
        False, "--preflight", "--preflight-billing",
        help="Check NAT-GATEWAY billing eligibility first (also set by the global --check-billing)",
    ),
    check_state: bool = check_state_option(),
) -> None:
    """Create the managed NAT gateway of a nat_gateway VPC."""

    settings, workspace, client = _session(ctx)
    catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
    if check_state:
        vpc = client.vpcs.get_vpc(vpc_id, workspace_id=workspace)
        check_nat_vpc(vpc)
        existing = _items(record_get(vpc, "nat_gateways"))
        if existing:
            typer.secho(
                "NAT gateway already exists; nothing was created "
                "(use --no-check-state to send the create, which repairs it).",
                fg=typer.colors.YELLOW,
                err=True,
            )
            print_json(existing[0], id_field="nat_gateway_id")
            return
        if reserved_public_ip_id:
            reserved_ip = client.reserved_ips.get_reserved_ip(reserved_public_ip_id.strip(), workspace_id=workspace)
            check_nat_reserved_ip(reserved_ip, record_get(vpc, "site_id"))
    with sdk_warnings():
        gateway = client.vpcs.create_nat_gateway(
            vpc_id,
            workspace_id=workspace,
            subnet_id=subnet_id,
            reserved_public_ip_id=reserved_public_ip_id,
            name=name,
            billing_catalog=catalog,
            preflight_billing=preflight or settings.check_billing,
            check_state=False,
        )
    print_json(gateway, id_field="nat_gateway_id")


@nat_app.command("delete")
@handle_api_errors
def delete_nat_gateway(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    ip_action: Optional[str] = typer.Option(
        None,
        "--ip-action",
        help="reserve (keep the public IP as a Reserved IP; billing continues) or release "
        "(default: a platform IP is released, a Reserved IP stays reserved)",
    ),
    billing_catalog: Optional[str] = typer.Option(
        None, "--billing-catalog", "--billing-catalog-json", metavar="JSON",
        help="RESERVED-IP billing catalog; needed to reserve a platform-assigned IP",
    ),
    billing_catalog_file: Optional[str] = typer.Option(
        None, "--billing-catalog-file", metavar="PATH", help="Read --billing-catalog from a JSON file"
    ),
    wait: bool = typer.Option(False, "--wait", help="Wait until the gateway is gone (up to 10 s)"),
    check_state: bool = check_state_option(),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a NAT gateway: NAT nodes lose outbound internet and its forwarding rules are deleted."""

    settings, workspace, client = _session(ctx)
    catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
    build_nat_delete_body(public_ip_action=ip_action, billing_catalog=catalog)
    prompt = f"Delete NAT gateway '{nat_gateway_id}'?"
    if check_state:
        gateways = client.vpcs.list_nat_gateways(vpc_id, workspace_id=workspace)
        gateway = next((g for g in gateways if record_get(g, "nat_gateway_id") == nat_gateway_id), None)
        if gateway is None:
            raise IbeeValidationError(
                f"NAT gateway {nat_gateway_id} was not found in this VPC.",
                code="nat_gateway_not_found",
                field="nat_gateway_id",
            )
        build_nat_delete_body(public_ip_action=ip_action, billing_catalog=catalog, gateway=gateway)
        virtual_ips = client.vpcs.list_vpc_virtual_ips(vpc_id, workspace_id=workspace)
        if any(record_get(vip, "public_ip_id") for vip in virtual_ips):
            raise IbeeValidationError(
                "Detach virtual-IP Reserved Public IPs before deleting the NAT gateway.",
                code="virtual_ip_has_reserved_ip",
                field="nat_gateway_id",
            )
        nodes = client.vpcs.list_vpc_nodes(vpc_id, workspace_id=workspace)
        nat_nodes = sum(1 for node in nodes if _value(node, "connectivity") == "nat")
        rules = client.vpcs.list_nat_port_forwarding_rules(vpc_id, nat_gateway_id, workspace_id=workspace)
        if nat_nodes or rules:
            prompt = (
                f"{nat_nodes} VM(s) will lose managed outbound internet, and {len(rules)} port forwarding "
                f"rule(s) will be permanently deleted. Delete NAT gateway '{nat_gateway_id}'?"
            )
    confirm_destructive(settings, prompt, yes)
    gone = client.vpcs.delete_nat_gateway(
        vpc_id,
        nat_gateway_id,
        workspace_id=workspace,
        public_ip_action=ip_action,
        billing_catalog=catalog,
        check_state=False,
        wait=wait,
        wait_attempts=NAT_DELETE_WAIT_ATTEMPTS,
        wait_interval=NAT_DELETE_WAIT_INTERVAL,
    )
    if wait and gone is False:
        typer.secho(
            f"NAT gateway '{nat_gateway_id}' deletion is still reconciling; check with: ibee vpcs nat list {vpc_id}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=EXIT_WAIT_TIMEOUT)
    if wait:
        _success(settings, f"NAT gateway '{nat_gateway_id}' deleted.")
    else:
        _success(settings, f"NAT gateway '{nat_gateway_id}' deletion accepted; deletion is still reconciling.")


@nat_app.command("replace-ip")
@handle_api_errors
def replace_nat_public_ip(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    reserved_public_ip_id: str = typer.Option(
        ..., "--reserved-ip-id", help="Reserved IP to use (same site, unattached, status reserved)"
    ),
    check_state: bool = check_state_option(),
) -> None:
    """Switch the NAT gateway's public IP to one of your Reserved IPs (forwarding rules are kept).

    Not yet part of the published API contract; behaviour may change.
    """

    _settings, workspace, client = _session(ctx)
    gateway = client.vpcs.replace_nat_gateway_public_ip(
        vpc_id,
        nat_gateway_id,
        workspace_id=workspace,
        reserved_public_ip_id=reserved_public_ip_id,
        check_state=check_state,
    )
    print_json(gateway, id_field="nat_gateway_id")


# ---------------------------------------------------------------------------
# Port forwarding
# ---------------------------------------------------------------------------


@forwarding_app.command("list")
@handle_api_errors
def list_forwarding_rules(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
) -> None:
    """List port-forwarding rules on a NAT gateway."""

    settings, workspace, client = _session(ctx)
    rules = client.vpcs.list_nat_port_forwarding_rules(vpc_id, nat_gateway_id, workspace_id=workspace)
    emit(rules, settings=settings, default="json", columns=RULE_COLUMNS, title="Port-forwarding rules",
         id_field="port_forward_rule_id")


def _target_vm_ids(values: Optional[List[str]]) -> Optional[List[str]]:
    return list(values) if values else None


@forwarding_app.command("create")
@handle_api_errors
def create_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID (must be available)"),
    name: str = typer.Argument(..., help="Rule name (1-80 characters)"),
    external_port: int = typer.Option(..., "--external-port", help="Public port 1-65535 (single port)"),
    internal_ip: str = typer.Option(
        ..., "--internal-ip", help="Private IP of a NAT-connected VM, or of a MetalLB virtual IP (--target vip)"
    ),
    internal_port: int = typer.Option(..., "--internal-port", help="Port 1-65535 (portal default 22)"),
    protocol: str = typer.Option("tcp", "--protocol", help="tcp or udp"),
    target: Optional[str] = typer.Option(
        None, "--target", help=f"vm (default) or vip. {UNCONTRACTED}"
    ),
    target_vm_id: Optional[List[str]] = typer.Option(
        None, "--target-vm-id", help="VIP announcer VM (repeatable; default: the VIP's announcers)"
    ),
    note: str = typer.Option("", "--note", help="Up to 500 characters"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
    check_state: bool = check_state_option(),
) -> None:
    """Create a NAT port-forwarding rule (one external port per protocol per gateway)."""

    _settings, workspace, client = _session(ctx)
    rule = client.vpcs.create_nat_port_forwarding_rule(
        vpc_id,
        nat_gateway_id,
        workspace_id=workspace,
        name=name,
        external_port=external_port,
        internal_ip=internal_ip,
        internal_port=internal_port,
        protocol=protocol,
        note=note,
        enabled=enabled,
        target_type=target,
        target_vm_ids=_target_vm_ids(target_vm_id),
        check_state=check_state,
    )
    print_json(rule, id_field="port_forward_rule_id")


def _update_rule(
    ctx: typer.Context,
    vpc_id: str,
    nat_gateway_id: str,
    rule_id: str,
    check_state: bool,
    **fields: Any,
) -> None:
    _settings, workspace, client = _session(ctx)
    rule = client.vpcs.update_nat_port_forwarding_rule(
        vpc_id,
        nat_gateway_id,
        rule_id,
        workspace_id=workspace,
        check_state=check_state,
        **fields,
    )
    print_json(rule, id_field="port_forward_rule_id")


@forwarding_app.command("update")
@handle_api_errors
def update_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    rule_id: str = typer.Argument(..., help="Port-forwarding rule ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    external_port: Optional[int] = typer.Option(None, "--external-port", help="1-65535"),
    internal_ip: Optional[str] = typer.Option(None, "--internal-ip"),
    internal_port: Optional[int] = typer.Option(None, "--internal-port", help="1-65535"),
    protocol: Optional[str] = typer.Option(None, "--protocol", help="tcp or udp"),
    target: Optional[str] = typer.Option(None, "--target", help=f"vm or vip. {UNCONTRACTED}"),
    target_vm_id: Optional[List[str]] = typer.Option(
        None, "--target-vm-id", help="VIP announcer VM (repeatable; needs --target)"
    ),
    note: Optional[str] = typer.Option(None, "--note"),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled"),
    check_state: bool = check_state_option(),
) -> None:
    """Update a NAT port-forwarding rule (at least one option)."""

    _update_rule(
        ctx,
        vpc_id,
        nat_gateway_id,
        rule_id,
        check_state,
        name=name,
        external_port=external_port,
        internal_ip=internal_ip,
        internal_port=internal_port,
        protocol=protocol,
        note=note,
        enabled=enabled,
        target_type=target,
        target_vm_ids=_target_vm_ids(target_vm_id),
    )


@forwarding_app.command("enable")
@handle_api_errors
def enable_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID (must be available)"),
    rule_id: str = typer.Argument(..., help="Port-forwarding rule ID"),
    check_state: bool = check_state_option(),
) -> None:
    """Enable a port-forwarding rule."""

    _update_rule(ctx, vpc_id, nat_gateway_id, rule_id, check_state, enabled=True)


@forwarding_app.command("disable")
@handle_api_errors
def disable_forwarding_rule(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    nat_gateway_id: str = typer.Argument(..., help="NAT gateway ID"),
    rule_id: str = typer.Argument(..., help="Port-forwarding rule ID"),
    check_state: bool = check_state_option(),
) -> None:
    """Disable a port-forwarding rule."""

    _update_rule(ctx, vpc_id, nat_gateway_id, rule_id, check_state, enabled=False)


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

    settings, workspace, client = _session(ctx)
    confirm_destructive(settings, f"Delete port-forwarding rule '{rule_id}'?", yes)
    client.vpcs.delete_nat_port_forwarding_rule(vpc_id, nat_gateway_id, rule_id, workspace_id=workspace)
    _success(settings, f"Port-forwarding rule '{rule_id}' deleted.")


# ---------------------------------------------------------------------------
# Virtual IPs (not yet part of the published API contract)
# ---------------------------------------------------------------------------


@virtual_ips_app.command("list")
@handle_api_errors
def list_virtual_ips(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
) -> None:
    """List virtual IPs reserved in a VPC."""

    settings, workspace, client = _session(ctx)
    virtual_ips = client.vpcs.list_vpc_virtual_ips(vpc_id, workspace_id=workspace)
    emit(virtual_ips, settings=settings, default="json", columns=VIP_COLUMNS, title="Virtual IPs",
         id_field="virtual_ip_id")


@virtual_ips_app.command("get")
@handle_api_errors
def get_virtual_ip(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    virtual_ip_id: str = typer.Argument(..., help="Virtual IP ID"),
) -> None:
    """Show one virtual IP."""

    _settings, workspace, client = _session(ctx)
    print_json(client.vpcs.get_vpc_virtual_ip(vpc_id, virtual_ip_id, workspace_id=workspace),
               id_field="virtual_ip_id")


@virtual_ips_app.command("create")
@handle_api_errors
def create_virtual_ip(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    subnet_id: str = typer.Option(..., "--subnet-id", help="Subnet ID"),
    private_ip: str = typer.Option(
        ..., "--private-ip", help="A usable host address of the subnet (not its network, broadcast or gateway)"
    ),
    announcer_vm_id: Optional[List[str]] = typer.Option(
        None,
        "--announcer-vm-id",
        help="NAT-connected VM in the same subnet that announces the address (repeatable, 1-32; required for metallb)",
    ),
    purpose: str = typer.Option("metallb", "--purpose", help="metallb (portal default) or custom"),
    check_state: bool = check_state_option(),
) -> None:
    """Reserve a virtual IP in a VPC subnet."""

    _settings, workspace, client = _session(ctx)
    virtual_ip = client.vpcs.create_vpc_virtual_ip(
        vpc_id,
        workspace_id=workspace,
        subnet_id=subnet_id,
        private_ip=private_ip,
        announcer_vm_ids=list(announcer_vm_id or []),
        purpose=purpose,
        check_state=check_state,
    )
    print_json(virtual_ip, id_field="virtual_ip_id")


@virtual_ips_app.command("delete")
@handle_api_errors
def delete_virtual_ip(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    virtual_ip_id: str = typer.Argument(..., help="Virtual IP ID"),
    check_state: bool = check_state_option(),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Release a virtual IP reservation (detach its Reserved IP and forwarding rules first)."""

    settings, workspace, client = _session(ctx)
    if check_state:
        vip = client.vpcs.get_vpc_virtual_ip(vpc_id, virtual_ip_id, workspace_id=workspace)
        check_virtual_ip_deletable(vip)
        private_ip = record_get(vip, "private_ip")
        for gateway in client.vpcs.list_nat_gateways(vpc_id, workspace_id=workspace):
            gateway_id = record_get(gateway, "nat_gateway_id")
            rules = client.vpcs.list_nat_port_forwarding_rules(vpc_id, gateway_id, workspace_id=workspace)
            if any(record_get(rule, "internal_ip") == private_ip for rule in rules):
                raise IbeeValidationError(
                    "Delete port-forwarding rules that target this virtual IP first.",
                    code="virtual_ip_has_rules",
                    field="virtual_ip_id",
                )
    confirm_destructive(settings, f"Delete virtual IP '{virtual_ip_id}'?", yes)
    client.vpcs.delete_vpc_virtual_ip(vpc_id, virtual_ip_id, workspace_id=workspace, check_state=False)
    _success(settings, f"Virtual IP '{virtual_ip_id}' deleted.")


@virtual_ips_app.command("attach-ip")
@handle_api_errors
def attach_virtual_ip_reserved_ip(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    virtual_ip_id: str = typer.Argument(..., help="Virtual IP ID"),
    reserved_ip_id: str = typer.Option(
        ..., "--reserved-ip-id", help="Unattached Reserved IP in the same site (1:1 NAT to the virtual IP)"
    ),
    check_state: bool = check_state_option(),
) -> None:
    """Attach a Reserved IP to a virtual IP (same as 'ibee reserved-ips attach-virtual-ip')."""

    _settings, workspace, client = _session(ctx)
    if check_state:
        vip = client.vpcs.get_vpc_virtual_ip(vpc_id, virtual_ip_id, workspace_id=workspace)
        if _status(vip) and _status(vip) != "available":
            raise IbeeValidationError(
                f"Virtual IP {virtual_ip_id} is {_status(vip)}; it must be available.",
                code="virtual_ip_unavailable",
                field="virtual_ip_id",
            )
        current = record_get(vip, "public_ip_id")
        if current and current != reserved_ip_id.strip():
            raise IbeeValidationError(
                "This virtual IP already has another Reserved IP; detach it first.",
                code="virtual_ip_has_reserved_ip",
                field="virtual_ip_id",
            )
        vpc = client.vpcs.get_vpc(vpc_id, workspace_id=workspace)
        if _value(vpc, "connectivity_type") == "nat_gateway":
            if available_nat_gateway(vpc) is None:
                raise IbeeValidationError(
                    "This VPC needs an available NAT gateway before a Reserved IP can be attached to a virtual IP.",
                    code="nat_gateway_unavailable",
                    field="virtual_ip_id",
                )
    reserved = client.reserved_ips.attach_reserved_ip_to_virtual_ip(
        reserved_ip_id, workspace_id=workspace, virtual_ip_id=virtual_ip_id, check_state=check_state
    )
    print_json(reserved, id_field="public_ip_id")


@virtual_ips_app.command("detach-ip")
@handle_api_errors
def detach_virtual_ip_reserved_ip(
    ctx: typer.Context,
    vpc_id: str = typer.Argument(..., help="VPC ID"),
    virtual_ip_id: str = typer.Argument(..., help="Virtual IP ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Detach the Reserved IP attached to a virtual IP (it stays reserved)."""

    settings, workspace, client = _session(ctx)
    vip = client.vpcs.get_vpc_virtual_ip(vpc_id, virtual_ip_id, workspace_id=workspace)
    reserved_ip_id = record_get(vip, "public_ip_id")
    if not reserved_ip_id:
        raise IbeeValidationError(
            "This virtual IP has no Reserved IP attached.", code="virtual_ip_no_reserved_ip", field="virtual_ip_id"
        )
    confirm_destructive(
        settings, f"Detach Reserved IP '{reserved_ip_id}' from virtual IP '{virtual_ip_id}'?", yes
    )
    reserved = client.reserved_ips.detach_reserved_ip(reserved_ip_id, workspace_id=workspace, check_state=False)
    print_json(reserved, id_field="public_ip_id")
