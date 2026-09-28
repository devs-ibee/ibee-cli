"""Shared cloud and GPU VM lifecycle commands.

``vms`` and ``gpus`` expose the same portal flows. The Python SDK applies the
portal's rules (plan and image resolution, billing SKU for the term, public-IP
choice on delete, state checks, recovery validation) before it sends anything; the
commands here add the interactive parts (confirmations, the public-IP question),
batch creates, and waiting.
"""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from typing import Any, List, Optional

import typer
from ibee.validation import (
    ATTACH_MODES,
    BACKUP_FREQUENCIES,
    BACKUP_RUN_STATUSES,
    BILLING_TERMS,
    METRICS_RANGES,
    NETWORK_CONNECTIVITY,
    RESTORE_TARGET_MODES,
    SNAPSHOT_MODES,
    IbeeValidationError,
    assert_vm_action_allowed,
    expand_batch_names,
    has_auto_assigned_public_ip,
    normalize_billing_term,
    normalize_ssh_public_keys,
    resolve_delete_public_ip_action,
    validate_attach_mode,
    validate_backup_statuses,
    validate_firewall_group_ids,
    validate_idempotency_key,
    validate_network_request,
    validate_restore_mode,
    validate_restore_mode_combination,
    validate_vm_id,
)

from ..context import get_client, get_settings, require_workspace
from ..helpers import (
    check_state_option,
    compact_payload,
    confirm_destructive,
    finish_operation,
    finish_operations,
    idempotency_key_option,
    load_json_input,
    new_idempotency_key,
    parse_json_object,
    parse_key_value_pairs,
    poll_interval_option,
    read_text_files,
    resolve_idempotency_key,
    resolve_wait,
    timeout_option,
    wait_for_recovery,
)
from ..render import cell, handle_api_errors, print_json, to_data

#: Recovery waits default to 30 minutes (the SDK default); VM operations to 20.
RECOVERY_WAIT_TIMEOUT_SECONDS = 1800.0

SNAPSHOT_SKU_HELP = (
    "The snapshot_storage billing SKU as JSON (sku_id and sku_code, code SNAPSHOT-STD). "
    "The public API cannot list it yet; copy billing_catalog from an existing snapshot set."
)
BACKUP_SKU_HELP = (
    "The backup_storage billing SKU as JSON (sku_id and sku_code, code BACKUP-STD). "
    "The public API cannot list it yet; copy billing_catalog from an existing backup run."
)
_PRECHECK_MESSAGES = {
    "migration_required": "This downgrade requires migration. In-place disk shrink is blocked.",
    "blocked": "Resize is currently blocked.",
}


@dataclass(frozen=True)
class VmCommandSpec:
    """Names that differ between the cloud and GPU generated SDK resources."""

    kind: str
    resource: str
    method_fragment: str
    label: str

    @property
    def group(self) -> str:
        """The CLI command group (``vms`` or ``gpus``)."""
        return "vms" if self.kind == "cloud" else "gpus"

    @property
    def key_scope(self) -> str:
        """Prefix of generated idempotency keys for create and delete."""
        return "vm" if self.kind == "cloud" else "gpu"


CLOUD_VM = VmCommandSpec("cloud", "cloud_vms", "cloud_vm", "cloud VM")
GPU_VM = VmCommandSpec("gpu", "gpu_vms", "gpu_vm", "GPU VM")


def _resource(ctx: typer.Context, spec: VmCommandSpec):
    settings = get_settings(ctx)
    client = get_client(settings)
    return settings, require_workspace(settings), client, getattr(client, spec.resource)


def _method(resource, spec: VmCommandSpec, verb: str):
    return getattr(resource, f"{verb}_{spec.method_fragment}")


def _require_change(**values: object) -> None:
    if not any(value is not None and value != [] for value in values.values()):
        raise typer.BadParameter("At least one change option is required.")


def _optional_bool(value: Optional[bool]) -> Optional[bool]:
    """Keep the distinction between an omitted flag and an explicit false flag."""

    return value


def _choice(value: Optional[str], allowed: tuple, option: str) -> Optional[str]:
    if value is not None and value not in allowed:
        raise typer.BadParameter(f"{option} must be {', '.join(allowed[:-1])} or {allowed[-1]}.")
    return value


def _read_new_password(password_stdin: bool, prompt_password: bool) -> Optional[str]:
    if password_stdin and prompt_password:
        raise typer.BadParameter("Use only one of --password-stdin or --prompt-password.")
    if prompt_password:
        password = typer.prompt(
            "New password",
            hide_input=True,
            confirmation_prompt=True,
        )
    elif password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        return None
    if not password:
        raise typer.BadParameter("The new password cannot be empty.")
    return password


def _secret_refs(raw_refs: Optional[List[str]]) -> Optional[List[dict]]:
    if not raw_refs:
        return None
    refs = [parse_json_object(raw, "--ssh-key-secret-ref") for raw in raw_refs]
    for ref in refs:
        if not ref.get("ssh_key_id") or not ref.get("secret_name"):
            raise typer.BadParameter(
                "--ssh-key-secret-ref requires ssh_key_id and secret_name."
            )
    return refs


def _restore_kwargs(
    *,
    target_mode: Optional[str],
    target_vm_name: Optional[str],
    target_cpu: Optional[int],
    target_ram_mb: Optional[int],
    target_disk_gb: Optional[int],
    target_plan_id: Optional[str],
    target_plan_name: Optional[str],
    target_plan_code: Optional[str],
    target_plan_type: Optional[str],
    target_performance_category: Optional[str],
    target_plan_monthly_rate: Optional[float],
    target_plan_hourly_rate: Optional[float],
    target_bandwidth_tb: Optional[float],
    target_bandwidth_display: Optional[str],
    target_network_bandwidth: Optional[str],
    target_compute_node_id: Optional[str],
    target_gpu_type: Optional[str],
    target_gpu_model: Optional[str],
    target_gpu_count: Optional[int],
    target_gpu_memory_gb: Optional[float],
    target_gpu_memory_display: Optional[str],
    target_site_id: Optional[str],
    target_site_name: Optional[str],
    selected_volume_id: Optional[str],
    requested_by: Optional[str],
    auto_start: Optional[bool],
    **extra: Any,
) -> dict:
    return compact_payload(
        target_mode=target_mode,
        target_vm_name=target_vm_name,
        target_cpu=target_cpu,
        target_ram_mb=target_ram_mb,
        target_disk_gb=target_disk_gb,
        target_plan_id=target_plan_id,
        target_plan_name=target_plan_name,
        target_plan_code=target_plan_code,
        target_plan_type=target_plan_type,
        target_performance_category=target_performance_category,
        target_plan_monthly_rate=target_plan_monthly_rate,
        target_plan_hourly_rate=target_plan_hourly_rate,
        target_bandwidth_tb=target_bandwidth_tb,
        target_bandwidth_display=target_bandwidth_display,
        target_network_bandwidth=target_network_bandwidth,
        target_compute_node_id=target_compute_node_id,
        target_gpu_type=target_gpu_type,
        target_gpu_model=target_gpu_model,
        target_gpu_count=target_gpu_count,
        target_gpu_memory_gb=target_gpu_memory_gb,
        target_gpu_memory_display=target_gpu_memory_display,
        target_site_id=target_site_id,
        target_site_name=target_site_name,
        selected_volume_id=selected_volume_id,
        requested_by=requested_by,
        auto_start=auto_start,
        **extra,
    )


def _check_restore_request(kwargs: dict) -> str:
    """Portal restore-mode rules, checked before the confirmation prompt."""

    mode = validate_restore_mode(kwargs.get("target_mode"))
    fields = {key: value for key, value in kwargs.items() if key not in ("requested_by", "auto_start", "target_mode")}
    validate_restore_mode_combination(mode, fields)
    return mode


