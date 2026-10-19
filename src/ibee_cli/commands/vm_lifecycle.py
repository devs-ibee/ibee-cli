"""Cloud VM and GPU VM commands (``ibee vms`` and ``ibee gpus``).

Each command makes one SDK call (the SDK's held VM create, delete, attach and resize
behaviour applies) and prints the server's answer. The server decides everything
else; ``--wait`` polls the operation.
"""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from typing import Any, List, Optional

import typer

from ..context import get_client, get_settings
from ..helpers import (
    compact_payload,
    confirm_destructive,
    field,
    finish_operation,
    idempotency_key_option,
    load_json_input,
    parse_json_object,
    poll_interval_option,
    read_text_files,
    response_items,
    timeout_option,
    wait_option,
)
from ..render import cell, handle_api_errors, print_json, print_table


@dataclass(frozen=True)
class VmCommandSpec:
    """Names that differ between the cloud and GPU resources of the SDK."""

    kind: str
    resource: str
    fragment: str
    label: str


CLOUD_VM = VmCommandSpec("cloud", "cloud_vms", "cloud_vm", "cloud VM")
GPU_VM = VmCommandSpec("gpu", "gpu_vms", "gpu_vm", "GPU VM")


def _sdk(ctx: typer.Context, spec: VmCommandSpec, template: str) -> Any:
    """``(settings, client, method)`` for ``template`` such as ``"get_{}"``."""
    settings = get_settings(ctx)
    client = get_client(settings)
    return settings, client, getattr(getattr(client, spec.resource), template.format(spec.fragment))


def _read_new_password(password_stdin: bool, prompt_password: bool) -> Optional[str]:
    if password_stdin and prompt_password:
        raise typer.BadParameter("Use only one of --password-stdin or --prompt-password.")
    if prompt_password:
        return typer.prompt("New password", hide_input=True, confirmation_prompt=True)
    if password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    return None


def _parse_datetime(value: str) -> dt.datetime:
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter("Use an ISO 8601 timestamp, for example 2026-08-10T02:00:00Z.") from exc


def _batch_names(name: str, count: int, instance_names: Optional[List[str]]) -> List[str]:
    if instance_names:
        return list(instance_names)
    return [name] if count == 1 else [f"{name}-{index}" for index in range(1, count + 1)]


def _restore_options(values: dict) -> dict:
    """The restore fields given on the command line (every ``--target-*`` option and a few more)."""
    keep = ("selected_volume_id", "requested_by", "auto_start")
    return compact_payload(**{k: v for k, v in values.items() if k.startswith("target_") or k in keep})


def _schedule(**values: Any) -> Optional[dict]:
    return compact_payload(**values) or None


