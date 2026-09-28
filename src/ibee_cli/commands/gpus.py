"""GPU VM commands."""

from __future__ import annotations

from typing import List, Optional

from ibee.validation import SORT_DIRECTIONS, VM_SORT_FIELDS, validate_vm_list_params

import typer

from ..context import get_client, get_settings, require_workspace
from ..helpers import (
    check_state_option,
    idempotency_key_option,
    poll_interval_option,
    response_items,
    timeout_option,
)
from ..render import cell, handle_api_errors, print_json, print_table
from ibee.validation import BILLING_TERMS, MAX_BATCH_VMS, NETWORK_CONNECTIVITY

from .vm_lifecycle import GPU_VM, create_vms, register_vm_lifecycle
from .vm_lifecycle import delete_vm as run_delete_vm
from .vm_lifecycle import power_action as run_power_action

app = typer.Typer(help="GPU VMs", no_args_is_help=True)
register_vm_lifecycle(app, GPU_VM)


@app.command("list")
@handle_api_errors
def list_gpu_vms(
    ctx: typer.Context,
    limit: Optional[int] = typer.Option(
        None, "--limit", min=1, max=100, help="Return one page of at most N VMs (1-100)"
    ),
    offset: Optional[int] = typer.Option(None, "--offset", min=0, help="Skip N VMs (one page)"),
    search: Optional[str] = typer.Option(None, "--search", help="Filter by text (max 120 characters)"),
    sort_by: Optional[str] = typer.Option(
        None, "--sort-by", help="Sort field: " + ", ".join(VM_SORT_FIELDS)
    ),
    sort_direction: Optional[str] = typer.Option(
        None, "--sort-direction", help="Sort direction: " + ", ".join(SORT_DIRECTIONS)
    ),
    all_pages: bool = typer.Option(
        False, "--all", help="Fetch every page (the default without --limit/--offset)"
    ),
) -> None:
    """List GPU VMs in the workspace.

    Without --limit/--offset every page is fetched; with either, one page is returned. The paging, search and sort options are not yet part of the published API contract; behaviour may change.
    """
    settings = get_settings(ctx)
    if all_pages and (limit is not None or offset is not None):
        raise typer.BadParameter("--all cannot be combined with --limit or --offset.")
    paging = validate_vm_list_params(
        limit=limit, offset=offset, search=search, sort_by=sort_by, sort_direction=sort_direction
    )
    workspace = require_workspace(settings)
    client = get_client(settings)
    result = client.gpu_vms.list_gpu_vms(workspace_id=workspace, **paging)
    if settings.structured_output:
        print_json(result)
        return
    vms = response_items(result, "items", "vms")
    print_table(
        "GPU VMs",
        ["ID", "Name", "Status", "GPU", "GPUs", "CPU", "RAM (MB)", "Public IP"],
        [
            tuple(
                cell(v, name)
                for name in (
                    "id", "name", "status", "gpu_model", "gpu_count", "cpu", "ram_mb", "public_ip"
                )
            )
            for v in vms
        ],
    )