def _schedule(
    frequency: Optional[str],
    timezone: Optional[str],
    hour: Optional[int],
    minute: Optional[int],
    day_of_week: Optional[int],
    window_minutes: Optional[int],
) -> Optional[dict]:
    schedule = compact_payload(
        frequency=frequency,
        timezone=timezone,
        hour=hour,
        minute=minute,
        day_of_week=day_of_week,
        window_minutes=window_minutes,
    )
    return schedule or None


def _parse_datetime(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter("Use an ISO 8601 timestamp, for example 2026-08-10T02:00:00Z.") from exc
    if parsed.tzinfo is None:
        raise typer.BadParameter("The timestamp must include a timezone (Z or an offset).")
    return parsed


def _required_catalog(value: Optional[str], path: Optional[str], help_text: str) -> dict:
    catalog = load_json_input(value, path, "billing-catalog")
    if catalog is None:
        raise typer.BadParameter(f"--billing-catalog (or --billing-catalog-file) is required. {help_text}")
    return catalog


def _check_state_arg(check_state: bool) -> dict:
    return {"check_state": bool(check_state)}


# ---------------------------------------------------------------------------
# Create, delete and power actions (vms.py / gpus.py define the commands)
# ---------------------------------------------------------------------------


def _batch_keys(explicit: Optional[str], spec: VmCommandSpec, names: List[str]) -> List[str]:
    """One idempotency key per VM (the portal keys each batch index separately)."""

    if explicit is not None:
        base = validate_idempotency_key(explicit)
        if len(names) == 1:
            return [base]
        return [validate_idempotency_key(f"{base}-{index}") for index in range(1, len(names) + 1)]
    return [new_idempotency_key(f"{spec.key_scope}-create", name) for name in names]


def create_vms(
    ctx: typer.Context,
    spec: VmCommandSpec,
    *,
    name: str,
    count: int,
    instance_names: Optional[List[str]],
    site_id: Optional[str],
    plan_id: str,
    template_id: str,
    os_type: Optional[str],
    os_distro: Optional[str],
    cpu: Optional[int],
    ram_mb: Optional[int],
    disk_gb: Optional[int],
    billing_term: Optional[str],
    billing_catalog: Optional[str],
    billing_catalog_file: Optional[str],
    ssh_keys: Optional[List[str]],
    ssh_key_files: Optional[List[str]],
    ssh_key_ids: Optional[List[str]],
    firewall_group_ids: Optional[List[str]],
    vpc_id: Optional[str],
    subnet_id: Optional[str],
    network_connectivity: Optional[str],
    reserved_public_ip_id: Optional[str],
    tags: Optional[List[str]],
    requested_by: Optional[str],
    preflight: bool,
    wait: bool,
    timeout: Optional[float],
    poll_interval: Optional[float],
    idempotency_key: Optional[str],
    windows_license: Optional[str] = None,
    windows_license_file: Optional[str] = None,
    gpu_count: Optional[int] = None,
    gpu_model: Optional[str] = None,
    client_factory: Any = None,
) -> None:
    """Portal deploy for one VM or a batch of 1-5 (``--count``)."""

    settings = get_settings(ctx)
    wait_config = resolve_wait(wait, timeout, poll_interval)
    if site_id is None or not site_id.strip():
        raise typer.BadParameter(
            "--site-id is required (the API needs it to place the VM). See `ibee compute sites`."
        )
    names = expand_batch_names(name, count, instance_names, reserved_ip=bool(reserved_public_ip_id))
    keys = _batch_keys(idempotency_key, spec, names)
    # Fail fast (exit 2) on the rules the SDK would reject, before any request.
    normalize_billing_term(billing_term)
    inline_keys = [*(ssh_keys or []), *read_text_files(ssh_key_files, "--ssh-key-file")]
    normalize_ssh_public_keys(inline_keys)
    validate_firewall_group_ids(firewall_group_ids)
    validate_network_request(
        vpc_id=vpc_id,
        subnet_id=subnet_id,
        network_connectivity=network_connectivity,
        reserved_public_ip_id=reserved_public_ip_id,
    )
    catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
    license_sku = load_json_input(windows_license, windows_license_file, "windows-license")

    workspace = require_workspace(settings)
    client = (client_factory or get_client)(settings)
    method = _method(getattr(client, spec.resource), spec, "create")
    common = dict(
        workspace_id=workspace,
        site_id=site_id,
        plan_id=plan_id,
        template_id=template_id,
        **compact_payload(
            os_type=os_type,
            os_distro=os_distro,
            cpu=cpu,
            ram_mb=ram_mb,
            disk_gb=disk_gb,
            gpu_count=gpu_count,
            gpu_model=gpu_model,
            billing_term=billing_term,
            billing_catalog=catalog,
            windows_license=license_sku,
            ssh_keys=inline_keys or None,
            ssh_key_ids=ssh_key_ids or None,
            firewall_group_ids=firewall_group_ids or None,
            vpc_id=vpc_id,
            subnet_id=subnet_id,
            network_connectivity=network_connectivity,
            reserved_public_ip_id=reserved_public_ip_id,
            tags=tags or None,
            requested_by=requested_by,
        ),
    )
    if preflight or getattr(settings, "check_billing", False):
        common["preflight_billing"] = True
    results = []
    for vm_name, key in zip(names, keys):
        try:
            results.append(method(name=vm_name, idempotency_key=key, **common))
        except Exception:
            if len(names) > 1:
                typer.secho(
                    f"Created {len(results)} of {len(names)} {spec.label}s; stopped at {vm_name}.",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
            raise
    finish_operations(
        settings, client, workspace, results, "Create", names, wait_config, idempotency_keys=keys
    )


def delete_vm(
    ctx: typer.Context,
    spec: VmCommandSpec,
    *,
    vm_id: str,
    yes: bool,
    reserve_public_ip: bool,
    release_public_ip: bool,
    reserved_ip_label: Optional[str],
    reserved_ip_billing_catalog: Optional[str],
    reserved_ip_billing_catalog_file: Optional[str],
    requested_by: Optional[str],
    preflight: bool,
    check_state: bool,
    wait: bool,
    timeout: Optional[float],
    poll_interval: Optional[float],
    idempotency_key: Optional[str],
    client_factory: Any = None,
) -> None:
    """Portal delete dialog: confirm, choose what happens to the public IP, delete."""

    settings = get_settings(ctx)
    wait_config = resolve_wait(wait, timeout, poll_interval)
    vm_id = validate_vm_id(vm_id)
    key = resolve_idempotency_key(idempotency_key, f"{spec.key_scope}-delete", vm_id)
    if reserve_public_ip and release_public_ip:
        raise typer.BadParameter("Use only one of --reserve-public-ip or --release-public-ip.")
    action = "reserve" if reserve_public_ip else "release" if release_public_ip else None
    catalog = load_json_input(
        reserved_ip_billing_catalog, reserved_ip_billing_catalog_file, "reserved-ip-billing-catalog"
    )
    if release_public_ip and (catalog is not None or reserved_ip_label is not None):
        raise typer.BadParameter(
            "--reserved-ip-label and --reserved-ip-billing-catalog are only used with --reserve-public-ip."
        )
    assume_yes = yes or getattr(settings, "assume_yes", False)
    workspace = require_workspace(settings)
    client = (client_factory or get_client)(settings)
    resource = getattr(client, spec.resource)

    vm = None
    target = f"{spec.label} '{vm_id}'"
    if check_state:
        vm = to_data(_method(resource, spec, "get")(vm_id=vm_id, workspace_id=workspace))
        vm = vm if isinstance(vm, dict) else {}
        assert_vm_action_allowed(vm, "delete")
        if vm.get("name"):
            target = f"{spec.label} '{vm['name']}' ({vm_id})"
        volumes = [
            str(item.get("volume_id") or item.get("name") or item) if isinstance(item, dict) else str(item)
            for item in vm.get("data_volumes") or []
        ]
        if volumes:
            typer.secho(
                "Attached data volumes are detached automatically and kept: " + ", ".join(volumes),
                fg=typer.colors.YELLOW,
                err=True,
            )

    def resolve(choice: Optional[str]) -> dict:
        return dict(
            resolve_delete_public_ip_action(
                vm,
                public_ip_action=choice,
                reserved_ip_label=reserved_ip_label,
                reserved_ip_billing_catalog=catalog,
            )
            or {}
        )

    ask = vm is not None and action is None and has_auto_assigned_public_ip(vm) and not assume_yes
    kwargs: dict = {}
    if vm is not None and not ask:
        kwargs = resolve(action)  # validated before the confirmation prompt
    elif vm is None:
        kwargs = compact_payload(
            public_ip_action=action,
            reserved_ip_label=reserved_ip_label,
            reserved_ip_billing_catalog=catalog,
        )
    confirm_destructive(settings, f"Delete {target}?", yes)
    if ask:
        reserve = typer.confirm(
            f"Reserve public IP {vm.get('public_ip')} as a Reserved IP (billing continues)?",
            default=False,
        )
        if reserve and catalog is None:
            raise typer.BadParameter(
                "Reserving the public IP needs --reserved-ip-billing-catalog (the Reserved IP SKU; copy "
                "billing_catalog from an existing Reserved IP in the same site). Nothing was deleted."
            )
        kwargs = resolve("reserve" if reserve else "release")
    if preflight or getattr(settings, "check_billing", False):
        if kwargs.get("public_ip_action") == "reserve":
            kwargs["preflight_billing"] = True
    result = _method(resource, spec, "delete")(
        vm_id=vm_id,
        workspace_id=workspace,
        idempotency_key=key,
        check_state=False,
        **kwargs,
        **compact_payload(requested_by=requested_by),
    )
    finish_operation(
        settings, client, workspace, result, "Delete", vm_id, wait_config, idempotency_key=key
    )


def power_action(
    ctx: typer.Context,
    spec: VmCommandSpec,
    vm_id: str,
    action: str,
    wait: bool,
    force: Optional[bool] = None,
    timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
    idempotency_key: Optional[str] = None,
    check_state: bool = True,
    client_factory: Any = None,
) -> None:
    settings = get_settings(ctx)
    wait_config = resolve_wait(wait, timeout, poll_interval)
    key = resolve_idempotency_key(idempotency_key, action, vm_id)
    workspace = require_workspace(settings)
    client = (client_factory or get_client)(settings)
    method = _method(getattr(client, spec.resource), spec, action)
    power_args = dict(
        workspace_id=workspace,
        vm_id=vm_id,
        idempotency_key=key,
        check_state=bool(check_state),
    )
    if force is not None:
        power_args["force"] = force
    result = method(**power_args)
    finish_operation(
        settings, client, workspace, result, action.capitalize(), vm_id, wait_config,
        idempotency_key=key,
    )


def register_vm_lifecycle(app: typer.Typer, spec: VmCommandSpec) -> None:
    """Attach the complete portal VM lifecycle to one VM command group."""

    display_label = spec.label[0].upper() + spec.label[1:]
    group = spec.group
    snapshots = typer.Typer(help=f"{display_label} snapshots", no_args_is_help=True)
    backup_policy = typer.Typer(help=f"{display_label} automated backup policy", no_args_is_help=True)
    backups = typer.Typer(help=f"{display_label} backup runs and restores", no_args_is_help=True)
    app.add_typer(snapshots, name="snapshots")
    app.add_typer(backup_policy, name="backup-policy")
    app.add_typer(backups, name="backups")

    @app.command("access-update")
    @handle_api_errors
    def access_update(
        ctx: typer.Context,
        vm_id: str = typer.Argument(..., help=f"{display_label} ID"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        admin_username: Optional[str] = typer.Option(None, "--admin-username"),
        ssh_key_mode: Optional[str] = typer.Option(None, "--ssh-key-mode", help="add or remove"),
        ssh_key: Optional[List[str]] = typer.Option(None, "--ssh-key", help="Inline public SSH key (repeatable)"),
        ssh_key_file: Optional[List[str]] = typer.Option(
            None, "--ssh-key-file", help="File with one public SSH key, e.g. ~/.ssh/id_ed25519.pub (repeatable)"
        ),
        ssh_key_id: Optional[List[str]] = typer.Option(None, "--ssh-key-id", help="Secret Store SSH key ID (repeatable)"),
        ssh_key_secret_ref: Optional[List[str]] = typer.Option(
            None,
            "--ssh-key-secret-ref",
            help='JSON object with ssh_key_id, secret_name, and optional store_key (repeatable)',
        ),
        password_stdin: bool = typer.Option(False, "--password-stdin", help="Read the new password from standard input"),
        prompt_password: bool = typer.Option(False, "--prompt-password", help="Prompt for the new password without echoing it"),
        password_auth_enabled: Optional[bool] = typer.Option(
            None,
            "--enable-password-auth/--disable-password-auth",
            help="Enable or disable SSH password authentication",
        ),
        confirm_remove_last_ssh_key: Optional[bool] = typer.Option(
            None,
            "--confirm-remove-last-ssh-key",
            help="Acknowledge that the final SSH key may be removed",
        ),
        check_state: bool = check_state_option(),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip SSH-key removal confirmation"),
        wait: bool = typer.Option(False, "--wait", help="Poll until the access update completes"),
        timeout: Optional[float] = timeout_option(),
        poll_interval: Optional[float] = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Change SSH keys, the admin password, or password authentication.

        Linux VMs only, while running. New passwords need at least 8 characters. Disabling password login needs an SSH key left on the VM; removing the last key while password login is off needs --confirm-remove-last-ssh-key.
        """
        wait_config = resolve_wait(wait, timeout, poll_interval)
        key = resolve_idempotency_key(idempotency_key, f"{spec.kind}-access", vm_id)
        new_password = _read_new_password(password_stdin, prompt_password)
        refs = _secret_refs(ssh_key_secret_ref)
        keys = [*(ssh_key or []), *read_text_files(ssh_key_file, "--ssh-key-file")] or None
        _require_change(
            ssh_key=keys,
            ssh_key_id=ssh_key_id,
            ssh_key_secret_ref=refs,
            new_password=new_password,
            password_auth_enabled=password_auth_enabled,
        )
        if ssh_key_mode not in (None, "add", "remove"):
            raise typer.BadParameter("--ssh-key-mode must be add or remove.")
        if ssh_key_mode == "remove":
            confirm_destructive(get_settings(ctx), f"Remove SSH access from {spec.label} '{vm_id}'?", yes)
        settings, workspace, client, resource = _resource(ctx, spec)
        result = getattr(resource, f"update_{spec.method_fragment}_access")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=key,
            **_check_state_arg(check_state),
            **compact_payload(
                requested_by=requested_by,
                admin_username=admin_username,
                ssh_key_mode=ssh_key_mode,
                ssh_keys=keys,
                ssh_key_ids=ssh_key_id,
                ssh_key_secret_refs=refs,
                new_password=new_password,
                password_auth_enabled=password_auth_enabled,
                confirm_remove_last_ssh_key=confirm_remove_last_ssh_key,
            ),
        )
        finish_operation(
            settings, client, workspace, result, "Access update", vm_id, wait_config,
            idempotency_key=key,
        )

    @app.command("resize-precheck")
    @handle_api_errors
    def resize_precheck(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        plan_id: Optional[str] = typer.Option(
            None, "--plan-id", help="Target plan (its CPU, RAM and disk); instead of --cpu/--ram-mb/--disk-gb"
        ),
        cpu: Optional[int] = typer.Option(None, "--cpu", help="vCPUs (1-256)"),
        ram_mb: Optional[int] = typer.Option(None, "--ram-mb", help="RAM in MB (257-2097152)"),
        disk_gb: Optional[int] = typer.Option(None, "--disk-gb", help="Root disk in GB (1-10000)"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Check whether a CPU, memory, or root-disk resize can run in place."""
        _require_change(cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb, plan_id=plan_id)
        settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"precheck_{spec.method_fragment}_resize")(
            vm_id,
            workspace_id=workspace,
            **compact_payload(
                plan_id=plan_id, cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb, requested_by=requested_by
            ),
        )
        print_json(result)
        decision = str(cell(result, "decision") or "").strip().lower()
        if decision in _PRECHECK_MESSAGES and not settings.structured_output:
            typer.secho(_PRECHECK_MESSAGES[decision], fg=typer.colors.YELLOW, err=True)

    def _resize_billing_args(
        billing_term: Optional[str],
        billing_catalog: Optional[str],
        billing_catalog_file: Optional[str],
        windows_license: Optional[str],
        windows_license_file: Optional[str],
    ) -> dict:
        normalize_billing_term(billing_term)
        return compact_payload(
            billing_term=billing_term,
            billing_catalog=load_json_input(billing_catalog, billing_catalog_file, "billing-catalog"),
            windows_license=load_json_input(windows_license, windows_license_file, "windows-license"),
        )

    billing_term_help = "Billing term for the new plan: " + ", ".join(BILLING_TERMS) + " (default HOURLY; needs --plan-id)"

    @app.command("resize")
    @handle_api_errors
    def resize(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        plan_id: Optional[str] = typer.Option(
            None, "--plan-id", help="Resize to this plan (the portal flow); bills the plan's SKU for the term"
        ),
        cpu: Optional[int] = typer.Option(None, "--cpu", help="vCPUs (1-256)"),
        ram_mb: Optional[int] = typer.Option(None, "--ram-mb", help="RAM in MB (257-2097152)"),
        disk_gb: Optional[int] = typer.Option(None, "--disk-gb", help="Root disk in GB (1-10000)"),
        billing_term: Optional[str] = typer.Option(None, "--billing-term", help=billing_term_help),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="Target billing SKU as JSON"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file", help="File with the target billing SKU JSON"),
        windows_license: Optional[str] = typer.Option(
            None, "--windows-license", help="Windows licence SKU as JSON (default: the VM's current licence)"
        ),
        windows_license_file: Optional[str] = typer.Option(None, "--windows-license-file"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        check_state: bool = check_state_option(),
        wait: bool = typer.Option(False, "--wait", help="Poll until the resize completes"),
        timeout: Optional[float] = timeout_option(),
        poll_interval: Optional[float] = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Resize CPU, memory, and optionally grow the root disk.

        A resize precheck runs first; the resize is sent only when it can run in place. A running VM is stopped and started again automatically.
        """
        wait_config = resolve_wait(wait, timeout, poll_interval)
        key = resolve_idempotency_key(idempotency_key, f"{spec.kind}-resize", vm_id)
        _require_change(cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb, plan_id=plan_id)
        billing = _resize_billing_args(
            billing_term, billing_catalog, billing_catalog_file, windows_license, windows_license_file
        )
        settings, workspace, client, resource = _resource(ctx, spec)
        result = _method(resource, spec, "resize")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=key,
            **_check_state_arg(check_state),
            **billing,
            **compact_payload(
                plan_id=plan_id, cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb, requested_by=requested_by
            ),
        )
        finish_operation(
            settings, client, workspace, result, "Resize", vm_id, wait_config,
            idempotency_key=key,
        )

    @app.command("resize-plan")
    @handle_api_errors
    def resize_plan(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        plan_id: Optional[str] = typer.Option(None, "--plan-id", help="Take CPU and RAM from this plan"),
        cpu: Optional[int] = typer.Option(None, "--cpu", help="vCPUs (1-256; required without --plan-id)"),
        ram_mb: Optional[int] = typer.Option(None, "--ram-mb", help="RAM in MB (257-2097152; required without --plan-id)"),
        allow_online: Optional[bool] = typer.Option(None, "--allow-online/--no-allow-online"),
        confirm_downgrade: bool = typer.Option(False, "--confirm-downgrade", help="Explicitly permit a smaller shape"),
        billing_term: Optional[str] = typer.Option(None, "--billing-term", help=billing_term_help),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="Target billing SKU as JSON"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        windows_license: Optional[str] = typer.Option(None, "--windows-license", help="Windows licence SKU as JSON"),
        windows_license_file: Optional[str] = typer.Option(None, "--windows-license-file"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        check_state: bool = check_state_option(),
        wait: bool = typer.Option(False, "--wait"),
        timeout: Optional[float] = timeout_option(),
        poll_interval: Optional[float] = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Change the CPU and memory shape, with explicit downgrade consent.

        An unchanged shape is rejected, and a smaller one needs --confirm-downgrade.
        """
        wait_config = resolve_wait(wait, timeout, poll_interval)
        key = resolve_idempotency_key(idempotency_key, f"{spec.kind}-resize-plan", vm_id)
        if plan_id is None and (cpu is None or ram_mb is None):
            raise typer.BadParameter("--cpu and --ram-mb are required (or pass --plan-id).")
        billing = _resize_billing_args(
            billing_term, billing_catalog, billing_catalog_file, windows_license, windows_license_file
        )
        settings, workspace, client, resource = _resource(ctx, spec)
        result = getattr(resource, f"resize_{spec.method_fragment}_plan")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=key,
            **_check_state_arg(check_state),
            **billing,
            **compact_payload(
                plan_id=plan_id,
                cpu=cpu,
                ram_mb=ram_mb,
                allow_online=_optional_bool(allow_online),
                confirm_downgrade=True if confirm_downgrade else None,
                requested_by=requested_by,
            ),
        )
        finish_operation(
            settings, client, workspace, result, "Plan resize", vm_id, wait_config,
            idempotency_key=key,
        )

    @app.command("resize-root-disk")
    @handle_api_errors
    def resize_root_disk(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        new_size_gb: int = typer.Option(..., "--new-size-gb", min=1, max=10000, help="New size in GB, larger than now"),
        allow_online: Optional[bool] = typer.Option(None, "--allow-online/--no-allow-online"),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="Billing SKU as JSON (optional)"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        check_state: bool = check_state_option(),
        wait: bool = typer.Option(False, "--wait"),
        timeout: Optional[float] = timeout_option(),
        poll_interval: Optional[float] = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Grow the root disk; shrinking is not supported."""
        wait_config = resolve_wait(wait, timeout, poll_interval)
        key = resolve_idempotency_key(idempotency_key, f"{spec.kind}-resize-disk", vm_id)
        catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
        settings, workspace, client, resource = _resource(ctx, spec)
        result = getattr(resource, f"resize_{spec.method_fragment}_root_disk")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=key,
            new_size_gb=new_size_gb,
            **_check_state_arg(check_state),
            **compact_payload(
                allow_online=_optional_bool(allow_online), billing_catalog=catalog, requested_by=requested_by
            ),
        )
        finish_operation(
            settings, client, workspace, result, "Root-disk resize", vm_id, wait_config,
            idempotency_key=key,
        )

    @app.command("volume-attach")
    @handle_api_errors
    def volume_attach(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        volume_id: str = typer.Argument(...),
        mode: Optional[str] = typer.Option(None, "--mode", help="single-writer (default) or multi-writer"),
        billing_catalog: Optional[str] = typer.Option(
            None, "--billing-catalog", help="Block Storage SKU as JSON (default: read from the volume)"
        ),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        check_state: bool = check_state_option(),
        wait: bool = typer.Option(False, "--wait"),
        timeout: Optional[float] = timeout_option(),
        poll_interval: Optional[float] = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Attach a persistent block volume.

        The volume must be unattached, idle and in the VM's site; its Block Storage SKU is read from the volume.
        """
        wait_config = resolve_wait(wait, timeout, poll_interval)
        key = resolve_idempotency_key(idempotency_key, f"{spec.kind}-volume-attach", volume_id)
        if mode is not None:
            _choice(mode, ATTACH_MODES, "--mode")
            validate_attach_mode(mode)
        catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
        settings, workspace, client, resource = _resource(ctx, spec)
        result = getattr(resource, f"attach_{spec.method_fragment}_volume")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=key,
            volume_id=volume_id,
            **_check_state_arg(check_state),
            **compact_payload(mode=mode, billing_catalog=catalog, requested_by=requested_by),
        )
        finish_operation(
            settings, client, workspace, result, "Volume attach", volume_id, wait_config,
            idempotency_key=key,
        )

    @app.command("volume-detach")
    @handle_api_errors
    def volume_detach(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        volume_id: str = typer.Argument(...),
        force: Optional[bool] = typer.Option(None, "--force/--no-force", help="Detach even if still mounted"),
        confirm_unmounted: Optional[bool] = typer.Option(
            None, "--confirm-unmounted", help="Confirm the volume is unmounted in the guest"
        ),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        check_state: bool = check_state_option(),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
        wait: bool = typer.Option(False, "--wait"),
        timeout: Optional[float] = timeout_option(),
        poll_interval: Optional[float] = poll_interval_option(),
        idempotency_key: Optional[str] = idempotency_key_option(),
    ) -> None:
        """Detach a persistent block volume after guest unmount.

        Needs --confirm-unmounted (unmount it in the guest first) or --force.
        """
        wait_config = resolve_wait(wait, timeout, poll_interval)
        key = resolve_idempotency_key(idempotency_key, f"{spec.kind}-volume-detach", volume_id)
        if confirm_unmounted is not True and force is not True:
            raise typer.BadParameter(
                "Unmount the volume in the guest and pass --confirm-unmounted, or pass --force."
            )
        prompt = f"Detach volume '{volume_id}' from {spec.label} '{vm_id}'?"
        if force and confirm_unmounted is not True:
            prompt = f"Force-detach volume '{volume_id}' from {spec.label} '{vm_id}' while it may still be mounted?"
        confirm_destructive(get_settings(ctx), prompt, yes)
        settings, workspace, client, resource = _resource(ctx, spec)
        result = getattr(resource, f"detach_{spec.method_fragment}_volume")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=key,
            volume_id=volume_id,
            **_check_state_arg(check_state),
            **compact_payload(
                force=_optional_bool(force),
                confirm_unmounted=confirm_unmounted,
                requested_by=requested_by,
            ),
        )
        finish_operation(
            settings, client, workspace, result, "Volume detach", volume_id, wait_config,
            idempotency_key=key,
        )

    @app.command("mount-guidance-acknowledge")
    @handle_api_errors
    def mount_guidance_acknowledge(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        volume_id: str = typer.Argument(...),
    ) -> None:
        """Record that the guest mount instructions were reviewed."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"acknowledge_{spec.method_fragment}_mount_guidance")(
            vm_id, workspace_id=workspace, volume_id=volume_id
        )
        print_json(result)

    @app.command("events")
    @handle_api_errors
    def events(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        limit: Optional[int] = typer.Option(None, "--limit", min=1, max=500, help="1-500 (default 100)"),
    ) -> None:
        """Show the VM lifecycle and operation event timeline."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"list_{spec.method_fragment}_events")(
            vm_id, workspace_id=workspace, **compact_payload(limit=limit)
        )
        print_json(result)

    @app.command("metrics-timeseries")
    @handle_api_errors
    def metrics_timeseries(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        metric_range: Optional[str] = typer.Option(None, "--range", help="30m, 1h, 6h, 24h, or 7d"),
    ) -> None:
        """Show rolled-up metric series for a supported time range."""
        if metric_range not in (None, *METRICS_RANGES):
            raise typer.BadParameter("--range must be 30m, 1h, 6h, 24h, or 7d.")
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"get_{spec.method_fragment}_metrics_timeseries")(
            vm_id, workspace_id=workspace, **compact_payload(range=metric_range)
        )
        print_json(result)

    @app.command("bandwidth")
    @handle_api_errors
    def bandwidth(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        month: Optional[str] = typer.Option(
            None, "--month", help="Calendar month in YYYY-MM format (default: the current UTC month)"
        ),
    ) -> None:
        """Show received and transmitted byte totals for one calendar month."""
        if month is not None:
            try:
                dt.datetime.strptime(month, "%Y-%m")
            except ValueError as exc:
                raise typer.BadParameter("--month must use YYYY-MM format.") from exc
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"get_{spec.method_fragment}_bandwidth")(
            vm_id, workspace_id=workspace, **compact_payload(month=month)
        )
        print_json(result)

    # -- snapshots -----------------------------------------------------------

    @snapshots.command("list")
    @handle_api_errors
    def snapshots_list(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        limit: Optional[int] = typer.Option(None, "--limit", min=1, max=200, help="1-200 (default 50)"),
        offset: Optional[int] = typer.Option(None, "--offset", min=0),
        search: Optional[str] = typer.Option(None, "--search"),
    ) -> None:
        """List recovery snapshots for one VM."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"list_{spec.method_fragment}_snapshots")(
            vm_id,
            workspace_id=workspace,
            **compact_payload(limit=limit, offset=offset, search=search),
        )
        print_json(result)

    @snapshots.command("create")
    @handle_api_errors
    def snapshots_create(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        name: str = typer.Argument(..., help="Snapshot name (1-255 characters)"),
        description: Optional[str] = typer.Option(None, "--description", help="At most 1024 characters"),
        mode: Optional[str] = typer.Option(
            None, "--mode", help="root_only, all_attached (default), or selective"
        ),
        selected_data_volume_id: Optional[List[str]] = typer.Option(
            None, "--selected-data-volume-id", help="Attached data volume to include (selective mode; repeatable)"
        ),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help=SNAPSHOT_SKU_HELP),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file", help="File with the SKU JSON"),
        preflight: bool = typer.Option(
            False, "--preflight-billing", help="Check billing eligibility for the SKU first (needs billing.read)"
        ),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        check_state: bool = check_state_option(),
        wait: bool = typer.Option(False, "--wait", help="Poll until the snapshot succeeds or fails"),
        timeout: Optional[float] = timeout_option(RECOVERY_WAIT_TIMEOUT_SECONDS),
        poll_interval: Optional[float] = poll_interval_option(),
    ) -> None:
        """Create a recovery snapshot set (billed as snapshot storage)."""
        _choice(mode, SNAPSHOT_MODES, "--mode")
        wait_config = resolve_wait(wait, timeout, poll_interval, default_timeout=RECOVERY_WAIT_TIMEOUT_SECONDS)
        catalog = _required_catalog(billing_catalog, billing_catalog_file, SNAPSHOT_SKU_HELP)
        settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"create_{spec.method_fragment}_snapshot")(
            vm_id,
            workspace_id=workspace,
            name=name,
            billing_catalog=catalog,
            **_check_state_arg(check_state),
            **compact_payload(
                description=description,
                mode=mode,
                selected_data_volume_ids=selected_data_volume_id,
                requested_by=requested_by,
                preflight_billing=True if preflight or getattr(settings, "check_billing", False) else None,
            ),
        )
        snapshot_id = cell(result, "snapshot_set_id") or cell(result, "id")
        if wait_config is None or not snapshot_id:
            print_json(result)
            return
        wait_for_recovery(
            settings,
            getattr(resource, f"wait_for_{spec.method_fragment}_snapshot"),
            str(snapshot_id),
            wait_config,
            label="Snapshot",
            resume=f"ibee {group} snapshots get {snapshot_id}",
            workspace_id=workspace,
            vm_id=vm_id,
        )

    @snapshots.command("get")
    @handle_api_errors
    def snapshots_get(
        ctx: typer.Context,
        snapshot_set_id: str = typer.Argument(...),
    ) -> None:
        """Get one workspace-owned snapshot set."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"get_{spec.method_fragment}_snapshot")(
            snapshot_set_id, workspace_id=workspace
        )
        print_json(result)

    @snapshots.command("delete")
    @handle_api_errors
    def snapshots_delete(
        ctx: typer.Context,
        snapshot_set_id: str = typer.Argument(...),
        check_state: bool = check_state_option(),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ) -> None:
        """Delete a snapshot set and its recovery points (not while a restore runs)."""
        confirm_destructive(get_settings(ctx), f"Delete snapshot set '{snapshot_set_id}'?", yes)
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"delete_{spec.method_fragment}_snapshot")(
            snapshot_set_id, workspace_id=workspace, **_check_state_arg(check_state)
        )
        print_json(result)

    target_volume_name_help = (
        "Name for a restored data volume, SOURCE_VOLUME_ID=NAME (new_vm only; repeatable; "
        "default <volume>-<kind>-restored-YYYYMMDD)"
    )

    @snapshots.command("restore")
    @handle_api_errors
    def snapshots_restore(
        ctx: typer.Context,
        vm_id: str = typer.Argument(..., help="The VM the snapshot was taken from"),
        snapshot_set_id: str = typer.Argument(...),
        target_mode: Optional[str] = typer.Option(None, "--target-mode", help="replace (default), new_vm, or volume_only"),
        target_vm_name: Optional[str] = typer.Option(
            None, "--target-vm-name", help="new_vm: name (default <vm>-snapshot-restored-YYYYMMDD)"
        ),
        target_cpu: Optional[int] = typer.Option(None, "--target-cpu"),
        target_ram_mb: Optional[int] = typer.Option(None, "--target-ram-mb"),
        target_disk_gb: Optional[int] = typer.Option(None, "--target-disk-gb"),
        target_plan_id: Optional[str] = typer.Option(
            None, "--target-plan-id", help="new_vm: plan (default: the VM's plan); fills the target shape and SKU"
        ),
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
        target_volume_name: Optional[List[str]] = typer.Option(None, "--target-volume-name", help=target_volume_name_help),
        target_billing_catalog: Optional[str] = typer.Option(
            None, "--target-billing-catalog", help="new_vm: billing SKU JSON (default: the target plan's)"
        ),
        target_billing_catalog_file: Optional[str] = typer.Option(None, "--target-billing-catalog-file"),
        vpc_id: Optional[str] = typer.Option(None, "--vpc-id", help="new_vm: VPC (with --subnet-id)"),
        subnet_id: Optional[str] = typer.Option(None, "--subnet-id", help="new_vm: subnet of --vpc-id"),
        network_connectivity: Optional[str] = typer.Option(
            None, "--network-connectivity", help="new_vm with a VPC: private (default), nat, or public_ip"
        ),
        ssh_key_id: Optional[List[str]] = typer.Option(None, "--ssh-key-id", help="new_vm: SSH key ID (repeatable)"),
        selected_volume_id: Optional[str] = typer.Option(
            None, "--selected-volume-id", help="volume_only: the captured volume to restore"
        ),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        auto_start: Optional[bool] = typer.Option(None, "--auto-start/--no-auto-start", help="Start after restore (default on)"),
        check_state: bool = check_state_option(),
        yes: bool = typer.Option(False, "--yes", "-y", help="Confirm the restore"),
        wait: bool = typer.Option(False, "--wait", help="Poll until the restore succeeds or fails"),
        timeout: Optional[float] = timeout_option(RECOVERY_WAIT_TIMEOUT_SECONDS),
        poll_interval: Optional[float] = poll_interval_option(),
    ) -> None:
        """Restore a snapshot by replacing a VM, creating a VM, or restoring a volume.

        The snapshot must be ready and the VM running or stopped. For new_vm the plan must fit the captured root disk; replace restarts a running VM.
        """
        _choice(target_mode, RESTORE_TARGET_MODES, "--target-mode")
        _choice(network_connectivity, NETWORK_CONNECTIVITY, "--network-connectivity")
        wait_config = resolve_wait(wait, timeout, poll_interval, default_timeout=RECOVERY_WAIT_TIMEOUT_SECONDS)
        kwargs = _restore_kwargs(
            target_mode=target_mode,
            target_vm_name=target_vm_name,
            target_cpu=target_cpu,
            target_ram_mb=target_ram_mb,
            target_disk_gb=target_disk_gb,
            target_plan_id=target_plan_id,
            target_plan_name=target_plan_name,
            target_plan_code=target_plan_code,
            target_plan_type=target_plan_type,
            target_performance_category=target_performance_category,
            target_plan_monthly_rate=target_plan_monthly_rate,
            target_plan_hourly_rate=target_plan_hourly_rate,
            target_bandwidth_tb=target_bandwidth_tb,
            target_bandwidth_display=target_bandwidth_display,
            target_network_bandwidth=target_network_bandwidth,
            target_compute_node_id=target_compute_node_id,
            target_gpu_type=target_gpu_type,
            target_gpu_model=target_gpu_model,
            target_gpu_count=target_gpu_count,
            target_gpu_memory_gb=target_gpu_memory_gb,
            target_gpu_memory_display=target_gpu_memory_display,
            target_site_id=target_site_id,
            target_site_name=target_site_name,
            selected_volume_id=selected_volume_id,
            requested_by=requested_by,
            auto_start=auto_start,
            target_volume_names=parse_key_value_pairs(target_volume_name, "--target-volume-name"),
            target_billing_catalog=load_json_input(
                target_billing_catalog, target_billing_catalog_file, "target-billing-catalog"
            ),
            vpc_id=vpc_id,
            subnet_id=subnet_id,
            network_connectivity=network_connectivity,
            ssh_key_ids=ssh_key_id or None,
        )
        _check_restore_request(kwargs)
        confirm_destructive(get_settings(ctx), f"Restore snapshot '{snapshot_set_id}' for {spec.label} '{vm_id}'?", yes)
        settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"restore_{spec.method_fragment}_snapshot")(
            snapshot_set_id,
            workspace_id=workspace,
            vm_id=vm_id,
            **_check_state_arg(check_state),
            **kwargs,
        )
        _finish_restore(settings, resource, workspace, result, wait_config, "snapshot")

    def _finish_restore(settings, resource, workspace, result, wait_config, kind: str) -> None:
        restore_id = cell(result, "restore_id") or cell(result, "id")
        if wait_config is None or not restore_id:
            print_json(result)
            return
        wait_for_recovery(
            settings,
            getattr(resource, f"wait_for_{spec.method_fragment}_{kind}_restore"),
            str(restore_id),
            wait_config,
            label="Restore",
            resume=f"ibee {group} {'snapshots' if kind == 'snapshot' else 'backups'} restore-status {restore_id} --wait",
            workspace_id=workspace,
        )

    def _restore_status(ctx, restore_id, kind, wait, timeout, poll_interval) -> None:
        wait_config = resolve_wait(wait, timeout, poll_interval, default_timeout=RECOVERY_WAIT_TIMEOUT_SECONDS)
        settings, workspace, _client, resource = _resource(ctx, spec)
        if wait_config is not None:
            _finish_restore(settings, resource, workspace, {"restore_id": restore_id}, wait_config, kind)
            return
        result = getattr(resource, f"get_{spec.method_fragment}_{kind}_restore")(
            restore_id, workspace_id=workspace
        )
        print_json(result)

    @snapshots.command("restore-status")
    @handle_api_errors
    def snapshots_restore_status(
        ctx: typer.Context,
        restore_id: str = typer.Argument(...),
        wait: bool = typer.Option(False, "--wait", help="Poll until the restore succeeds or fails"),
        timeout: Optional[float] = timeout_option(RECOVERY_WAIT_TIMEOUT_SECONDS),
        poll_interval: Optional[float] = poll_interval_option(),
    ) -> None:
        """Get the current snapshot restore status."""
        _restore_status(ctx, restore_id, "snapshot", wait, timeout, poll_interval)

    # -- backup policy -------------------------------------------------------

    def _policy_kwargs(
        frequency: Optional[str],
        timezone: Optional[str],
        hour: Optional[int],
        minute: Optional[int],
        day_of_week: Optional[int],
        window_minutes: Optional[int],
        retention_days: Optional[int],
        full_backup_interval_days: Optional[int],
        incremental_enabled: Optional[bool],
        requested_by: Optional[str],
    ) -> dict:
        if frequency not in (None, *BACKUP_FREQUENCIES):
            raise typer.BadParameter("--frequency must be daily or weekly.")
        return compact_payload(
            schedule=_schedule(frequency, timezone, hour, minute, day_of_week, window_minutes),
            retention_days=retention_days,
            full_backup_interval_days=full_backup_interval_days,
            incremental_enabled=incremental_enabled,
            requested_by=requested_by,
        )

    @backup_policy.command("get")
    @handle_api_errors
    def policy_get(ctx: typer.Context, vm_id: str = typer.Argument(...)) -> None:
        """Get the effective automated backup policy."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"get_{spec.method_fragment}_backup_policy")(
            vm_id, workspace_id=workspace
        )
        print_json(result)

    frequency_help = "daily or weekly (weekly needs --day-of-week)"
    day_help = "0 (Monday) - 6 (Sunday); weekly only"
    timezone_help = "IANA time zone, e.g. Asia/Kolkata (default UTC)"

    @backup_policy.command("update")
    @handle_api_errors
    def policy_update(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        frequency: Optional[str] = typer.Option(None, "--frequency", help=frequency_help),
        timezone: Optional[str] = typer.Option(None, "--timezone", help=timezone_help),
        hour: Optional[int] = typer.Option(None, "--hour", min=0, max=23),
        minute: Optional[int] = typer.Option(None, "--minute", min=0, max=59),
        day_of_week: Optional[int] = typer.Option(None, "--day-of-week", min=0, max=6, help=day_help),
        window_minutes: Optional[int] = typer.Option(None, "--window-minutes", min=5, max=180, help="5-180 (default 30)"),
        retention_days: Optional[int] = typer.Option(None, "--retention-days", min=1, max=365),
        full_backup_interval_days: Optional[int] = typer.Option(None, "--full-backup-interval-days", min=1, max=30),
        incremental_enabled: Optional[bool] = typer.Option(None, "--incremental/--no-incremental"),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help="Replacement backup SKU JSON (optional)"),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        check_state: bool = check_state_option(),
    ) -> None:
        """Update backup schedule and retention settings (backups must be enabled)."""
        kwargs = _policy_kwargs(
            frequency, timezone, hour, minute, day_of_week, window_minutes,
            retention_days, full_backup_interval_days, incremental_enabled, requested_by,
        )
        catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
        if catalog is not None:
            kwargs["billing_catalog"] = catalog
        _require_change(**{key: value for key, value in kwargs.items() if key != "requested_by"})
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"update_{spec.method_fragment}_backup_policy")(
            vm_id, workspace_id=workspace, **_check_state_arg(check_state), **kwargs
        )
        print_json(result)

    @backup_policy.command("enable")
    @handle_api_errors
    def policy_enable(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help=BACKUP_SKU_HELP),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file", help="File with the SKU JSON"),
        frequency: Optional[str] = typer.Option(None, "--frequency", help=frequency_help + "; default daily"),
        timezone: Optional[str] = typer.Option(None, "--timezone", help=timezone_help),
        hour: Optional[int] = typer.Option(None, "--hour", min=0, max=23, help="Default 12"),
        minute: Optional[int] = typer.Option(None, "--minute", min=0, max=59, help="Default 0"),
        day_of_week: Optional[int] = typer.Option(None, "--day-of-week", min=0, max=6, help=day_help),
        window_minutes: Optional[int] = typer.Option(None, "--window-minutes", min=5, max=180, help="5-180 (default 30)"),
        retention_days: Optional[int] = typer.Option(None, "--retention-days", min=1, max=365, help="1-365 (default 7)"),
        full_backup_interval_days: Optional[int] = typer.Option(
            None, "--full-backup-interval-days", min=1, max=30, help="1-30 (default 7)"
        ),
        incremental_enabled: Optional[bool] = typer.Option(None, "--incremental/--no-incremental"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Enable automated backups (billed as backup storage).

        Values you leave out come from the saved policy when there is one, otherwise from the portal defaults (daily at 12:00 UTC, 30-minute window, 7-day retention).
        """
        kwargs = _policy_kwargs(
            frequency, timezone, hour, minute, day_of_week, window_minutes,
            retention_days, full_backup_interval_days, incremental_enabled, requested_by,
        )
        kwargs["billing_catalog"] = _required_catalog(billing_catalog, billing_catalog_file, BACKUP_SKU_HELP)
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"enable_{spec.method_fragment}_backups")(
            vm_id, workspace_id=workspace, **kwargs
        )
        print_json(result)

    @backup_policy.command("disable")
    @handle_api_errors
    def policy_disable(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Disable future backup runs without deleting recovery points."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"disable_{spec.method_fragment}_backups")(
            vm_id, workspace_id=workspace, **compact_payload(requested_by=requested_by)
        )
        print_json(result)

    @backup_policy.command("reschedule")
    @handle_api_errors
    def policy_reschedule(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        next_run_at: str = typer.Option(..., "--next-run-at", help="Timezone-aware ISO 8601 timestamp"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Set the next automated backup execution time."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"reschedule_{spec.method_fragment}_backup")(
            vm_id,
            workspace_id=workspace,
            next_run_at=_parse_datetime(next_run_at),
            **compact_payload(requested_by=requested_by),
        )
        print_json(result)

    # -- backup runs ---------------------------------------------------------

    @backups.command("list")
    @handle_api_errors
    def backups_list(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        limit: Optional[int] = typer.Option(None, "--limit", min=1, max=200, help="1-200 (default 50)"),
        offset: Optional[int] = typer.Option(None, "--offset", min=0),
        search: Optional[str] = typer.Option(None, "--search"),
        restorable_only: bool = typer.Option(
            False, "--restorable-only", help="Only succeeded runs (the ones that can be restored)"
        ),
    ) -> None:
        """List backup runs and usable recovery points for one VM."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"list_{spec.method_fragment}_backup_runs")(
            vm_id,
            workspace_id=workspace,
            **compact_payload(
                limit=limit, offset=offset, search=search, restorable_only=True if restorable_only else None
            ),
        )
        print_json(result)

    @backups.command("list-all")
    @handle_api_errors
    def backups_list_all(
        ctx: typer.Context,
        vm_id: Optional[str] = typer.Option(None, "--vm-id", help="Only this VM's backups"),
        status: Optional[List[str]] = typer.Option(
            None, "--status", help="Status to include (repeatable): " + ", ".join(BACKUP_RUN_STATUSES)
        ),
        limit: Optional[int] = typer.Option(None, "--limit", min=1, max=200, help="1-200 (default 50)"),
        offset: Optional[int] = typer.Option(None, "--offset", min=0),
        search: Optional[str] = typer.Option(None, "--search"),
    ) -> None:
        """List backups across the workspace (the portal Backups page).

        Not yet part of the published API contract; behaviour may change.
        """
        statuses = validate_backup_statuses(status) if status else None
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"list_all_{spec.method_fragment}_backup_runs")(
            workspace_id=workspace,
            **compact_payload(vm_id=vm_id, status=statuses, limit=limit, offset=offset, search=search),
        )
        print_json(result)

    @backups.command("create")
    @handle_api_errors
    def backups_create(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        billing_catalog: Optional[str] = typer.Option(None, "--billing-catalog", help=BACKUP_SKU_HELP),
        billing_catalog_file: Optional[str] = typer.Option(None, "--billing-catalog-file", help="File with the SKU JSON"),
        reason: Optional[str] = typer.Option(None, "--reason", help="At most 512 characters"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        check_state: bool = check_state_option(),
        wait: bool = typer.Option(False, "--wait", help="Poll until the backup run succeeds or fails"),
        timeout: Optional[float] = timeout_option(RECOVERY_WAIT_TIMEOUT_SECONDS),
        poll_interval: Optional[float] = poll_interval_option(),
    ) -> None:
        """Queue a manual backup run (backups must be enabled; never retried automatically)."""
        wait_config = resolve_wait(wait, timeout, poll_interval, default_timeout=RECOVERY_WAIT_TIMEOUT_SECONDS)
        catalog = _required_catalog(billing_catalog, billing_catalog_file, BACKUP_SKU_HELP)
        settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"create_{spec.method_fragment}_backup_run")(
            vm_id,
            workspace_id=workspace,
            billing_catalog=catalog,
            **_check_state_arg(check_state),
            **compact_payload(reason=reason, requested_by=requested_by),
        )
        run_id = cell(result, "run_id") or cell(result, "id")
        if wait_config is None or not run_id:
            print_json(result)
            return
        wait_for_recovery(
            settings,
            getattr(resource, f"wait_for_{spec.method_fragment}_backup_run"),
            str(run_id),
            wait_config,
            label="Backup run",
            resume=f"ibee {group} backups get {run_id}",
            workspace_id=workspace,
        )

    @backups.command("get")
    @handle_api_errors
    def backups_get(ctx: typer.Context, run_id: str = typer.Argument(...)) -> None:
        """Get one workspace-owned backup run or recovery point."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"get_{spec.method_fragment}_backup_run")(
            run_id, workspace_id=workspace
        )
        print_json(result)

    @backups.command("delete")
    @handle_api_errors
    def backups_delete(
        ctx: typer.Context,
        run_id: str = typer.Argument(..., help="Backup run (recovery point) ID"),
        check_state: bool = check_state_option(),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ) -> None:
        """Delete a completed backup (recovery point).

        Not yet part of the published API contract; behaviour may change. Backups that a newer incremental depends on, or that are being restored, cannot be deleted.
        """
        confirm_destructive(get_settings(ctx), f"Delete backup '{run_id}'?", yes)
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"delete_{spec.method_fragment}_backup_run")(
            run_id, workspace_id=workspace, **_check_state_arg(check_state)
        )
        print_json(result)

    @backups.command("restore")
    @handle_api_errors
    def backups_restore(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        recovery_point_id: str = typer.Argument(...),
        target_mode: Optional[str] = typer.Option(None, "--target-mode", help="replace (default), new_vm, or volume_only"),
        target_vm_name: Optional[str] = typer.Option(
            None, "--target-vm-name", help="new_vm: name (default <vm>-backup-restored-YYYYMMDD)"
        ),
        target_cpu: Optional[int] = typer.Option(None, "--target-cpu"),
        target_ram_mb: Optional[int] = typer.Option(None, "--target-ram-mb"),
        target_disk_gb: Optional[int] = typer.Option(None, "--target-disk-gb"),
        target_plan_id: Optional[str] = typer.Option(
            None, "--target-plan-id", help="new_vm: plan (default: the VM's plan); fills the target shape and SKU"
        ),
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
        target_volume_name: Optional[List[str]] = typer.Option(None, "--target-volume-name", help=target_volume_name_help),
        target_billing_catalog: Optional[str] = typer.Option(
            None, "--target-billing-catalog", help="new_vm: billing SKU JSON (default: the target plan's)"
        ),
        target_billing_catalog_file: Optional[str] = typer.Option(None, "--target-billing-catalog-file"),
        selected_volume_id: Optional[str] = typer.Option(
            None, "--selected-volume-id", help="volume_only: the captured volume to restore"
        ),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        auto_start: Optional[bool] = typer.Option(
            None, "--auto-start/--no-auto-start", help="Ignored for backup restores (the API does not use it)"
        ),
        check_state: bool = check_state_option(),
        yes: bool = typer.Option(False, "--yes", "-y", help="Confirm the restore"),
        wait: bool = typer.Option(False, "--wait", help="Poll until the restore succeeds or fails"),
        timeout: Optional[float] = timeout_option(RECOVERY_WAIT_TIMEOUT_SECONDS),
        poll_interval: Optional[float] = poll_interval_option(),
    ) -> None:
        """Restore a backup by replacing a VM, creating a VM, or restoring a volume.

        Only succeeded backups can be restored. For new_vm the plan must fit the captured root disk.
        """
        _choice(target_mode, RESTORE_TARGET_MODES, "--target-mode")
        wait_config = resolve_wait(wait, timeout, poll_interval, default_timeout=RECOVERY_WAIT_TIMEOUT_SECONDS)
        kwargs = _restore_kwargs(
            target_mode=target_mode,
            target_vm_name=target_vm_name,
            target_cpu=target_cpu,
            target_ram_mb=target_ram_mb,
            target_disk_gb=target_disk_gb,
            target_plan_id=target_plan_id,
            target_plan_name=target_plan_name,
            target_plan_code=target_plan_code,
            target_plan_type=target_plan_type,
            target_performance_category=target_performance_category,
            target_plan_monthly_rate=target_plan_monthly_rate,
            target_plan_hourly_rate=target_plan_hourly_rate,
            target_bandwidth_tb=target_bandwidth_tb,
            target_bandwidth_display=target_bandwidth_display,
            target_network_bandwidth=target_network_bandwidth,
            target_compute_node_id=target_compute_node_id,
            target_gpu_type=target_gpu_type,
            target_gpu_model=target_gpu_model,
            target_gpu_count=target_gpu_count,
            target_gpu_memory_gb=target_gpu_memory_gb,
            target_gpu_memory_display=target_gpu_memory_display,
            target_site_id=target_site_id,
            target_site_name=target_site_name,
            selected_volume_id=selected_volume_id,
            requested_by=requested_by,
            auto_start=auto_start,
            target_volume_names=parse_key_value_pairs(target_volume_name, "--target-volume-name"),
            target_billing_catalog=load_json_input(
                target_billing_catalog, target_billing_catalog_file, "target-billing-catalog"
            ),
        )
        _check_restore_request(kwargs)
        confirm_destructive(get_settings(ctx), f"Restore recovery point '{recovery_point_id}' for {spec.label} '{vm_id}'?", yes)
        settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"restore_{spec.method_fragment}_backup")(
            vm_id,
            workspace_id=workspace,
            recovery_point_id=recovery_point_id,
            **_check_state_arg(check_state),
            **kwargs,
        )
        _finish_restore(settings, resource, workspace, result, wait_config, "backup")

    @backups.command("restore-status")
    @handle_api_errors
    def backups_restore_status(
        ctx: typer.Context,
        restore_id: str = typer.Argument(...),
        wait: bool = typer.Option(False, "--wait", help="Poll until the restore succeeds or fails"),
        timeout: Optional[float] = timeout_option(RECOVERY_WAIT_TIMEOUT_SECONDS),
        poll_interval: Optional[float] = poll_interval_option(),
    ) -> None:
        """Get the current backup restore status."""
        _restore_status(ctx, restore_id, "backup", wait, timeout, poll_interval)


__all__ = [
    "CLOUD_VM",
    "GPU_VM",
    "IbeeValidationError",
    "VmCommandSpec",
    "create_vms",
    "delete_vm",
    "power_action",
    "register_vm_lifecycle",
]