def register_vm_commands(app: typer.Typer, spec: VmCommandSpec) -> None:
    """Attach every VM command to ``app`` (``vms`` or ``gpus``)."""

    label = spec.label
    title = label[0].upper() + label[1:]
    snapshots = typer.Typer(help=f"{title} snapshots", no_args_is_help=True)
    backup_policy = typer.Typer(help=f"{title} automated backup policy", no_args_is_help=True)
    backups = typer.Typer(help=f"{title} backup runs and restores", no_args_is_help=True)
    app.add_typer(snapshots, name="snapshots")
    app.add_typer(backup_policy, name="backup-policy")
    app.add_typer(backups, name="backups")

    # -- list, get, create, delete, power ------------------------------------

    @app.command("list")
    @handle_api_errors
    def list_vms(
        ctx: typer.Context,
        limit: Optional[int] = typer.Option(None, "--limit", help="Return one page of at most N VMs"),
        offset: Optional[int] = typer.Option(None, "--offset", help="Skip N VMs (one page)"),
        all_pages: bool = typer.Option(False, "--all", help="Fetch every page (the default without --limit/--offset)"),
    ) -> None:
        """List VMs in the workspace (every page unless --limit or --offset is given)."""
        if all_pages and (limit is not None or offset is not None):
            raise typer.BadParameter("--all cannot be combined with --limit or --offset.")
        settings, _client, method = _sdk(ctx, spec, "list_{}s")
        result = method(**compact_payload(limit=limit, offset=offset))
        if settings.structured_output:
            print_json(result)
            return
        columns = ("id", "name", "status", "cpu", "ram_mb", "public_ip")
        print_table(
            f"{title}s",
            ["ID", "Name", "Status", "CPU", "RAM (MB)", "Public IP"],
            [tuple(cell(vm, name) for name in columns) for vm in response_items(result)],
        )

    @app.command("get")
    @handle_api_errors
    def get_vm(ctx: typer.Context, vm_id: str = typer.Argument(..., help="VM ID")) -> None:
        """Show one VM."""
        _settings, _client, method = _sdk(ctx, spec, "get_{}")
        print_json(method(vm_id))

    @app.command("metrics")
    @handle_api_errors
    def vm_metrics(ctx: typer.Context, vm_id: str = typer.Argument(..., help="VM ID")) -> None:
        """Show current resource-usage metrics."""
        _settings, _client, method = _sdk(ctx, spec, "get_{}_metrics")
        print_json(method(vm_id))

    def create_command(
        ctx: typer.Context,
        name: str,
        options: dict,
        count: int,
        instance_names: Optional[List[str]],
        wait: bool,
        timeout: float,
        poll_interval: float,
        idempotency_key: Optional[str],
    ) -> None:
        names = _batch_names(name, count, instance_names)
        settings, client, method = _sdk(ctx, spec, "create_{}")
        results = []
        for index, vm_name in enumerate(names, start=1):
            key = idempotency_key if idempotency_key is None or len(names) == 1 else f"{idempotency_key}-{index}"
            try:
                results.append(method(name=vm_name, **compact_payload(idempotency_key=key, **options)))
            except Exception:
                if len(names) > 1:
                    typer.secho(f"Created {len(results)} of {len(names)} {label}s; stopped at {vm_name}.", fg=typer.colors.YELLOW, err=True)
                    for done, result in zip(names, results):
                        finish_operation(settings, client, result, "Create", done)
                raise
        for vm_name, result in zip(names, results):
            finish_operation(settings, client, result, "Create", vm_name, wait, timeout, poll_interval)

    def create_options(**values: Any) -> dict:
        keys = [*(values.pop("ssh_key") or []), *read_text_files(values.pop("ssh_key_file"), "--ssh-key-file")]
        values["billing_catalog"] = load_json_input(values.pop("billing_catalog"), values.pop("billing_catalog_file"), "billing-catalog")
        values["windows_license"] = load_json_input(values.pop("windows_license"), values.pop("windows_license_file"), "windows-license")
        return compact_payload(ssh_keys=keys or None, **values)

    if spec.kind == "cloud":

        @app.command("create")
        @handle_api_errors
        def create_vm(
            ctx: typer.Context,
            name: str = typer.Argument(..., help="VM name (with --count, the base name)"),
            site_id: str = typer.Option(..., "--site-id", help="Site (required). See `ibee compute sites`."),
            plan_id: str = typer.Option(..., "--plan-id", help="Plan. See `ibee compute plans --vm-type cloud`."),
            template_id: str = typer.Option(..., "--template-id", help="Image. See `ibee compute images --vm-type cloud`."),
            os_type: Optional[str] = typer.Option(None, "--os-type", help="Default: the image's"),
            os_distro: Optional[str] = typer.Option(None, "--os-distro", help="Default: the image's"),
            cpu: Optional[int] = typer.Option(None, "--cpu", help="Default: the plan's"),
            ram_mb: Optional[int] = typer.Option(None, "--ram-mb", help="Default: the plan's"),
            disk_gb: Optional[int] = typer.Option(None, "--disk-gb", help="Default: the plan's"),
            billing_term: Optional[str] = typer.Option(None, "--billing-term", help="HOURLY or MONTHLY (default HOURLY when the plan offers it)"),
            windows_license: Optional[str] = typer.Option(None, "--windows-license", help="Windows licence SKU as JSON"),
            windows_license_file: Optional[str] = typer.Option(None, "--windows-license-file"),
            billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="Billing SKU JSON to send instead of the plan's"),
            billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
            ssh_key: Optional[List[str]] = typer.Option(None, "--ssh-key", help="Public SSH key (repeatable)"),
            ssh_key_file: Optional[List[str]] = typer.Option(None, "--ssh-key-file", help="File with one public SSH key (repeatable)"),
            ssh_key_id: Optional[List[str]] = typer.Option(None, "--ssh-key-id", help="Saved SSH key ID (repeatable)"),
            vpc_id: Optional[str] = typer.Option(None, "--vpc-id", help="VPC (with --subnet-id)"),
            subnet_id: Optional[str] = typer.Option(None, "--subnet-id"),
            network_connectivity: Optional[str] = typer.Option(None, "--network-connectivity", help="private, nat_gateway or public_ip"),
            vpc_attachment_mode: Optional[str] = typer.Option(None, "--vpc-attachment-mode", help="primary or secondary"),
            reserved_public_ip_id: Optional[str] = typer.Option(None, "--reserved-public-ip-id"),
            count: int = typer.Option(1, "--count", help="Number of VMs (named NAME-1..NAME-N)"),
            instance_name: Optional[List[str]] = typer.Option(None, "--instance-name", help="Name for VM N, in order (repeatable)"),
            tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag (repeatable)"),
            wait: bool = wait_option("the VM is provisioned"),
            timeout: float = timeout_option(),
            poll_interval: float = poll_interval_option(),
            idempotency_key: Optional[str] = idempotency_key_option(),
        ) -> None:
            """Create a cloud VM (async; returns an operation).

            CPU, RAM, disk and OS default to the plan's and the image's, and the plan's billing SKU is sent for --billing-term.
            """
            options = create_options(
                site_id=site_id, plan_id=plan_id, template_id=template_id, os_type=os_type, os_distro=os_distro,
                cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb, billing_term=billing_term, windows_license=windows_license,
                windows_license_file=windows_license_file, billing_catalog=billing_catalog,
                billing_catalog_file=billing_catalog_file, ssh_key=ssh_key, ssh_key_file=ssh_key_file,
                ssh_key_ids=ssh_key_id or None, vpc_id=vpc_id, subnet_id=subnet_id,
                network_connectivity=network_connectivity, vpc_attachment_mode=vpc_attachment_mode,
                reserved_public_ip_id=reserved_public_ip_id, tags=tag or None,
            )
            create_command(ctx, name, options, count, instance_name, wait, timeout, poll_interval, idempotency_key)

    else:

        @app.command("create")
        @handle_api_errors
        def create_gpu(
            ctx: typer.Context,
            name: str = typer.Argument(..., help="VM name (with --count, the base name)"),
            site_id: str = typer.Option(..., "--site-id", help="Site (required). See `ibee compute sites`."),
            plan_id: str = typer.Option(..., "--plan-id", help="Plan. See `ibee compute plans --vm-type gpu`."),
            template_id: str = typer.Option(..., "--template-id", help="Image. See `ibee compute images --vm-type gpu`."),
            os_type: Optional[str] = typer.Option(None, "--os-type", help="Default: the image's"),
            os_distro: Optional[str] = typer.Option(None, "--os-distro", help="Default: the image's"),
            cpu: Optional[int] = typer.Option(None, "--cpu", help="Default: the plan's"),
            ram_mb: Optional[int] = typer.Option(None, "--ram-mb", help="Default: the plan's"),
            disk_gb: Optional[int] = typer.Option(None, "--disk-gb", help="Default: the plan's"),
            gpu_count: Optional[int] = typer.Option(None, "--gpu-count", help="Default: the plan's"),
            gpu_model: Optional[str] = typer.Option(None, "--gpu-model", help="Default: the plan's"),
            billing_term: Optional[str] = typer.Option(None, "--billing-term", help="HOURLY or MONTHLY (default: the plan's catalog)"),
            billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="Billing SKU JSON to send instead of the plan's"),
            billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
            ssh_key: Optional[List[str]] = typer.Option(None, "--ssh-key", help="Public SSH key (repeatable)"),
            ssh_key_file: Optional[List[str]] = typer.Option(None, "--ssh-key-file", help="File with one public SSH key (repeatable)"),
            ssh_key_id: Optional[List[str]] = typer.Option(None, "--ssh-key-id", help="Saved SSH key ID (repeatable)"),
            vpc_id: Optional[str] = typer.Option(None, "--vpc-id", help="VPC (with --subnet-id)"),
            subnet_id: Optional[str] = typer.Option(None, "--subnet-id"),
            network_connectivity: Optional[str] = typer.Option(None, "--network-connectivity", help="private, nat_gateway or public_ip"),
            vpc_attachment_mode: Optional[str] = typer.Option(None, "--vpc-attachment-mode", help="primary or secondary"),
            reserved_public_ip_id: Optional[str] = typer.Option(None, "--reserved-public-ip-id"),
            count: int = typer.Option(1, "--count", help="Number of VMs (named NAME-1..NAME-N)"),
            instance_name: Optional[List[str]] = typer.Option(None, "--instance-name", help="Name for VM N, in order (repeatable)"),
            tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag (repeatable)"),
            wait: bool = wait_option("the VM is provisioned"),
            timeout: float = timeout_option(),
            poll_interval: float = poll_interval_option(),
            idempotency_key: Optional[str] = idempotency_key_option(),
        ) -> None:
            """Create a GPU VM (async; returns an operation).

            CPU, RAM, disk, GPU and OS default to the plan's and the image's, and the plan's billing SKU is sent.
            """
            options = create_options(
                site_id=site_id, plan_id=plan_id, template_id=template_id, os_type=os_type, os_distro=os_distro,
                cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb, gpu_count=gpu_count, gpu_model=gpu_model,
                billing_term=billing_term, windows_license=None, windows_license_file=None,
                billing_catalog=billing_catalog, billing_catalog_file=billing_catalog_file, ssh_key=ssh_key,
                ssh_key_file=ssh_key_file, ssh_key_ids=ssh_key_id or None, vpc_id=vpc_id, subnet_id=subnet_id,
                network_connectivity=network_connectivity, vpc_attachment_mode=vpc_attachment_mode,
                reserved_public_ip_id=reserved_public_ip_id, tags=tag or None,
            )
            create_command(ctx, name, options, count, instance_name, wait, timeout, poll_interval, idempotency_key)

    @app.command("delete")
    @handle_api_errors
    def delete_vm(
        ctx: typer.Context,
        vm_id: str = typer.Argument(..., help="VM ID"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
        release_public_ip: bool = typer.Option(False, "--release-public-ip", help="Release an auto-assigned public IP (the default)"),
        reserve_public_ip: bool = typer.Option(False, "--reserve-public-ip", help="Keep an auto-assigned public IP as a Reserved IP"),
        reserved_ip_label: Optional[str] = typer.Option(None, "--reserved-ip-label", help="Label for the Reserved IP (default: the VM name)"),
        reserved_ip_billing_catalog: Optional[str] = typer.Option(None, "--reserved-ip-billing-catalog", help="Reserved IP SKU JSON"),
        reserved_ip_billing_catalog_file: Optional[str] = typer.Option(None, "--reserved-ip-billing-catalog-file"),
        wait: bool = wait_option("the VM is deleted"),
        timeout: float = timeout_option(),
        poll_interval: float = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Delete a VM (async). An auto-assigned public IP is released unless --reserve-public-ip."""
        if reserve_public_ip and release_public_ip:
            raise typer.BadParameter("Use only one of --reserve-public-ip or --release-public-ip.")
        catalog = load_json_input(reserved_ip_billing_catalog, reserved_ip_billing_catalog_file, "reserved-ip-billing-catalog")
        confirm_destructive(get_settings(ctx), f"Delete {label} '{vm_id}'?", yes)
        settings, client, method = _sdk(ctx, spec, "delete_{}")
        result = method(
            vm_id,
            **compact_payload(
                idempotency_key=idempotency_key,
                public_ip_action="reserve" if reserve_public_ip else "release" if release_public_ip else None,
                reserved_ip_label=reserved_ip_label,
                reserved_ip_billing_catalog=catalog,
            ),
        )
        finish_operation(settings, client, result, "Delete", vm_id, wait, timeout, poll_interval)

    def power_command(verb: str) -> None:
        @app.command(verb)
        @handle_api_errors
        def power(
            ctx: typer.Context,
            vm_id: str = typer.Argument(..., help="VM ID"),
            force: Optional[bool] = typer.Option(None, "--force/--no-force"),
            wait: bool = wait_option(),
            timeout: float = timeout_option(),
            poll_interval: float = poll_interval_option(),
            idempotency_key: Optional[str] = idempotency_key_option(),
        ) -> None:
            settings, client, method = _sdk(ctx, spec, verb + "_{}")
            result = method(vm_id, **compact_payload(force=force, idempotency_key=idempotency_key))
            finish_operation(settings, client, result, verb.capitalize(), vm_id, wait, timeout, poll_interval)

        power.__doc__ = f"{verb.capitalize()} a {label} (async)."

    for verb in ("start", "stop", "reboot"):
        power_command(verb)

    # -- access, resize, volumes, metrics ------------------------------------

    @app.command("access-update")
    @handle_api_errors
    def access_update(
        ctx: typer.Context,
        vm_id: str = typer.Argument(..., help="VM ID"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        admin_username: Optional[str] = typer.Option(None, "--admin-username"),
        ssh_key_mode: Optional[str] = typer.Option(None, "--ssh-key-mode", help="add or remove"),
        ssh_key: Optional[List[str]] = typer.Option(None, "--ssh-key", help="Public SSH key (repeatable)"),
        ssh_key_file: Optional[List[str]] = typer.Option(None, "--ssh-key-file", help="File with one public SSH key (repeatable)"),
        ssh_key_id: Optional[List[str]] = typer.Option(None, "--ssh-key-id", help="SSH key ID (repeatable)"),
        ssh_key_secret_ref: Optional[List[str]] = typer.Option(None, "--ssh-key-secret-ref", help="JSON object (repeatable)"),
        password_stdin: bool = typer.Option(False, "--password-stdin", help="Read the new password from standard input"),
        prompt_password: bool = typer.Option(False, "--prompt-password", help="Prompt for the new password"),
        password_auth_enabled: Optional[bool] = typer.Option(None, "--enable-password-auth/--disable-password-auth"),
        confirm_remove_last_ssh_key: Optional[bool] = typer.Option(None, "--confirm-remove-last-ssh-key"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the SSH-key removal confirmation"),
        wait: bool = wait_option(),
        timeout: float = timeout_option(),
        poll_interval: float = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Change SSH keys, the admin password or password authentication (async)."""
        new_password = _read_new_password(password_stdin, prompt_password)
        keys = [*(ssh_key or []), *read_text_files(ssh_key_file, "--ssh-key-file")]
        refs = [parse_json_object(raw, "--ssh-key-secret-ref") for raw in ssh_key_secret_ref or []]
        if ssh_key_mode == "remove":
            confirm_destructive(get_settings(ctx), f"Remove SSH access from {label} '{vm_id}'?", yes)
        settings, client, method = _sdk(ctx, spec, "update_{}_access")
        result = method(
            vm_id,
            **compact_payload(
                idempotency_key=idempotency_key, requested_by=requested_by, admin_username=admin_username,
                ssh_key_mode=ssh_key_mode, ssh_keys=keys or None, ssh_key_ids=ssh_key_id or None,
                ssh_key_secret_refs=refs or None, new_password=new_password,
                password_auth_enabled=password_auth_enabled, confirm_remove_last_ssh_key=confirm_remove_last_ssh_key,
            ),
        )
        finish_operation(settings, client, result, "Access update", vm_id, wait, timeout, poll_interval)

    @app.command("resize-precheck")
    @handle_api_errors
    def resize_precheck(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        cpu: Optional[int] = typer.Option(None, "--cpu"),
        ram_mb: Optional[int] = typer.Option(None, "--ram-mb"),
        disk_gb: Optional[int] = typer.Option(None, "--disk-gb"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Ask the server whether a resize can run in place."""
        _settings, _client, method = _sdk(ctx, spec, "precheck_{}_resize")
        print_json(method(vm_id, **compact_payload(cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb, requested_by=requested_by)))

    def billing_options(**values: Any) -> dict:
        values["billing_catalog"] = load_json_input(values.pop("billing_catalog"), values.pop("billing_catalog_file"), "billing-catalog")
        values["windows_license"] = load_json_input(values.pop("windows_license"), values.pop("windows_license_file"), "windows-license")
        return compact_payload(**values)

    @app.command("resize")
    @handle_api_errors
    def resize(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        plan_id: Optional[str] = typer.Option(None, "--plan-id", help="Take CPU, RAM and disk from this plan and bill its SKU"),
        cpu: Optional[int] = typer.Option(None, "--cpu"),
        ram_mb: Optional[int] = typer.Option(None, "--ram-mb"),
        disk_gb: Optional[int] = typer.Option(None, "--disk-gb"),
        billing_term: Optional[str] = typer.Option(None, "--billing-term", help="With --plan-id: HOURLY (default) or MONTHLY"),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="Target billing SKU as JSON"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        windows_license: Optional[str] = typer.Option(None, "--windows-license", help="Windows licence SKU JSON (default: the VM's)"),
        windows_license_file: Optional[str] = typer.Option(None, "--windows-license-file"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        wait: bool = wait_option("the resize completes"),
        timeout: float = timeout_option(),
        poll_interval: float = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Resize CPU and memory, and optionally grow the root disk (async)."""
        options = billing_options(
            plan_id=plan_id, cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb, billing_term=billing_term,
            billing_catalog=billing_catalog, billing_catalog_file=billing_catalog_file, windows_license=windows_license,
            windows_license_file=windows_license_file, requested_by=requested_by, idempotency_key=idempotency_key,
        )
        settings, client, method = _sdk(ctx, spec, "resize_{}")
        finish_operation(settings, client, method(vm_id, **options), "Resize", vm_id, wait, timeout, poll_interval)

    @app.command("resize-plan")
    @handle_api_errors
    def resize_plan(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        plan_id: Optional[str] = typer.Option(None, "--plan-id", help="Take CPU and RAM from this plan and bill its SKU"),
        cpu: Optional[int] = typer.Option(None, "--cpu"),
        ram_mb: Optional[int] = typer.Option(None, "--ram-mb"),
        allow_online: Optional[bool] = typer.Option(None, "--allow-online/--no-allow-online"),
        confirm_downgrade: bool = typer.Option(False, "--confirm-downgrade", help="Permit a smaller shape"),
        billing_term: Optional[str] = typer.Option(None, "--billing-term", help="With --plan-id: HOURLY (default) or MONTHLY"),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="Target billing SKU as JSON"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        windows_license: Optional[str] = typer.Option(None, "--windows-license", help="Windows licence SKU JSON"),
        windows_license_file: Optional[str] = typer.Option(None, "--windows-license-file"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        wait: bool = wait_option("the resize completes"),
        timeout: float = timeout_option(),
        poll_interval: float = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Change the CPU and memory shape (async)."""
        options = billing_options(
            plan_id=plan_id, cpu=cpu, ram_mb=ram_mb, allow_online=allow_online,
            confirm_downgrade=True if confirm_downgrade else None, billing_term=billing_term,
            billing_catalog=billing_catalog, billing_catalog_file=billing_catalog_file, windows_license=windows_license,
            windows_license_file=windows_license_file, requested_by=requested_by, idempotency_key=idempotency_key,
        )
        settings, client, method = _sdk(ctx, spec, "resize_{}_plan")
        finish_operation(settings, client, method(vm_id, **options), "Plan resize", vm_id, wait, timeout, poll_interval)

    @app.command("resize-root-disk")
    @handle_api_errors
    def resize_root_disk(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        new_size_gb: int = typer.Option(..., "--new-size-gb", help="New size in GB"),
        allow_online: Optional[bool] = typer.Option(None, "--allow-online/--no-allow-online"),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="Billing SKU as JSON (optional)"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        wait: bool = wait_option(),
        timeout: float = timeout_option(),
        poll_interval: float = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Grow the root disk (async)."""
        catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
        settings, client, method = _sdk(ctx, spec, "resize_{}_root_disk")
        result = method(
            vm_id,
            new_size_gb=new_size_gb,
            **compact_payload(allow_online=allow_online, billing_catalog=catalog, requested_by=requested_by, idempotency_key=idempotency_key),
        )
        finish_operation(settings, client, result, "Root-disk resize", vm_id, wait, timeout, poll_interval)

    @app.command("volume-attach")
    @handle_api_errors
    def volume_attach(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        volume_id: str = typer.Argument(...),
        mode: Optional[str] = typer.Option(None, "--mode", help="single-writer or multi-writer"),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="Block Storage SKU JSON (default: read from the volume)"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        wait: bool = wait_option(),
        timeout: float = timeout_option(),
        poll_interval: float = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Attach a block volume (async)."""
        catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
        settings, client, method = _sdk(ctx, spec, "attach_{}_volume")
        result = method(
            vm_id,
            volume_id=volume_id,
            **compact_payload(mode=mode, billing_catalog=catalog, requested_by=requested_by, idempotency_key=idempotency_key),
        )
        finish_operation(settings, client, result, "Volume attach", volume_id, wait, timeout, poll_interval)

    @app.command("volume-detach")
    @handle_api_errors
    def volume_detach(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        volume_id: str = typer.Argument(...),
        force: Optional[bool] = typer.Option(None, "--force/--no-force", help="Detach even if still mounted"),
        confirm_unmounted: Optional[bool] = typer.Option(None, "--confirm-unmounted", help="Confirm the volume is unmounted in the guest"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
        wait: bool = wait_option(),
        timeout: float = timeout_option(),
        poll_interval: float = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Detach a block volume (async)."""
        confirm_destructive(get_settings(ctx), f"Detach volume '{volume_id}' from {label} '{vm_id}'?", yes)
        settings, client, method = _sdk(ctx, spec, "detach_{}_volume")
        result = method(
            vm_id,
            volume_id=volume_id,
            **compact_payload(force=force, confirm_unmounted=confirm_unmounted, requested_by=requested_by, idempotency_key=idempotency_key),
        )
        finish_operation(settings, client, result, "Volume detach", volume_id, wait, timeout, poll_interval)

    @app.command("mount-guidance-acknowledge")
    @handle_api_errors
    def mount_guidance_acknowledge(ctx: typer.Context, vm_id: str = typer.Argument(...), volume_id: str = typer.Argument(...)) -> None:
        """Record that the guest mount instructions were reviewed."""
        _settings, _client, method = _sdk(ctx, spec, "acknowledge_{}_mount_guidance")
        print_json(method(vm_id, volume_id=volume_id))

    @app.command("events")
    @handle_api_errors
    def events(ctx: typer.Context, vm_id: str = typer.Argument(...), limit: Optional[int] = typer.Option(None, "--limit")) -> None:
        """Show the VM event timeline."""
        _settings, _client, method = _sdk(ctx, spec, "list_{}_events")
        print_json(method(vm_id, **compact_payload(limit=limit)))

    @app.command("metrics-timeseries")
    @handle_api_errors
    def metrics_timeseries(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        metric_range: Optional[str] = typer.Option(None, "--range", help="For example 30m, 1h, 6h, 24h or 7d"),
    ) -> None:
        """Show metric series for a time range."""
        _settings, _client, method = _sdk(ctx, spec, "get_{}_metrics_timeseries")
        print_json(method(vm_id, **compact_payload(range=metric_range)))

    @app.command("bandwidth")
    @handle_api_errors
    def bandwidth(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        month: Optional[str] = typer.Option(None, "--month", help="YYYY-MM (default: the current UTC month)"),
    ) -> None:
        """Show received and transmitted bytes for one month."""
        _settings, _client, method = _sdk(ctx, spec, "get_{}_bandwidth")
        print_json(method(vm_id, month=month or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")))

    # -- snapshots -----------------------------------------------------------

    @snapshots.command("list")
    @handle_api_errors
    def snapshots_list(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        limit: Optional[int] = typer.Option(None, "--limit"),
        offset: Optional[int] = typer.Option(None, "--offset"),
        search: Optional[str] = typer.Option(None, "--search"),
    ) -> None:
        """List recovery snapshots of one VM."""
        _settings, _client, method = _sdk(ctx, spec, "list_{}_snapshots")
        print_json(method(vm_id, **compact_payload(limit=limit, offset=offset, search=search)))

    @snapshots.command("create")
    @handle_api_errors
    def snapshots_create(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        name: str = typer.Argument(..., help="Snapshot name"),
        description: Optional[str] = typer.Option(None, "--description"),
        mode: Optional[str] = typer.Option(None, "--mode", help="root_only, all_attached or selective"),
        selected_data_volume_id: Optional[List[str]] = typer.Option(None, "--selected-data-volume-id", help="Repeatable"),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="The snapshot storage SKU as JSON"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Create a recovery snapshot set."""
        catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
        _settings, _client, method = _sdk(ctx, spec, "create_{}_snapshot")
        print_json(
            method(
                vm_id,
                name=name,
                billing_catalog=catalog,
                **compact_payload(
                    description=description, mode=mode, selected_data_volume_ids=selected_data_volume_id or None,
                    requested_by=requested_by,
                ),
            )
        )

    @snapshots.command("get")
    @handle_api_errors
    def snapshots_get(ctx: typer.Context, snapshot_set_id: str = typer.Argument(...)) -> None:
        """Get one snapshot set."""
        _settings, _client, method = _sdk(ctx, spec, "get_{}_snapshot")
        print_json(method(snapshot_set_id))

    @snapshots.command("delete")
    @handle_api_errors
    def snapshots_delete(
        ctx: typer.Context,
        snapshot_set_id: str = typer.Argument(...),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ) -> None:
        """Delete a snapshot set and its recovery points."""
        confirm_destructive(get_settings(ctx), f"Delete snapshot set '{snapshot_set_id}'?", yes)
        _settings, _client, method = _sdk(ctx, spec, "delete_{}_snapshot")
        print_json(method(snapshot_set_id))

    @snapshots.command("restore")
    @handle_api_errors
    def snapshots_restore(
        ctx: typer.Context,
        vm_id: str = typer.Argument(..., help="The VM the snapshot was taken from"),
        snapshot_set_id: str = typer.Argument(...),
        target_mode: Optional[str] = typer.Option(None, "--target-mode", help="replace, new_vm or volume_only"),
        target_vm_name: Optional[str] = typer.Option(None, "--target-vm-name"),
        target_cpu: Optional[int] = typer.Option(None, "--target-cpu"),
        target_ram_mb: Optional[int] = typer.Option(None, "--target-ram-mb"),
        target_disk_gb: Optional[int] = typer.Option(None, "--target-disk-gb"),
        target_plan_id: Optional[str] = typer.Option(None, "--target-plan-id"),
        target_plan_name: Optional[str] = typer.Option(None, "--target-plan-name"),
        target_plan_code: Optional[str] = typer.Option(None, "--target-plan-code"),
        target_plan_type: Optional[str] = typer.Option(None, "--target-plan-type"),
        target_performance_category: Optional[str] = typer.Option(None, "--target-performance-category"),
        target_plan_monthly_rate: Optional[float] = typer.Option(None, "--target-plan-monthly-rate"),
        target_plan_hourly_rate: Optional[float] = typer.Option(None, "--target-plan-hourly-rate"),
        target_bandwidth_tb: Optional[float] = typer.Option(None, "--target-bandwidth-tb"),
        target_bandwidth_display: Optional[str] = typer.Option(None, "--target-bandwidth-display"),
        target_network_bandwidth: Optional[str] = typer.Option(None, "--target-network-bandwidth"),
        target_compute_node_id: Optional[str] = typer.Option(None, "--target-compute-node-id"),
        target_gpu_type: Optional[str] = typer.Option(None, "--target-gpu-type"),
        target_gpu_model: Optional[str] = typer.Option(None, "--target-gpu-model"),
        target_gpu_count: Optional[int] = typer.Option(None, "--target-gpu-count"),
        target_gpu_memory_gb: Optional[float] = typer.Option(None, "--target-gpu-memory-gb"),
        target_gpu_memory_display: Optional[str] = typer.Option(None, "--target-gpu-memory-display"),
        target_site_id: Optional[str] = typer.Option(None, "--target-site-id"),
        target_site_name: Optional[str] = typer.Option(None, "--target-site-name"),
        selected_volume_id: Optional[str] = typer.Option(None, "--selected-volume-id"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        auto_start: Optional[bool] = typer.Option(None, "--auto-start/--no-auto-start"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Confirm the restore"),
    ) -> None:
        """Restore a snapshot (replace the VM, create a VM, or restore a volume)."""
        options = _restore_options(locals())
        confirm_destructive(get_settings(ctx), f"Restore snapshot '{snapshot_set_id}' for {label} '{vm_id}'?", yes)
        _settings, _client, method = _sdk(ctx, spec, "restore_{}_snapshot")
        print_json(method(snapshot_set_id, vm_id=vm_id, **options))

    @snapshots.command("restore-status")
    @handle_api_errors
    def snapshots_restore_status(ctx: typer.Context, restore_id: str = typer.Argument(...)) -> None:
        """Get a snapshot restore."""
        _settings, _client, method = _sdk(ctx, spec, "get_{}_snapshot_restore")
        print_json(method(restore_id))

    # -- backup policy -------------------------------------------------------

    @backup_policy.command("get")
    @handle_api_errors
    def policy_get(ctx: typer.Context, vm_id: str = typer.Argument(...)) -> None:
        """Get the automated backup policy."""
        _settings, _client, method = _sdk(ctx, spec, "get_{}_backup_policy")
        print_json(method(vm_id))

    @backup_policy.command("update")
    @handle_api_errors
    def policy_update(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        frequency: Optional[str] = typer.Option(None, "--frequency", help="For example daily or weekly"),
        timezone: Optional[str] = typer.Option(None, "--timezone", help="IANA time zone, e.g. Asia/Kolkata"),
        hour: Optional[int] = typer.Option(None, "--hour"),
        minute: Optional[int] = typer.Option(None, "--minute"),
        day_of_week: Optional[int] = typer.Option(None, "--day-of-week", help="0 (Monday) - 6 (Sunday)"),
        window_minutes: Optional[int] = typer.Option(None, "--window-minutes"),
        retention_days: Optional[int] = typer.Option(None, "--retention-days"),
        full_backup_interval_days: Optional[int] = typer.Option(None, "--full-backup-interval-days"),
        incremental_enabled: Optional[bool] = typer.Option(None, "--incremental/--no-incremental"),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="Replacement backup SKU JSON (optional)"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Update the backup schedule and retention."""
        _settings, _client, method = _sdk(ctx, spec, "update_{}_backup_policy")
        result = method(
            vm_id,
            **compact_payload(
                schedule=_schedule(frequency=frequency, timezone=timezone, hour=hour, minute=minute, day_of_week=day_of_week, window_minutes=window_minutes),
                retention_days=retention_days, full_backup_interval_days=full_backup_interval_days,
                incremental_enabled=incremental_enabled, requested_by=requested_by,
                billing_catalog=load_json_input(billing_catalog, billing_catalog_file, "billing-catalog"),
            ),
        )
        print_json(result)

    @backup_policy.command("enable")
    @handle_api_errors
    def policy_enable(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="The backup storage SKU as JSON"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        frequency: Optional[str] = typer.Option(None, "--frequency", help="For example daily or weekly"),
        timezone: Optional[str] = typer.Option(None, "--timezone", help="IANA time zone, e.g. Asia/Kolkata"),
        hour: Optional[int] = typer.Option(None, "--hour"),
        minute: Optional[int] = typer.Option(None, "--minute"),
        day_of_week: Optional[int] = typer.Option(None, "--day-of-week", help="0 (Monday) - 6 (Sunday)"),
        window_minutes: Optional[int] = typer.Option(None, "--window-minutes"),
        retention_days: Optional[int] = typer.Option(None, "--retention-days"),
        full_backup_interval_days: Optional[int] = typer.Option(None, "--full-backup-interval-days"),
        incremental_enabled: Optional[bool] = typer.Option(None, "--incremental/--no-incremental"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Enable automated backups. Values you leave out are the server's defaults."""
        _settings, _client, method = _sdk(ctx, spec, "enable_{}_backups")
        result = method(
            vm_id,
            billing_catalog=load_json_input(billing_catalog, billing_catalog_file, "billing-catalog"),
            **compact_payload(
                schedule=_schedule(frequency=frequency, timezone=timezone, hour=hour, minute=minute, day_of_week=day_of_week, window_minutes=window_minutes),
                retention_days=retention_days, full_backup_interval_days=full_backup_interval_days,
                incremental_enabled=incremental_enabled, requested_by=requested_by,
            ),
        )
        print_json(result)

    @backup_policy.command("disable")
    @handle_api_errors
    def policy_disable(ctx: typer.Context, vm_id: str = typer.Argument(...), requested_by: Optional[str] = typer.Option(None, "--requested-by")) -> None:
        """Disable future backup runs (recovery points are kept)."""
        _settings, _client, method = _sdk(ctx, spec, "disable_{}_backups")
        print_json(method(vm_id, **compact_payload(requested_by=requested_by)))

    @backup_policy.command("reschedule")
    @handle_api_errors
    def policy_reschedule(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        next_run_at: str = typer.Option(..., "--next-run-at", help="ISO 8601 timestamp"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Set the next automated backup time."""
        _settings, _client, method = _sdk(ctx, spec, "reschedule_{}_backup")
        print_json(method(vm_id, next_run_at=_parse_datetime(next_run_at), **compact_payload(requested_by=requested_by)))

    # -- backup runs ---------------------------------------------------------

    @backups.command("list")
    @handle_api_errors
    def backups_list(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        limit: Optional[int] = typer.Option(None, "--limit"),
        offset: Optional[int] = typer.Option(None, "--offset"),
        search: Optional[str] = typer.Option(None, "--search"),
    ) -> None:
        """List backup runs of one VM."""
        _settings, _client, method = _sdk(ctx, spec, "list_{}_backup_runs")
        print_json(method(vm_id, **compact_payload(limit=limit, offset=offset, search=search)))

    @backups.command("create")
    @handle_api_errors
    def backups_create(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="The backup storage SKU as JSON"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        reason: Optional[str] = typer.Option(None, "--reason"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Queue a manual backup run."""
        _settings, _client, method = _sdk(ctx, spec, "create_{}_backup_run")
        result = method(
            vm_id,
            billing_catalog=load_json_input(billing_catalog, billing_catalog_file, "billing-catalog"),
            **compact_payload(reason=reason, requested_by=requested_by),
        )
        print_json(result)

    @backups.command("get")
    @handle_api_errors
    def backups_get(ctx: typer.Context, run_id: str = typer.Argument(...)) -> None:
        """Get one backup run or recovery point."""
        _settings, _client, method = _sdk(ctx, spec, "get_{}_backup_run")
        print_json(method(run_id))

    @backups.command("restore")
    @handle_api_errors
    def backups_restore(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        recovery_point_id: str = typer.Argument(..., help="Recovery point ID"),
        target_mode: Optional[str] = typer.Option(None, "--target-mode", help="replace, new_vm or volume_only"),
        target_vm_name: Optional[str] = typer.Option(None, "--target-vm-name"),
        target_cpu: Optional[int] = typer.Option(None, "--target-cpu"),
        target_ram_mb: Optional[int] = typer.Option(None, "--target-ram-mb"),
        target_disk_gb: Optional[int] = typer.Option(None, "--target-disk-gb"),
        target_plan_id: Optional[str] = typer.Option(None, "--target-plan-id"),
        target_plan_name: Optional[str] = typer.Option(None, "--target-plan-name"),
        target_plan_code: Optional[str] = typer.Option(None, "--target-plan-code"),
        target_plan_type: Optional[str] = typer.Option(None, "--target-plan-type"),
        target_performance_category: Optional[str] = typer.Option(None, "--target-performance-category"),
        target_plan_monthly_rate: Optional[float] = typer.Option(None, "--target-plan-monthly-rate"),
        target_plan_hourly_rate: Optional[float] = typer.Option(None, "--target-plan-hourly-rate"),
        target_bandwidth_tb: Optional[float] = typer.Option(None, "--target-bandwidth-tb"),
        target_bandwidth_display: Optional[str] = typer.Option(None, "--target-bandwidth-display"),
        target_network_bandwidth: Optional[str] = typer.Option(None, "--target-network-bandwidth"),
        target_compute_node_id: Optional[str] = typer.Option(None, "--target-compute-node-id"),
        target_gpu_type: Optional[str] = typer.Option(None, "--target-gpu-type"),
        target_gpu_model: Optional[str] = typer.Option(None, "--target-gpu-model"),
        target_gpu_count: Optional[int] = typer.Option(None, "--target-gpu-count"),
        target_gpu_memory_gb: Optional[float] = typer.Option(None, "--target-gpu-memory-gb"),
        target_gpu_memory_display: Optional[str] = typer.Option(None, "--target-gpu-memory-display"),
        target_site_id: Optional[str] = typer.Option(None, "--target-site-id"),
        target_site_name: Optional[str] = typer.Option(None, "--target-site-name"),
        selected_volume_id: Optional[str] = typer.Option(None, "--selected-volume-id"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        auto_start: Optional[bool] = typer.Option(None, "--auto-start/--no-auto-start"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Confirm the restore"),
    ) -> None:
        """Restore a backup (replace the VM, create a VM, or restore a volume)."""
        options = _restore_options(locals())
        confirm_destructive(get_settings(ctx), f"Restore recovery point '{recovery_point_id}' for {label} '{vm_id}'?", yes)
        _settings, _client, method = _sdk(ctx, spec, "restore_{}_backup")
        print_json(method(vm_id, recovery_point_id=recovery_point_id, **options))

    @backups.command("restore-status")
    @handle_api_errors
    def backups_restore_status(ctx: typer.Context, restore_id: str = typer.Argument(...)) -> None:
        """Get a backup restore."""
        _settings, _client, method = _sdk(ctx, spec, "get_{}_backup_restore")
        print_json(method(restore_id))


__all__ = ["CLOUD_VM", "GPU_VM", "VmCommandSpec", "field", "register_vm_commands"]
