"""Shared cloud and GPU VM lifecycle command registration."""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from typing import List, Optional

import typer

from ..context import get_client, get_settings, require_workspace
from ..helpers import compact_payload, finish_operation, new_idempotency_key, parse_json_object
from ..render import handle_api_errors, print_json


@dataclass(frozen=True)
class VmCommandSpec:
    """Names that differ between the cloud and GPU generated SDK resources."""

    kind: str
    resource: str
    method_fragment: str
    label: str


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
    )


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


def register_vm_lifecycle(app: typer.Typer, spec: VmCommandSpec) -> None:
    """Attach the complete portal VM lifecycle to one VM command group."""

    display_label = spec.label[0].upper() + spec.label[1:]
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
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip SSH-key removal confirmation"),
        wait: bool = typer.Option(False, "--wait", help="Poll until the access update completes"),
    ) -> None:
        """Change SSH keys, the admin password, or password authentication."""
        new_password = _read_new_password(password_stdin, prompt_password)
        refs = _secret_refs(ssh_key_secret_ref)
        _require_change(
            ssh_key=ssh_key,
            ssh_key_id=ssh_key_id,
            ssh_key_secret_ref=refs,
            new_password=new_password,
            password_auth_enabled=password_auth_enabled,
        )
        if ssh_key_mode not in (None, "add", "remove"):
            raise typer.BadParameter("--ssh-key-mode must be add or remove.")
        if ssh_key_mode == "remove" and not yes:
            typer.confirm(f"Remove SSH access from {spec.label} '{vm_id}'?", abort=True)
        settings, workspace, client, resource = _resource(ctx, spec)
        result = getattr(resource, f"update_{spec.method_fragment}_access")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=new_idempotency_key(f"{spec.kind}-access", vm_id),
            **compact_payload(
                requested_by=requested_by,
                admin_username=admin_username,
                ssh_key_mode=ssh_key_mode,
                ssh_keys=ssh_key,
                ssh_key_ids=ssh_key_id,
                ssh_key_secret_refs=refs,
                new_password=new_password,
                password_auth_enabled=password_auth_enabled,
                confirm_remove_last_ssh_key=confirm_remove_last_ssh_key,
            ),
        )
        finish_operation(settings, client, workspace, result, "Access update", vm_id, wait)

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
        """Check whether a CPU, memory, or root-disk resize is allowed."""
        _require_change(cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb)
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"precheck_{spec.method_fragment}_resize")(
            vm_id,
            workspace_id=workspace,
            **compact_payload(cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb, requested_by=requested_by),
        )
        print_json(result)

    @app.command("resize")
    @handle_api_errors
    def resize(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        cpu: Optional[int] = typer.Option(None, "--cpu"),
        ram_mb: Optional[int] = typer.Option(None, "--ram-mb"),
        disk_gb: Optional[int] = typer.Option(None, "--disk-gb"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        wait: bool = typer.Option(False, "--wait", help="Poll until the resize completes"),
    ) -> None:
        """Resize CPU, memory, and optionally grow the root disk."""
        _require_change(cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb)
        settings, workspace, client, resource = _resource(ctx, spec)
        result = _method(resource, spec, "resize")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=new_idempotency_key(f"{spec.kind}-resize", vm_id),
            **compact_payload(cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb, requested_by=requested_by),
        )
        finish_operation(settings, client, workspace, result, "Resize", vm_id, wait)

    @app.command("resize-plan")
    @handle_api_errors
    def resize_plan(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        cpu: int = typer.Option(..., "--cpu"),
        ram_mb: int = typer.Option(..., "--ram-mb"),
        allow_online: Optional[bool] = typer.Option(None, "--allow-online/--no-allow-online"),
        confirm_downgrade: bool = typer.Option(False, "--confirm-downgrade", help="Explicitly permit a smaller shape"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        wait: bool = typer.Option(False, "--wait"),
    ) -> None:
        """Change the CPU and memory shape, with explicit downgrade consent."""
        settings, workspace, client, resource = _resource(ctx, spec)
        result = getattr(resource, f"resize_{spec.method_fragment}_plan")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=new_idempotency_key(f"{spec.kind}-resize-plan", vm_id),
            cpu=cpu,
            ram_mb=ram_mb,
            **compact_payload(
                allow_online=_optional_bool(allow_online),
                confirm_downgrade=True if confirm_downgrade else None,
                requested_by=requested_by,
            ),
        )
        finish_operation(settings, client, workspace, result, "Plan resize", vm_id, wait)

    @app.command("resize-root-disk")
    @handle_api_errors
    def resize_root_disk(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        new_size_gb: int = typer.Option(..., "--new-size-gb", min=1),
        allow_online: Optional[bool] = typer.Option(None, "--allow-online/--no-allow-online"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        wait: bool = typer.Option(False, "--wait"),
    ) -> None:
        """Grow the root disk; shrinking is not supported."""
        settings, workspace, client, resource = _resource(ctx, spec)
        result = getattr(resource, f"resize_{spec.method_fragment}_root_disk")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=new_idempotency_key(f"{spec.kind}-resize-disk", vm_id),
            new_size_gb=new_size_gb,
            **compact_payload(allow_online=_optional_bool(allow_online), requested_by=requested_by),
        )
        finish_operation(settings, client, workspace, result, "Root-disk resize", vm_id, wait)

    @app.command("volume-attach")
    @handle_api_errors
    def volume_attach(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        volume_id: str = typer.Argument(...),
        mode: Optional[str] = typer.Option(None, "--mode", help="single-writer or multi-writer"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        wait: bool = typer.Option(False, "--wait"),
    ) -> None:
        """Attach a persistent block volume."""
        if mode not in (None, "single-writer", "multi-writer"):
            raise typer.BadParameter("--mode must be single-writer or multi-writer.")
        settings, workspace, client, resource = _resource(ctx, spec)
        result = getattr(resource, f"attach_{spec.method_fragment}_volume")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=new_idempotency_key(f"{spec.kind}-volume-attach", volume_id),
            volume_id=volume_id,
            **compact_payload(mode=mode, requested_by=requested_by),
        )
        finish_operation(settings, client, workspace, result, "Volume attach", volume_id, wait)

    @app.command("volume-detach")
    @handle_api_errors
    def volume_detach(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        volume_id: str = typer.Argument(...),
        force: Optional[bool] = typer.Option(None, "--force/--no-force"),
        confirm_unmounted: Optional[bool] = typer.Option(None, "--confirm-unmounted"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
        wait: bool = typer.Option(False, "--wait"),
    ) -> None:
        """Detach a persistent block volume after guest unmount."""
        if not yes:
            typer.confirm(f"Detach volume '{volume_id}' from {spec.label} '{vm_id}'?", abort=True)
        settings, workspace, client, resource = _resource(ctx, spec)
        result = getattr(resource, f"detach_{spec.method_fragment}_volume")(
            vm_id,
            workspace_id=workspace,
            idempotency_key=new_idempotency_key(f"{spec.kind}-volume-detach", volume_id),
            volume_id=volume_id,
            **compact_payload(
                force=_optional_bool(force),
                confirm_unmounted=confirm_unmounted,
                requested_by=requested_by,
            ),
        )
        finish_operation(settings, client, workspace, result, "Volume detach", volume_id, wait)

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
        limit: Optional[int] = typer.Option(None, "--limit", min=1),
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
        if metric_range not in (None, "30m", "1h", "6h", "24h", "7d"):
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
        month: str = typer.Option(..., "--month", help="Calendar month in YYYY-MM format"),
    ) -> None:
        """Show received and transmitted byte totals for one calendar month."""
        try:
            dt.datetime.strptime(month, "%Y-%m")
        except ValueError as exc:
            raise typer.BadParameter("--month must use YYYY-MM format.") from exc
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"get_{spec.method_fragment}_bandwidth")(
            vm_id, workspace_id=workspace, month=month
        )
        print_json(result)

    @snapshots.command("list")
    @handle_api_errors
    def snapshots_list(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        limit: Optional[int] = typer.Option(None, "--limit", min=1),
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
        name: str = typer.Argument(...),
        description: Optional[str] = typer.Option(None, "--description"),
        mode: Optional[str] = typer.Option(None, "--mode", help="root_only, all_attached, or selective"),
        selected_data_volume_id: Optional[List[str]] = typer.Option(None, "--selected-data-volume-id"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Create a recovery snapshot set."""
        if mode not in (None, "root_only", "all_attached", "selective"):
            raise typer.BadParameter("--mode must be root_only, all_attached, or selective.")
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"create_{spec.method_fragment}_snapshot")(
            vm_id,
            workspace_id=workspace,
            name=name,
            **compact_payload(
                description=description,
                mode=mode,
                selected_data_volume_ids=selected_data_volume_id,
                requested_by=requested_by,
            ),
        )
        print_json(result)

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
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ) -> None:
        """Delete a snapshot set and its recovery points."""
        if not yes:
            typer.confirm(f"Delete snapshot set '{snapshot_set_id}'?", abort=True)
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"delete_{spec.method_fragment}_snapshot")(
            snapshot_set_id, workspace_id=workspace
        )
        print_json(result)

    @snapshots.command("restore")
    @handle_api_errors
    def snapshots_restore(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        snapshot_set_id: str = typer.Argument(...),
        target_mode: Optional[str] = typer.Option(None, "--target-mode", help="replace, new_vm, or volume_only"),
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
        """Restore a snapshot by replacing a VM, creating a VM, or restoring a volume."""
        if target_mode not in (None, "replace", "new_vm", "volume_only"):
            raise typer.BadParameter("--target-mode must be replace, new_vm, or volume_only.")
        if not yes:
            typer.confirm(f"Restore snapshot '{snapshot_set_id}' for {spec.label} '{vm_id}'?", abort=True)
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"restore_{spec.method_fragment}_snapshot")(
            snapshot_set_id,
            workspace_id=workspace,
            vm_id=vm_id,
            **_restore_kwargs(
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
            ),
        )
        print_json(result)

    @snapshots.command("restore-status")
    @handle_api_errors
    def snapshots_restore_status(
        ctx: typer.Context,
        restore_id: str = typer.Argument(...),
    ) -> None:
        """Get the current snapshot restore status."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"get_{spec.method_fragment}_snapshot_restore")(
            restore_id, workspace_id=workspace
        )
        print_json(result)

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
        if frequency not in (None, "hourly", "daily", "weekly"):
            raise typer.BadParameter("--frequency must be hourly, daily, or weekly.")
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

    @backup_policy.command("update")
    @handle_api_errors
    def policy_update(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        frequency: Optional[str] = typer.Option(None, "--frequency"),
        timezone: Optional[str] = typer.Option(None, "--timezone"),
        hour: Optional[int] = typer.Option(None, "--hour", min=0, max=23),
        minute: Optional[int] = typer.Option(None, "--minute", min=0, max=59),
        day_of_week: Optional[int] = typer.Option(None, "--day-of-week", min=0, max=6),
        window_minutes: Optional[int] = typer.Option(None, "--window-minutes", min=1),
        retention_days: Optional[int] = typer.Option(None, "--retention-days", min=1),
        full_backup_interval_days: Optional[int] = typer.Option(None, "--full-backup-interval-days", min=1),
        incremental_enabled: Optional[bool] = typer.Option(None, "--incremental/--no-incremental"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Update backup schedule and retention settings."""
        kwargs = _policy_kwargs(
            frequency, timezone, hour, minute, day_of_week, window_minutes,
            retention_days, full_backup_interval_days, incremental_enabled, requested_by,
        )
        _require_change(**kwargs)
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"update_{spec.method_fragment}_backup_policy")(
            vm_id, workspace_id=workspace, **kwargs
        )
        print_json(result)

    @backup_policy.command("enable")
    @handle_api_errors
    def policy_enable(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        frequency: Optional[str] = typer.Option(None, "--frequency"),
        timezone: Optional[str] = typer.Option(None, "--timezone"),
        hour: Optional[int] = typer.Option(None, "--hour", min=0, max=23),
        minute: Optional[int] = typer.Option(None, "--minute", min=0, max=59),
        day_of_week: Optional[int] = typer.Option(None, "--day-of-week", min=0, max=6),
        window_minutes: Optional[int] = typer.Option(None, "--window-minutes", min=1),
        retention_days: Optional[int] = typer.Option(None, "--retention-days", min=1),
        full_backup_interval_days: Optional[int] = typer.Option(None, "--full-backup-interval-days", min=1),
        incremental_enabled: Optional[bool] = typer.Option(None, "--incremental/--no-incremental"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Enable automated backups and create the policy."""
        kwargs = _policy_kwargs(
            frequency, timezone, hour, minute, day_of_week, window_minutes,
            retention_days, full_backup_interval_days, incremental_enabled, requested_by,
        )
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

    @backups.command("list")
    @handle_api_errors
    def backups_list(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        limit: Optional[int] = typer.Option(None, "--limit", min=1),
        offset: Optional[int] = typer.Option(None, "--offset", min=0),
        search: Optional[str] = typer.Option(None, "--search"),
    ) -> None:
        """List backup runs and usable recovery points for one VM."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"list_{spec.method_fragment}_backup_runs")(
            vm_id,
            workspace_id=workspace,
            **compact_payload(limit=limit, offset=offset, search=search),
        )
        print_json(result)

    @backups.command("create")
    @handle_api_errors
    def backups_create(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        reason: Optional[str] = typer.Option(None, "--reason"),
        requested_by: Optional[str] = typer.Option(None, "--requested-by"),
    ) -> None:
        """Queue a manual backup run."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"create_{spec.method_fragment}_backup_run")(
            vm_id,
            workspace_id=workspace,
            **compact_payload(reason=reason, requested_by=requested_by),
        )
        print_json(result)

    @backups.command("get")
    @handle_api_errors
    def backups_get(ctx: typer.Context, run_id: str = typer.Argument(...)) -> None:
        """Get one workspace-owned backup run or recovery point."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"get_{spec.method_fragment}_backup_run")(
            run_id, workspace_id=workspace
        )
        print_json(result)

    @backups.command("restore")
    @handle_api_errors
    def backups_restore(
        ctx: typer.Context,
        vm_id: str = typer.Argument(...),
        recovery_point_id: str = typer.Argument(...),
        target_mode: Optional[str] = typer.Option(None, "--target-mode", help="replace, new_vm, or volume_only"),
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
        """Restore a backup by replacing a VM, creating a VM, or restoring a volume."""
        if target_mode not in (None, "replace", "new_vm", "volume_only"):
            raise typer.BadParameter("--target-mode must be replace, new_vm, or volume_only.")
        if not yes:
            typer.confirm(f"Restore recovery point '{recovery_point_id}' for {spec.label} '{vm_id}'?", abort=True)
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"restore_{spec.method_fragment}_backup")(
            vm_id,
            workspace_id=workspace,
            recovery_point_id=recovery_point_id,
            **_restore_kwargs(
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
            ),
        )
        print_json(result)

    @backups.command("restore-status")
    @handle_api_errors
    def backups_restore_status(
        ctx: typer.Context,
        restore_id: str = typer.Argument(...),
    ) -> None:
        """Get the current backup restore status."""
        _settings, workspace, _client, resource = _resource(ctx, spec)
        result = getattr(resource, f"get_{spec.method_fragment}_backup_restore")(
            restore_id, workspace_id=workspace
        )
        print_json(result)
