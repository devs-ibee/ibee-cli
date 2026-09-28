"""Standalone Block Storage commands.

Every command calls the Python SDK, which applies the portal's rules before any
request: the volume-name rule (3-255 lowercase letters, numbers and hyphens), sizes
of 10-10000 GB, 24-character hexadecimal volume IDs, grow-only resize, no delete of an
attached volume, and the unmount confirmation on detach. A broken rule exits 2.

Use ``attach-vm`` / ``detach-vm`` (or ``ibee vms|gpus volume-attach``) to attach a
volume to a server, as the portal does. ``attach`` and ``detach`` are advanced
storage-node commands.
"""

from __future__ import annotations

from typing import Any, Optional

import typer
from ibee.core.api_error import ApiError
from ibee.errors import ForbiddenError
from ibee.validation import (
    BLOCK_VOLUME_ATTACH_MODES,
    BLOCK_VOLUME_CLASSES,
    BLOCK_VOLUME_VM_STATES,
    BLOCK_VOLUME_VM_TYPES,
    VOLUME_OPERATION_POLL_INTERVAL_SECONDS,
    VOLUME_OPERATION_TIMEOUT_SECONDS,
    IbeeValidationError,
    build_block_volume_create_body,
    check_volume_deletable,
    resolve_single_attachment,
    validate_block_volume_id,
    validate_node_safe_detach,
    validate_vm_detach_confirmation,
    validate_volume_attach_mode,
    validate_volume_vm_state,
    validate_vm_id,
    validate_volume_vm_type,
    volume_vm_type,
)

from ..context import get_client, get_settings, require_workspace
from ..helpers import (
    api_hints,
    check_state_option,
    confirm_destructive,
    finish_operation,
    idempotency_key_option,
    load_json_input,
    poll_interval_option,
    preflight_billing,
    resolve_idempotency_key,
    resolve_wait,
    timeout_option,
)
from ..render import emit, handle_api_errors, print_json, to_data

app = typer.Typer(
    help=(
        "Standalone persistent Block Storage volumes. Attach them to servers with "
        "'attach-vm' / 'detach-vm'."
    ),
    no_args_is_help=True,
)

ID_FIELD = "id"
LIST_COLUMNS = ("ID", "Name", "Size (GB)", "State", "Site", "VM type", "Attached to")
NODE_LEVEL_NOTE = (
    "Advanced: records a storage-node attachment only and does not attach the disk to a VM. "
    "Use 'ibee block-storage attach-vm' for VMs. Do not manage the same attachment through both "
    "surfaces."
)
FORCE_DELETE_WARNING = (
    "Force delete detaches the volume from all servers and erases all data. Continue?"
)
CREATE_PLAN_HINT = (
    "Pick another --size-gb allowed by the site's plan, or pass --sku-code when the site has "
    "several Block Storage plans."
)
AMBIGUOUS_PLAN_HINT = (
    "The site has several priced Block Storage plans; this is a server-side catalog issue that "
    "--sku-code cannot resolve yet. Contact support."
)
FORCE_DETACH_WARNING = (
    "Force detach skips the unmount safety check and can corrupt data that is still in use. Continue?"
)


def _choice_help(values: tuple[str, ...]) -> str:
    return "|".join(values)


def _session(ctx: typer.Context) -> tuple[Any, str, Any]:
    settings = get_settings(ctx)
    client = get_client(settings)  # token first, as in 0.3.0
    return settings, require_workspace(settings), client