@app.command("get")
@handle_api_errors
def get_gpu_vm(ctx: typer.Context, vm_id: str = typer.Argument(..., help="GPU VM ID")) -> None:
    """Show one GPU VM."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.gpu_vms.get_gpu_vm(workspace_id=require_workspace(settings), vm_id=vm_id)
    print_json(result)


@app.command("create")
@handle_api_errors
def create_gpu_vm(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Hostname: letters, digits and '-' (with --count, the base name)"),
    site_id: Optional[str] = typer.Option(
        None, "--site-id", help="Site to place the VM in (required). See `ibee compute sites`."
    ),
    plan_id: str = typer.Option(..., "--plan-id", help="Plan ID for the site. See `ibee compute plans --vm-type gpu`."),
    template_id: str = typer.Option(
        ..., "--template-id", help="OS image ID for the site. See `ibee compute images --vm-type gpu`."
    ),
    gpu_model: Optional[str] = typer.Option(
        None, "--gpu-model", help="GPU model (default: the plan's; must match it)"
    ),
    gpu_count: Optional[int] = typer.Option(None, "--gpu-count", help="GPUs (default: the plan's; must match it)"),
    os_type: Optional[str] = typer.Option(None, "--os-type", help="linux (GPU VMs need Linux images)"),
    os_distro: Optional[str] = typer.Option(None, "--os-distro", help="Default: the image's distribution"),
    cpu: Optional[int] = typer.Option(None, "--cpu", help="vCPUs (default: the plan's; must match it)"),
    ram_mb: Optional[int] = typer.Option(None, "--ram-mb", help="RAM in MB (default: the plan's; must match it)"),
    disk_gb: Optional[int] = typer.Option(None, "--disk-gb", help="Root disk in GB (default: the plan's; must match it)"),
    billing_term: Optional[str] = typer.Option(
        None, "--billing-term", help="Billing term: " + ", ".join(BILLING_TERMS) + " (default: the plan's SKU as is, billed hourly)"
    ),
    billing_catalog: Optional[str] = typer.Option(
        None, "--billing-catalog", help="Advanced: billing SKU JSON to send instead of the plan's"
    ),
    billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
    ssh_key: Optional[List[str]] = typer.Option(
        None, "--ssh-key", help="Public SSH key to install, e.g. 'ssh-ed25519 AAAA... me' (repeatable; recommended)"
    ),
    ssh_key_file: Optional[List[str]] = typer.Option(
        None, "--ssh-key-file", help="File with one public SSH key, e.g. ~/.ssh/id_ed25519.pub (repeatable)"
    ),
    ssh_key_id: Optional[List[str]] = typer.Option(
        None,
        "--ssh-key-id",
        help="Saved SSH key ID (repeatable). Saved keys resolve for portal users only; prefer --ssh-key.",
    ),
    firewall_group_id: Optional[List[str]] = typer.Option(
        None, "--firewall-group-id", help="Firewall group to apply (at most one)"
    ),
    vpc_id: Optional[str] = typer.Option(None, "--vpc-id", help="VPC to attach to (with --subnet-id)"),
    subnet_id: Optional[str] = typer.Option(None, "--subnet-id", help="Subnet of --vpc-id"),
    network_connectivity: Optional[str] = typer.Option(
        None,
        "--network-connectivity",
        help="With a VPC: " + ", ".join(NETWORK_CONNECTIVITY) + " (default private; nat needs a NAT Gateway VPC)",
    ),
    reserved_public_ip_id: Optional[str] = typer.Option(
        None,
        "--reserved-public-ip-id",
        help="Unattached Reserved IP in the same site (with --network-connectivity public_ip; one VM only)",
    ),
    count: int = typer.Option(1, "--count", min=1, max=MAX_BATCH_VMS, help="Number of VMs to create (1-5)"),
    instance_name: Optional[List[str]] = typer.Option(
        None, "--instance-name", help="Hostname for VM N in order, instead of NAME-N (repeatable)"
    ),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag (repeatable)"),
    requested_by: Optional[str] = typer.Option(None, "--requested-by", help="Audit label (1-128 characters)"),
    preflight: bool = typer.Option(
        False,
        "--preflight-billing",
        help="Check billing eligibility for the plan SKU first (same as the global --check-billing)",
    ),
    wait: bool = typer.Option(False, "--wait", help="Poll until the VM is provisioned"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Create a GPU VM (async; returns an operation).

    The plan and image are looked up for --site-id: GPU model and count, CPU, RAM, disk and OS come from them, and the plan's billing SKU is sent. --count creates up to 5 VMs named NAME-1..NAME-N.
    """
    create_vms(
        ctx,
        GPU_VM,
        name=name,
        count=count,
        instance_names=instance_name,
        site_id=site_id,
        plan_id=plan_id,
        template_id=template_id,
        os_type=os_type,
        os_distro=os_distro,
        cpu=cpu,
        ram_mb=ram_mb,
        disk_gb=disk_gb,
        billing_term=billing_term,
        billing_catalog=billing_catalog,
        billing_catalog_file=billing_catalog_file,
        gpu_model=gpu_model,
        gpu_count=gpu_count,
        ssh_keys=ssh_key,
        ssh_key_files=ssh_key_file,
        ssh_key_ids=ssh_key_id,
        firewall_group_ids=firewall_group_id,
        vpc_id=vpc_id,
        subnet_id=subnet_id,
        network_connectivity=network_connectivity,
        reserved_public_ip_id=reserved_public_ip_id,
        tags=tag,
        requested_by=requested_by,
        preflight=preflight,
        wait=wait,
        timeout=timeout,
        poll_interval=poll_interval,
        idempotency_key=idempotency_key,
        client_factory=lambda settings: get_client(settings),
    )


@app.command("delete")
@handle_api_errors
def delete_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(..., help="GPU VM ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation (an auto-assigned public IP is released)"),
    release_public_ip: bool = typer.Option(
        False, "--release-public-ip", help="Release the auto-assigned public IP (the default)"
    ),
    reserve_public_ip: bool = typer.Option(
        False, "--reserve-public-ip", help="Keep the auto-assigned public IP as a Reserved IP (billing continues)"
    ),
    reserved_ip_label: Optional[str] = typer.Option(
        None, "--reserved-ip-label", help="Label for the Reserved IP (default: the VM name; max 120)"
    ),
    reserved_ip_billing_catalog: Optional[str] = typer.Option(
        None,
        "--reserved-ip-billing-catalog",
        help="Reserved IP SKU JSON (needed to reserve; copy billing_catalog from a Reserved IP in the same site)",
    ),
    reserved_ip_billing_catalog_file: Optional[str] = typer.Option(None, "--reserved-ip-billing-catalog-file"),
    requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    preflight: bool = typer.Option(
        False, "--preflight-billing", help="With --reserve-public-ip, check billing eligibility for the Reserved IP SKU"
    ),
    check_state: bool = check_state_option(),
    wait: bool = typer.Option(False, "--wait", help="Poll until the VM is deleted"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Delete a GPU VM (async; returns an operation).

    When the VM has an auto-assigned public IP you are asked whether to keep it as a Reserved IP (default: release it). Attached data volumes are detached and kept.
    """
    run_delete_vm(
        ctx,
        GPU_VM,
        vm_id=vm_id,
        yes=yes,
        reserve_public_ip=reserve_public_ip,
        release_public_ip=release_public_ip,
        reserved_ip_label=reserved_ip_label,
        reserved_ip_billing_catalog=reserved_ip_billing_catalog,
        reserved_ip_billing_catalog_file=reserved_ip_billing_catalog_file,
        requested_by=requested_by,
        preflight=preflight,
        check_state=check_state,
        wait=wait,
        timeout=timeout,
        poll_interval=poll_interval,
        idempotency_key=idempotency_key,
        client_factory=lambda settings: get_client(settings),
    )

@app.command("metrics")
@handle_api_errors
def gpu_metrics(ctx: typer.Context, vm_id: str = typer.Argument(..., help="GPU VM ID")) -> None:
    """Show current resource-usage metrics for a GPU VM."""
    settings = get_settings(ctx)
    client = get_client(settings)
    # SDK method was renamed get_gpu_vm_metrics -> get_gpu_vm_metrics_overview
    # (ibee 0.2.0); accept either so the CLI works across SDK versions.
    method = getattr(client.gpu_vms, "get_gpu_vm_metrics_overview", None) or client.gpu_vms.get_gpu_vm_metrics
    result = method(workspace_id=require_workspace(settings), vm_id=vm_id)
    print_json(result)


@app.command("start")
@handle_api_errors
def start_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    force: Optional[bool] = typer.Option(None, "--force/--no-force"),
    check_state: bool = check_state_option(),
    wait: bool = typer.Option(False, "--wait", help="Poll until started"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Start a GPU VM (the VM must be stopped)."""
    run_power_action(
        ctx, GPU_VM, vm_id, "start", wait, force, timeout, poll_interval, idempotency_key, check_state,
        client_factory=lambda settings: get_client(settings),
    )


@app.command("stop")
@handle_api_errors
def stop_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    force: Optional[bool] = typer.Option(None, "--force/--no-force"),
    check_state: bool = check_state_option(),
    wait: bool = typer.Option(False, "--wait", help="Poll until stopped"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Stop a GPU VM (the VM must be running)."""
    run_power_action(
        ctx, GPU_VM, vm_id, "stop", wait, force, timeout, poll_interval, idempotency_key, check_state,
        client_factory=lambda settings: get_client(settings),
    )


@app.command("reboot")
@handle_api_errors
def reboot_gpu_vm(
    ctx: typer.Context,
    vm_id: str = typer.Argument(...),
    force: Optional[bool] = typer.Option(None, "--force/--no-force"),
    check_state: bool = check_state_option(),
    wait: bool = typer.Option(False, "--wait", help="Poll until rebooted"),
    timeout: Optional[float] = timeout_option(),
    poll_interval: Optional[float] = poll_interval_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Reboot a GPU VM (the VM must be running)."""
    run_power_action(
        ctx, GPU_VM, vm_id, "reboot", wait, force, timeout, poll_interval, idempotency_key, check_state,
        client_factory=lambda settings: get_client(settings),
    )