def _get(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def _volume_id(record: Any) -> Any:
    return _get(record, "id") or _get(record, "_id")


def _attached_to(volume: Any) -> str:
    names = []
    for item in _get(volume, "attachments") or []:
        name = _get(item, "vm_name") or _get(item, "vm_id") or _get(item, "node_name")
        if name:
            names.append(str(name))
    return ", ".join(names) or "-"


def _volume_rows(volumes: list[Any]) -> list[tuple[Any, ...]]:
    return [
        (
            _volume_id(volume),
            _get(volume, "name"),
            _get(volume, "size_gb"),
            _get(volume, "state") or _get(volume, "status"),
            _get(volume, "site_name") or _get(volume, "site_id"),
            _get(volume, "vm_type") or "cloud",
            _attached_to(volume),
        )
        for volume in volumes
    ]


def _read_volume(client: Any, workspace: str, volume_id: str) -> Any:
    """The volume, or ``None`` when the token lacks ``block-storage.read`` (the API then decides)."""

    try:
        return client.block_storage.get_block_volume(volume_id, workspace_id=workspace)
    except ForbiddenError:
        return None


def _volume_wait(wait: bool, timeout: Optional[float], poll_interval: Optional[float]):
    """``--wait`` settings with the portal cadence: every 2 s, up to 120 s by default."""

    return resolve_wait(
        wait,
        timeout,
        VOLUME_OPERATION_POLL_INTERVAL_SECONDS if wait and poll_interval is None else poll_interval,
        default_timeout=VOLUME_OPERATION_TIMEOUT_SECONDS,
    )


def _print_vm_action(settings: Any, result: Any, action: str, volume_id: str) -> None:
    """Render the ``{operation, volume}`` result of a waited-for VM attach or detach."""

    data = to_data(result)
    if settings.structured_output:
        print_json(data)
        return
    operation = data.get("operation") if isinstance(data, dict) else None
    op_id = _get(operation, "operation_id") if operation else None
    tail = f" (operation {op_id})" if op_id else ""
    typer.secho(f"{action} completed for volume {volume_id}{tail}.", fg=typer.colors.GREEN)
    volume = data.get("volume") if isinstance(data, dict) else None
    if volume:
        print_json(volume)


@app.command("list")
@handle_api_errors
def list_volumes(
    ctx: typer.Context,
    site_id: Optional[str] = typer.Option(None, "--site-id", help="Only volumes in this site"),
    vm_type: Optional[str] = typer.Option(
        None, "--vm-type", metavar=_choice_help(BLOCK_VOLUME_VM_TYPES), help="Only volumes for cloud or GPU VMs"
    ),
    limit: Optional[int] = typer.Option(None, "--limit", help="Page size, 1-1000 (API default 100)"),
    offset: Optional[int] = typer.Option(None, "--offset", help="Volumes to skip (>= 0)"),
    all_pages: bool = typer.Option(False, "--all", help="Read every page (--limit sets the page size)"),
) -> None:
    """List standalone volumes in the workspace (newest first).

    One page is read (the API returns up to 100 volumes by default); pass --all to read
    every page. -o table shows a summary table.
    """

    settings, workspace, client = _session(ctx)
    if all_pages:
        if offset is not None:
            raise typer.BadParameter("--offset cannot be used with --all.", param_hint="--offset")
        volumes = client.block_storage.list_all_block_volumes(
            workspace_id=workspace, site_id=site_id, vm_type=vm_type, page_size=100 if limit is None else limit
        )
    else:
        volumes = client.block_storage.list_block_volumes(
            workspace_id=workspace, site_id=site_id, vm_type=vm_type, limit=limit, offset=offset
        )
    volumes = list(volumes or [])
    emit(
        volumes,
        settings=settings,
        default="json",
        headers=LIST_COLUMNS,
        rows=_volume_rows(volumes),
        title="Block Storage volumes",
        id_field=ID_FIELD,
    )
    page = 100 if limit is None else limit
    if not all_pages and len(volumes) >= page and not settings.structured_output:
        next_offset = (offset or 0) + len(volumes)
        typer.secho(
            f"More volumes may exist: use --all, or --offset {next_offset}.", fg=typer.colors.YELLOW, err=True
        )


@app.command("create")
@handle_api_errors
def create_volume(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="3-255 lowercase letters, numbers and hyphens; unique in the workspace"),
    size_gb: int = typer.Option(..., "--size-gb", help="Whole GB, 10-10000 (the site's plan may allow only some sizes)"),
    site_id: str = typer.Option(..., "--site-id", help="Placement site ID (see 'ibee compute sites')"),
    site_name: Optional[str] = typer.Option(
        None, "--site-name", help="Site display name (default: read from the compute sites)"
    ),
    sku_code: Optional[str] = typer.Option(
        None, "--sku-code", help="Block Storage SKU when the site has several plans (root-disk SKUs are rejected)"
    ),
    volume_class: str = typer.Option(
        "balanced", "--class", metavar=_choice_help(BLOCK_VOLUME_CLASSES), help="Storage class"
    ),
    replica_count: int = typer.Option(2, "--replicas", help="Replica count, 1-5"),
    backup_enabled: bool = typer.Option(
        True, "--backup-enabled/--no-backup", help="Enable volume backups"
    ),
    vm_type: Optional[str] = typer.Option(
        None,
        "--vm-type",
        metavar=_choice_help(BLOCK_VOLUME_VM_TYPES),
        help=(
            "Kind of VM the volume will attach to (API default cloud). A GPU VM can attach only a "
            "volume created with --vm-type gpu. Not yet part of the published API contract; "
            "behaviour may change."
        ),
    ),
    delete_on_termination: Optional[bool] = typer.Option(
        None,
        "--delete-on-termination/--keep-on-termination",
        help=(
            "Delete the volume when its VM is deleted (API default: keep). Not yet part of the "
            "published API contract; behaviour may change."
        ),
    ),
    check_site: bool = typer.Option(
        True,
        "--check-site/--no-check-site",
        help="Read the compute sites to fill --site-name and reject an unknown --site-id (default: on)",
    ),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Create a volume; the plan and billing are resolved by the server.

    Creation is synchronous: the result carries the ready volume and its operation.
    """

    settings, workspace, client = _session(ctx)
    key = resolve_idempotency_key(idempotency_key, "block-create", name)
    # Validate first (exit 2) so a bad request never reaches the billing preflight.
    body = build_block_volume_create_body(
        name=name,
        size_gb=size_gb,
        site_id=site_id,
        site_name=site_name,
        sku_code=sku_code,
        volume_class=volume_class,
        replica_count=replica_count,
        backup_enabled=backup_enabled,
        vm_type=vm_type,
        delete_on_termination=delete_on_termination,
    )
    if settings.check_billing:
        preflight_billing(
            settings, client, workspace, sku_code=body.get("sku_code"), resource_type="block_storage"
        )
    try:
        with api_hints({400: CREATE_PLAN_HINT}):
            result = client.block_storage.create_block_volume(
                workspace_id=workspace,
                name=name,
                size_gb=size_gb,
                site_id=site_id,
                site_name=site_name,
                sku_code=sku_code,
                volume_class=volume_class,
                replica_count=replica_count,
                backup_enabled=backup_enabled,
                vm_type=vm_type,
                delete_on_termination=delete_on_termination,
                idempotency_key=key,
                resolve_site_name=check_site,
            )
    except ApiError as exc:
        if getattr(exc, "code", None) == "ambiguous_block_storage_plan":
            exc.cli_hint = AMBIGUOUS_PLAN_HINT  # type: ignore[attr-defined]
        raise
    volume = _get(result, "volume") or {}
    if settings.output == "id":
        typer.echo(str(_volume_id(volume) or ""))
        return
    print_json(result)
    if not settings.structured_output and volume:
        typer.secho(
            f"Volume {_volume_id(volume)} is {_get(volume, 'state') or 'created'}.",
            fg=typer.colors.GREEN,
            err=True,
        )


@app.command("get")
@handle_api_errors
def get_volume(ctx: typer.Context, volume_id: str = typer.Argument(..., help="Volume ID (24 hex characters)")) -> None:
    """Show a volume, including its attachments, VM type and billing catalog."""

    _settings, workspace, client = _session(ctx)
    print_json(client.block_storage.get_block_volume(volume_id, workspace_id=workspace), id_field=ID_FIELD)


@app.command("operations")
@handle_api_errors
def list_operations(
    ctx: typer.Context,
    volume_id: str = typer.Argument(..., help="Volume ID (24 hex characters)"),
    limit: Optional[int] = typer.Option(None, "--limit", help="1-200 (API default 20)"),
) -> None:
    """List a volume's operations, newest first."""

    _settings, workspace, client = _session(ctx)
    operations = client.block_storage.list_block_volume_operations(volume_id, workspace_id=workspace, limit=limit)
    for item in operations or []:
        if isinstance(item, dict):
            item.pop("debug_reason", None)
    print_json(operations, id_field=ID_FIELD)


@app.command("attach-vm")
@handle_api_errors
def attach_to_vm(
    ctx: typer.Context,
    volume_id: str = typer.Argument(..., help="Volume ID (24 hex characters)"),
    vm_id: str = typer.Argument(..., help="Cloud or GPU VM ID"),
    mode: Optional[str] = typer.Option(
        None, "--mode", metavar=_choice_help(BLOCK_VOLUME_ATTACH_MODES), help="Default single-writer (as the portal)"
    ),
    vm_type: Optional[str] = typer.Option(
        None,
        "--vm-type",
        metavar=_choice_help(BLOCK_VOLUME_VM_TYPES),
        help="Must match the volume (default: the volume's VM type, which also picks the cloud or GPU endpoint)",
    ),
    billing_catalog: Optional[str] = typer.Option(
        None,
        "--billing-catalog",
        metavar="JSON",
        help=(
            "Block Storage billing catalog (sku_id and sku_code). Only needed when the token cannot "
            "read the volume; by default it is read from the volume (pass --vm-type too)."
        ),
    ),
    billing_catalog_file: Optional[str] = typer.Option(
        None, "--billing-catalog-file", metavar="PATH", help="Read --billing-catalog from a JSON file"
    ),
    requested_by: Optional[str] = typer.Option(None, "--requested-by", help="Audit label (API default 'api')"),
    wait: bool = typer.Option(False, "--wait", help="Wait for the attach to finish (every 2 s, up to 120 s)"),
    timeout: Optional[float] = timeout_option(VOLUME_OPERATION_TIMEOUT_SECONDS),
    poll_interval: Optional[float] = poll_interval_option(),
    check_state: bool = check_state_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Attach a volume to a cloud or GPU VM, the way the portal does.

    The volume is read first: it must be unattached and idle, created for the VM's type,
    and in the VM's site. Its Block Storage SKU is sent as the billing catalog.
    """

    settings, workspace, client = _session(ctx)
    volume_id = validate_block_volume_id(volume_id)
    validate_volume_attach_mode(mode)
    validate_volume_vm_type(vm_type)
    config = _volume_wait(wait, timeout, poll_interval)
    catalog = load_json_input(billing_catalog, billing_catalog_file, "billing-catalog")
    key = resolve_idempotency_key(idempotency_key, "attach-volume", volume_id)
    kwargs: dict[str, Any] = {
        "workspace_id": workspace,
        "vm_type": vm_type,
        "mode": mode,
        "billing_catalog": catalog,
        "requested_by": requested_by,
        "idempotency_key": key,
        "check_state": check_state,
    }
    if config is not None:
        kwargs.update(wait=True, timeout=config.timeout, poll_interval=config.poll_interval)
    result = client.block_storage.attach_block_volume_to_vm(volume_id, vm_id, **kwargs)
    if config is not None:
        _print_vm_action(settings, result, "Volume attach", volume_id)
        return
    finish_operation(
        settings, client, workspace, result, "Volume attach", f"volume {volume_id}", False, idempotency_key=key
    )


@app.command("detach-vm")
@handle_api_errors
def detach_from_vm(
    ctx: typer.Context,
    volume_id: str = typer.Argument(..., help="Volume ID (24 hex characters)"),
    vm_id: Optional[str] = typer.Argument(None, help="VM ID (default: the VM the volume is attached to)"),
    confirm_unmounted: bool = typer.Option(
        False,
        "--confirm-unmounted",
        help="I have unmounted this volume from the server before detaching (required unless --force)",
    ),
    force: bool = typer.Option(False, "--force", help="Detach without the unmount safety check (asks again)"),
    vm_type: Optional[str] = typer.Option(
        None, "--vm-type", metavar=_choice_help(BLOCK_VOLUME_VM_TYPES), help="Default: from the volume"
    ),
    requested_by: Optional[str] = typer.Option(None, "--requested-by", help="Audit label (API default 'api')"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    wait: bool = typer.Option(False, "--wait", help="Wait for the detach to finish (every 2 s, up to 120 s)"),
    timeout: Optional[float] = timeout_option(VOLUME_OPERATION_TIMEOUT_SECONDS),
    poll_interval: Optional[float] = poll_interval_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Detach a volume from its cloud or GPU VM, the way the portal does.

    Unmount the volume inside the server first and pass --confirm-unmounted (the portal's
    mandatory tick), or pass --force.
    """

    settings, workspace, client = _session(ctx)
    volume_id = validate_block_volume_id(volume_id)
    validate_vm_detach_confirmation(confirm_unmounted, force)
    validate_volume_vm_type(vm_type)
    config = _volume_wait(wait, timeout, poll_interval)
    key = resolve_idempotency_key(idempotency_key, "detach-volume", volume_id)
    target = f"VM {vm_id}" if vm_id else "its server"
    # Resolve the attachment before asking, so the prompt names the VM and a volume that
    # cannot be detached fails (exit 2) without a prompt.
    if vm_id is not None:
        vm_id = validate_vm_id(vm_id)
    volume = _read_volume(client, workspace, volume_id)
    if volume is not None:
        attachment = resolve_single_attachment(volume, vm_id=vm_id, for_vm=True)
        vm_id = str(_get(attachment, "vm_id")).strip()
        vm_name = _get(attachment, "vm_name")
        target = f"VM '{vm_name}' ({vm_id})" if vm_name else f"VM {vm_id}"
    elif vm_id is None:
        raise IbeeValidationError(
            "Reading the volume needs block-storage.read; grant it or pass the VM ID and --vm-type.",
            code="volume_unreadable",
            field="vm_id",
        )
    confirm_destructive(settings, f"Detach volume '{volume_id}' from {target}?", yes)
    if force:
        confirm_destructive(settings, FORCE_DETACH_WARNING, yes)
    kwargs: dict[str, Any] = {
        "workspace_id": workspace,
        "vm_type": vm_type,
        "confirm_unmounted": confirm_unmounted,
        "force": force,
        "requested_by": requested_by,
        "idempotency_key": key,
    }
    if config is not None:
        kwargs.update(wait=True, timeout=config.timeout, poll_interval=config.poll_interval)
    result = client.block_storage.detach_block_volume_from_vm(volume_id, vm_id, **kwargs)
    if config is not None:
        _print_vm_action(settings, result, "Volume detach", volume_id)
        return
    finish_operation(
        settings, client, workspace, result, "Volume detach", f"volume {volume_id}", False, idempotency_key=key
    )


@app.command("attach", help=f"Attach a volume to a storage node. {NODE_LEVEL_NOTE}")
@handle_api_errors
def attach_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(..., help="Volume ID (24 hex characters)"),
    node_name: str = typer.Option(..., "--node-name", help="Storage node name"),
    mode: str = typer.Option("single-writer", "--mode", metavar=_choice_help(BLOCK_VOLUME_ATTACH_MODES)),
    vm_id: Optional[str] = typer.Option(None, "--vm-id"),
    vm_name: Optional[str] = typer.Option(None, "--vm-name"),
    vm_state: Optional[str] = typer.Option(None, "--vm-state", metavar=_choice_help(BLOCK_VOLUME_VM_STATES)),
    vm_site_id: Optional[str] = typer.Option(
        None, "--vm-site-id", help="The VM's site; a site different from the volume's is refused"
    ),
    vm_type: str = typer.Option("cloud", "--vm-type", metavar=_choice_help(BLOCK_VOLUME_VM_TYPES)),
    check_state: bool = check_state_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    _settings, workspace, client = _session(ctx)
    key = resolve_idempotency_key(idempotency_key, "block-attach", volume_id)
    result = client.block_storage.attach_block_volume(
        volume_id,
        workspace_id=workspace,
        node_name=node_name,
        mode=mode,
        vm_id=vm_id,
        vm_name=vm_name,
        vm_state=vm_state,
        vm_site_id=vm_site_id,
        vm_type=vm_type,
        idempotency_key=key,
        check_state=check_state,
    )
    print_json(result)


@app.command(
    "detach",
    help=(
        "Detach a volume from a storage node (needs --confirm-unmounted, --force or --vm-state "
        f"stopped|suspended; --node-name defaults to the volume's only attachment). {NODE_LEVEL_NOTE}"
    ),
)
@handle_api_errors
def detach_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(..., help="Volume ID (24 hex characters)"),
    node_name: Optional[str] = typer.Option(
        None, "--node-name", help="Storage node (default: the volume's only attachment)"
    ),
    force: bool = typer.Option(False, "--force", help="Detach without the safety check (asks again)"),
    confirm_unmounted: bool = typer.Option(
        False, "--confirm-unmounted", help="I have unmounted this volume from the server"
    ),
    vm_state: Optional[str] = typer.Option(None, "--vm-state", metavar=_choice_help(BLOCK_VOLUME_VM_STATES)),
    vm_type: Optional[str] = typer.Option(
        None, "--vm-type", metavar=_choice_help(BLOCK_VOLUME_VM_TYPES), help="Default: the volume's VM type"
    ),
    reason: Optional[str] = typer.Option(None, "--reason"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    settings, workspace, client = _session(ctx)
    validate_block_volume_id(volume_id)
    state = validate_volume_vm_state(vm_state)
    validate_node_safe_detach(force, confirm_unmounted, state)
    validate_volume_vm_type(vm_type)
    key = resolve_idempotency_key(idempotency_key, "block-detach", volume_id)
    target = ""
    if node_name is None:
        # Find the only attachment before asking (the SDK would do it after the prompt).
        volume = client.block_storage.get_block_volume(volume_id, workspace_id=workspace)
        attachment = resolve_single_attachment(volume)
        node_name = str(_get(attachment, "node_name") or "").strip()
        if not node_name:
            raise IbeeValidationError(
                "The attachment has no node name; pass --node-name.", code="invalid_node_name", field="node_name"
            )
        vm_type = vm_type or volume_vm_type(volume)
        target = f" from node '{node_name}'"
    confirm_destructive(settings, f"Detach Block Storage volume '{volume_id}'{target}?", yes)
    if force:
        confirm_destructive(settings, FORCE_DETACH_WARNING, yes)
    result = client.block_storage.detach_block_volume(
        volume_id,
        workspace_id=workspace,
        node_name=node_name,
        force=force,
        confirm_unmounted=confirm_unmounted,
        vm_state=state,
        vm_type=vm_type,
        reason=reason,
        idempotency_key=key,
    )
    print_json(result)


@app.command("resize")
@handle_api_errors
def resize_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(..., help="Volume ID (24 hex characters)"),
    new_size_gb: int = typer.Option(..., "--new-size-gb", help="New size in whole GB, up to 10000 (grow only)"),
    allow_online: bool = typer.Option(
        False, "--allow-online", help="Resize while attached to a running server"
    ),
    vm_state: Optional[str] = typer.Option(
        None,
        "--vm-state",
        metavar=_choice_help(BLOCK_VOLUME_VM_STATES),
        help="State of the attached server; an attached volume needs stopped/suspended or --allow-online",
    ),
    check_state: bool = check_state_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Grow a volume. Shrinking is not supported; the same size is a no-op.

    After growing, extend the filesystem inside the server.
    """

    settings, workspace, client = _session(ctx)
    key = resolve_idempotency_key(idempotency_key, "block-resize", volume_id)
    result = client.block_storage.resize_block_volume(
        volume_id,
        workspace_id=workspace,
        new_size_gb=new_size_gb,
        vm_state=vm_state,
        allow_online=allow_online,
        idempotency_key=key,
        check_state=check_state,
    )
    print_json(result)
    if not settings.structured_output:
        typer.secho(
            "Extend the filesystem inside the server to use the new space.", fg=typer.colors.YELLOW, err=True
        )


@app.command("delete")
@handle_api_errors
def delete_volume(
    ctx: typer.Context,
    volume_id: str = typer.Argument(..., help="Volume ID (24 hex characters)"),
    force: bool = typer.Option(
        False, "--force", help="Detach from every server and erase all data (asks again unless --yes)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    check_state: bool = check_state_option(),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Delete a volume.

    Like the portal, an attached volume is refused ("Detach this volume from all servers
    before deleting.") and so is one in the middle of another operation; --force skips
    these checks. The idempotency key is sent as a query parameter, which is not yet part
    of the published API contract; behaviour may change.
    """

    settings, workspace, client = _session(ctx)
    volume_id = validate_block_volume_id(volume_id)
    key = resolve_idempotency_key(idempotency_key, "block-delete", volume_id)
    if check_state and not force:
        volume = _read_volume(client, workspace, volume_id)
        if volume is not None:
            check_volume_deletable(volume)
    confirm_destructive(settings, f"Delete Block Storage volume '{volume_id}'?", yes)
    if force:
        confirm_destructive(settings, FORCE_DELETE_WARNING, yes)
    result = client.block_storage.delete_block_volume(
        volume_id, workspace_id=workspace, force=force, idempotency_key=key, check_state=False
    )
    if result is not None:
        print_json(result)
    else:
        typer.secho(f"Volume '{volume_id}' deleted.", fg=typer.colors.GREEN)
