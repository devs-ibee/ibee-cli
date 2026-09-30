"""Secret Store commands for stores, secrets, application identities, and scopes.

Every command runs the Python SDK's Secret Store rules (the portal's conditions)
before it sends anything: a broken rule exits 2. Commands that ask for confirmation
check their inputs first, so a bad request never reaches the prompt.
"""

from __future__ import annotations

import json
import sys
import warnings
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import typer
from ibee.core.api_error import ApiError
from ibee.errors import ConflictError, InsufficientScopeError
from ibee.validation import IbeeValidationError
from ibee.validation.secret_store import (
    SECRET_BATCH_MAX_ITEMS,
    SECRET_STORE_MAX_BODY_BYTES,
    SECRET_STORE_PAGE_LIMIT_MAX,
    build_identity_create_body,
    build_identity_update_body,
    build_scope_create_body,
    build_scope_update_body,
    build_store_create_body,
    build_store_update_body,
    chunk_batch_secrets,
    find_store_by_name,
    normalize_secret_name,
    normalize_secret_search_query,
    normalize_secret_value,
    secret_version_state,
    validate_cas,
    validate_secret_store_resource_id,
    validate_secret_store_workspace_id,
    validate_secret_versions,
)

from ..context import fail_usage, get_client, get_settings, require_workspace
from ..helpers import check_state_option, confirm_destructive, parse_value, sdk_warnings
from ..render import cell, emit, handle_api_errors, print_json, print_table, to_data

app = typer.Typer(
    help="Secret Store: stores, secrets, and application identities",
    no_args_is_help=True,
)

# ── stores sub-group: `ibee secrets stores <verb>` ──────────────────────────
stores_app = typer.Typer(help="Manage secret stores", no_args_is_help=True)
app.add_typer(stores_app, name="stores")

# Application identities authenticate workloads directly to Secret Store. Scope
# management stays nested under identities so it cannot be confused with API
# token scopes: `ibee secrets identities scopes <verb>`.
identities_app = typer.Typer(
    help="Manage AppRole and Kubernetes application identities",
    no_args_is_help=True,
)
identity_scopes_app = typer.Typer(
    help="Manage store access granted to application identities",
    no_args_is_help=True,
)
app.add_typer(identities_app, name="identities")
identities_app.add_typer(identity_scopes_app, name="scopes")

#: Page size the portal uses for store and secret lists.
PORTAL_PAGE_LIMIT = 100


class AuthMethod(str, Enum):
    APPROLE = "approle"
    KUBERNETES = "kubernetes"


class AccessMode(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class IfExists(str, Enum):
    ERROR = "error"
    REUSE = "reuse"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_sensitive_output(show_sensitive: bool) -> None:
    """Require an explicit opt-in before printing generated credentials."""
    if not show_sensitive:
        raise typer.BadParameter(
            "This command can generate and print credentials. "
            "Pass --show-sensitive only when the terminal and output handling are secure."
        )


def _workspace(settings: Any) -> str:
    """The workspace id, checked against Secret Store's 2-128 digit rule."""
    return validate_secret_store_workspace_id(require_workspace(settings))


def _rid(value: str, field: str) -> str:
    return validate_secret_store_resource_id(value, field=field)


def _status(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):  # pragma: no cover - closed or replaced stdin
        return False


def _confirm_if_interactive(settings: Any, prompt: str, yes: bool) -> None:
    """Ask only on a terminal without --yes, so 0.3.0 scripts keep working unattended."""
    if yes or getattr(settings, "assume_yes", False) or not _stdin_is_tty():
        return
    typer.confirm(prompt, abort=True)


def _note(message: str) -> None:
    typer.secho(message, fg=typer.colors.YELLOW, err=True)


def _optional_read(step: str, read: Any) -> Any:
    """Run a read-only pre-step; skip it with a warning when the token lacks the read scope."""
    try:
        return read()
    except InsufficientScopeError as exc:
        scope = getattr(exc, "required_scope", None) or "secret-store.read"
        _note(f"Warning: {step} skipped: the API token lacks the '{scope}' scope. The API still enforces the rule.")
        return None


def _parse_versions(raw: str) -> list[int]:
    """Parse a comma-separated version list, then apply the SDK rule (1-100 versions >= 1, de-duplicated)."""
    try:
        versions = [int(part.strip()) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise typer.BadParameter("versions must be comma-separated integers") from exc
    try:
        return validate_secret_versions(versions)
    except IbeeValidationError as exc:
        raise typer.BadParameter(exc.message) from exc


def _load_batch(path: Path) -> list[Any]:
    """Read a batch-ingest file: an array, or an object with a "secrets" array."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"batch file is not valid JSON: {exc}") from exc
    secrets = payload.get("secrets") if isinstance(payload, dict) else payload
    if not isinstance(secrets, list) or not secrets:
        raise typer.BadParameter("batch file must contain at least one secret")
    return secrets


def _normalize_batch(items: list[Any]) -> tuple[list[dict], list[str]]:
    """Every item checked like `secrets create`; returns the items and duplicate names."""
    normalized: list[dict] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise IbeeValidationError(
                f"secrets[{index}] must be an object with secret_name and value.", field=f"secrets[{index}]"
            )
        try:
            entry = {
                "secret_name": normalize_secret_name(item.get("secret_name")),
                "value": normalize_secret_value(item.get("value")),
            }
        except IbeeValidationError as exc:
            raise IbeeValidationError(
                f"secrets[{index}]: {exc.message}", code=exc.code, field=f"secrets[{index}]", details=exc.details
            ) from exc
        if entry["secret_name"] in seen and entry["secret_name"] not in duplicates:
            duplicates.append(entry["secret_name"])
        seen.add(entry["secret_name"])
        normalized.append(entry)
    return normalized, duplicates


def _page_hint(kind: str, shown: int, total: Any, page: int, limit: int = PORTAL_PAGE_LIMIT) -> None:
    """Point at the next page only when rows follow this one."""
    if isinstance(total, int) and (page - 1) * limit + shown < total:
        _note(
            f"Showing {shown} of {total} {kind} (page {page}); use --page {page + 1} or --all for the rest."
        )


def _check_list_options(page: Optional[int], all_pages: bool) -> None:
    if all_pages and page is not None:
        fail_usage("--page cannot be combined with --all.")


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


@stores_app.command("list")
@handle_api_errors
def list_stores(
    ctx: typer.Context,
    include_archived: bool = typer.Option(
        True,
        "--include-archived/--active-only",
        help="Include archived stores, as the portal does (default), or list active stores only",
    ),
    page: Optional[int] = typer.Option(None, "--page", min=1, help="Page number (default 1)"),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        min=1,
        max=SECRET_STORE_PAGE_LIMIT_MAX,
        help=f"Stores per page, 1-{SECRET_STORE_PAGE_LIMIT_MAX} (default {PORTAL_PAGE_LIMIT})",
    ),
    all_pages: bool = typer.Option(False, "--all", help="Fetch every page"),
) -> None:
    """List secret stores in the workspace, including archived ones by default."""
    _check_list_options(page, all_pages)
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    client = get_client(settings)
    if all_pages:
        stores = client.secret_store.list_all_secret_stores(
            workspace_id=workspace, include_archived=include_archived, page_size=limit
        )
        result: Any = stores
        total: Any = len(stores)
    else:
        result = client.secret_store.list_secret_stores(
            workspace_id=workspace,
            include_archived=include_archived,
            page=page or 1,
            limit=limit or PORTAL_PAGE_LIMIT,
        )
        stores = getattr(result, "stores", None) or []
        total = getattr(result, "total", None)
    if settings.structured_output:
        print_json(result)
        return
    print_table(
        "Secret stores",
        ["ID", "Name", "Store key", "Status", "Updated"],
        [
            (
                cell(s, "id"),
                cell(s, "name"),
                cell(s, "store_key"),
                cell(s, "status"),
                str(cell(s, "updated_at") or "")[:19],
            )
            for s in stores
        ],
    )
    if not all_pages:
        _page_hint("stores", len(stores), total, page or 1, limit or PORTAL_PAGE_LIMIT)


@stores_app.command("create")
@handle_api_errors
def create_store(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Store name (1-128 characters, with at least one letter or digit)"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Store description"),
    billing_check: bool = typer.Option(
        True,
        "--billing-check/--no-billing-check",
        help="Deprecated no-op; upstream decides billing and lifecycle admission.",
    ),
    if_exists: IfExists = typer.Option(
        IfExists.ERROR,
        "--if-exists",
        help="What to do when a store with this name already exists: error (default) or reuse it",
    ),
) -> None:
    """Create a secret store."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    body = build_store_create_body(name, description)
    client = get_client(settings)
    reused = False
    with sdk_warnings():
        try:
            result = client.secret_store.create_secret_store(
                workspace_id=workspace,
                name=body["name"],
                description=body.get("description"),
                preflight_billing=False,
            )
        except ConflictError as exc:
            if if_exists is not IfExists.REUSE or type(exc) is not ConflictError:
                raise
            stores = client.secret_store.list_all_secret_stores(workspace_id=workspace, include_archived=True)
            existing = find_store_by_name(stores, body["name"])
            if existing is None:
                raise
            result, reused = existing, True
    if reused:
        _note(f"Store already exists: '{cell(result, 'name')}' (id {cell(result, 'id')}); reusing it.")
        if _status(cell(result, "status")) != "active":
            _note(
                f"The store is {cell(result, 'status')}; restore it with "
                f"`ibee secrets stores unarchive {cell(result, 'id')}` before adding secrets."
            )
    if settings.structured_output:
        print_json(result)
        return
    if not reused:
        typer.secho(
            f"Store '{body['name']}' created (id {getattr(result, 'id', '?')}).", fg=typer.colors.GREEN
        )


@stores_app.command("get")
@handle_api_errors
def get_store(ctx: typer.Context, store_id: str = typer.Argument(..., help="Store ID")) -> None:
    """Show one secret store."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    client = get_client(settings)
    result = client.secret_store.get_secret_store(_rid(store_id, "store_id"), workspace_id=workspace)
    print_json(result)


@stores_app.command("update")
@handle_api_errors
def update_store(
    ctx: typer.Context,
    store_id: str = typer.Argument(..., help="Store ID"),
    name: Optional[str] = typer.Option(None, "--name", help="New name (the store key does not change)"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="New description"),
) -> None:
    """Update a secret store's name or description (the store must be active)."""
    if name is None and description is None:
        raise typer.BadParameter("Provide --name and/or --description to update.")
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    store = _rid(store_id, "store_id")
    body = build_store_update_body(name, description)
    client = get_client(settings)
    result = client.secret_store.update_secret_store(store, workspace_id=workspace, **body)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Store '{store}' updated.", fg=typer.colors.GREEN)


def _read_store(client: Any, workspace: str, store_id: str, check_state: bool) -> Any:
    if not check_state:
        return None
    return _optional_read(
        "Store state check",
        lambda: client.secret_store.get_secret_store(store_id, workspace_id=workspace),
    )


@stores_app.command("archive")
@handle_api_errors
def archive_store(
    ctx: typer.Context,
    store_id: str = typer.Argument(..., help="Store ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    check_state: bool = check_state_option(),
) -> None:
    """Archive a secret store: access is blocked and sessions are revoked until you restore it."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    store_ref = _rid(store_id, "store_id")
    client = get_client(settings)
    store = _read_store(client, workspace, store_ref, check_state)
    status = _status(cell(store, "status")) if store is not None else ""
    label = (cell(store, "name") if store is not None else None) or store_ref
    if status == "deleting":
        raise IbeeValidationError(
            f"Store '{label}' is being deleted and cannot be archived.", code="store_deleting", field="store_id"
        )
    if status == "archived":
        _note(f"Store '{label}' is already archived.")
        if settings.structured_output:
            print_json(store)
        return
    confirm_destructive(
        settings, f"Archive store? Archiving {label} will block access until you restore it.", yes
    )
    result = client.secret_store.archive_secret_store(store_ref, workspace_id=workspace)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Store '{label}' archived.", fg=typer.colors.GREEN)


@stores_app.command("unarchive")
@handle_api_errors
def unarchive_store(
    ctx: typer.Context,
    store_id: str = typer.Argument(..., help="Store ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation (asked only on a terminal)"),
    check_state: bool = check_state_option(),
) -> None:
    """Restore (reactivate) an archived secret store."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    store_ref = _rid(store_id, "store_id")
    client = get_client(settings)
    store = _read_store(client, workspace, store_ref, check_state)
    status = _status(cell(store, "status")) if store is not None else ""
    label = (cell(store, "name") if store is not None else None) or store_ref
    if status == "deleting":
        raise IbeeValidationError(
            f"Store '{label}' is being deleted and cannot be restored.", code="store_deleting", field="store_id"
        )
    if status == "active":
        _note(f"Store '{label}' is already active.")
        if settings.structured_output:
            print_json(store)
        return
    _confirm_if_interactive(settings, f"Restore store? {label} will be available again.", yes)
    result = client.secret_store.unarchive_secret_store(store_ref, workspace_id=workspace)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Store '{label}' unarchived.", fg=typer.colors.GREEN)


@stores_app.command("delete-permanent")
@handle_api_errors
def permanently_delete_store(
    ctx: typer.Context,
    store_id: str = typer.Argument(..., help="Secret store ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm irreversible deletion"),
    check_state: bool = check_state_option(),
) -> None:
    """Permanently delete a store, its secrets and its access entries.

    If the API reports the deletion as incomplete (503), the store stays in
    'deleting'; run the same command again to finish it.
    """
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    store_ref = _rid(store_id, "store_id")
    client = get_client(settings)
    store = _read_store(client, workspace, store_ref, check_state)
    label = (cell(store, "name") if store is not None else None) or store_ref
    confirm_destructive(
        settings,
        f"Delete store? This removes {label}, its secrets, and its access entries. "
        "This action cannot be undone.",
        yes,
    )
    result = client.secret_store.permanently_delete_secret_store(store_ref, workspace_id=workspace)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Store '{label}' permanently deleted.", fg=typer.colors.GREEN)


# ── application identities: `ibee secrets identities <verb>` ───────────────

@identities_app.command("list")
@handle_api_errors
def list_identities(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store ID"),
) -> None:
    """List application identities bound to a store."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    client = get_client(settings)
    result = client.secret_store.list_secret_identities(_rid(store_id, "store_id"), workspace_id=workspace)
    if settings.structured_output:
        print_json(result)
        return
    identities = getattr(result, "identities", None) or []
    print_table(
        "Secret Store identities",
        ["ID", "Name", "Auth method", "Policy", "Status", "Updated"],
        [
            (
                cell(identity, "id"),
                cell(identity, "name"),
                cell(identity, "auth_method"),
                cell(identity, "token_policy_mode"),
                cell(identity, "status"),
                str(cell(identity, "updated_at") or "")[:19],
            )
            for identity in identities
        ],
    )


@identities_app.command("create")
@handle_api_errors
def create_identity(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Initial secret store ID (must be active)"),
    name: str = typer.Option(
        ..., "--name", "-n", help="Application identity name (1-128 characters, unique in the workspace; cannot be changed)"
    ),
    auth_method: AuthMethod = typer.Option(..., "--auth-method", help="approle or kubernetes"),
    token_policy_mode: AccessMode = typer.Option(
        AccessMode.READ_ONLY,
        "--token-policy-mode",
        help="Default read_only or read_write access policy (always sent)",
    ),
    k8s_namespace: Optional[str] = typer.Option(
        None,
        "--k8s-namespace",
        help="Required for kubernetes identities",
    ),
    k8s_service_account: Optional[str] = typer.Option(
        None,
        "--k8s-service-account",
        help="Required for kubernetes identities",
    ),
) -> None:
    """Create an AppRole or Kubernetes application identity.

    Credentials are not returned; fetch them with `ibee secrets identities access`.
    """
    namespace = (k8s_namespace or "").strip()
    account = (k8s_service_account or "").strip()
    if auth_method is AuthMethod.KUBERNETES:
        if not namespace or not account:
            raise typer.BadParameter(
                "Kubernetes identities require --k8s-namespace and --k8s-service-account."
            )
    elif k8s_namespace is not None or k8s_service_account is not None:
        raise typer.BadParameter(
            "--k8s-namespace and --k8s-service-account apply only to kubernetes identities."
        )

    settings = get_settings(ctx)
    workspace = _workspace(settings)
    store = _rid(store_id, "store_id")
    body = build_identity_create_body(
        auth_method.value, name, token_policy_mode.value, namespace or None, account or None
    )
    client = get_client(settings)
    result = client.secret_store.create_secret_identity(store, workspace_id=workspace, **body)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(
        f"Identity '{body['name']}' created (id {getattr(result, 'id', '?')}). "
        "Use `ibee secrets identities access ID --show-sensitive` to obtain access details.",
        fg=typer.colors.GREEN,
    )


@identities_app.command("get")
@handle_api_errors
def get_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
) -> None:
    """Show application identity metadata without credentials."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    client = get_client(settings)
    result = client.secret_store.get_secret_identity(_rid(identity_id, "identity_id"), workspace_id=workspace)
    print_json(result)


@identities_app.command("update")
@handle_api_errors
def update_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    token_policy_mode: AccessMode = typer.Option(
        ...,
        "--token-policy-mode",
        help="Set read_only or read_write and revoke active sessions",
    ),
) -> None:
    """Update an identity's token policy mode (the name cannot be changed).

    Every scope of the identity is rewritten (read_only clears rollback and destroy)
    and its active sessions are revoked.
    """
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    identity = _rid(identity_id, "identity_id")
    body = build_identity_update_body(token_policy_mode.value)
    client = get_client(settings)
    _note(
        "Warning: changing the token policy mode rewrites every scope of this identity "
        "(read_only clears rollback and destroy) and revokes its active sessions."
    )
    result = client.secret_store.update_secret_identity(identity, workspace_id=workspace, **body)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Identity '{identity}' updated.", fg=typer.colors.GREEN)


@identities_app.command("disable")
@handle_api_errors
def disable_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation (asked only on a terminal)"),
) -> None:
    """Disable an identity and revoke its active sessions."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    identity = _rid(identity_id, "identity_id")
    _confirm_if_interactive(settings, f"Disable access? {identity} is disabled until it is enabled again.", yes)
    client = get_client(settings)
    result = client.secret_store.disable_secret_identity(identity, workspace_id=workspace)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Identity '{identity}' disabled.", fg=typer.colors.GREEN)


@identities_app.command("enable")
@handle_api_errors
def enable_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation (asked only on a terminal)"),
) -> None:
    """Re-enable a disabled identity so it can authenticate again."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    identity = _rid(identity_id, "identity_id")
    _confirm_if_interactive(settings, f"Enable access? {identity} can authenticate again.", yes)
    client = get_client(settings)
    result = client.secret_store.enable_secret_identity(identity, workspace_id=workspace)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Identity '{identity}' enabled.", fg=typer.colors.GREEN)


@identities_app.command("access")
@handle_api_errors
def get_identity_access(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    show_sensitive: bool = typer.Option(
        False,
        "--show-sensitive",
        help="Acknowledge that generated credentials will be printed",
    ),
) -> None:
    """Print workload login details, which may contain credentials.

    For an AppRole identity every call issues a new secret ID that is not revoked,
    so this command is never retried automatically. The identity must be active.
    """
    _require_sensitive_output(show_sensitive)
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    client = get_client(settings)
    result = client.secret_store.get_secret_identity_access(
        _rid(identity_id, "identity_id"), workspace_id=workspace
    )
    print_json(result)


@identities_app.command("rotate-secret-id")
@handle_api_errors
def rotate_identity_secret_id(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="AppRole application identity ID"),
    show_sensitive: bool = typer.Option(
        False,
        "--show-sensitive",
        help="Acknowledge that fresh AppRole credentials will be printed",
    ),
    check_state: bool = check_state_option(),
) -> None:
    """Generate fresh AppRole credentials and print them once.

    Only active AppRole identities can rotate (checked first unless --no-check-state).
    The previous secret ID is not revoked, and the call is never retried automatically.
    """
    _require_sensitive_output(show_sensitive)
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    client = get_client(settings)
    with sdk_warnings():
        result = client.secret_store.rotate_secret_identity_secret_id(
            _rid(identity_id, "identity_id"),
            workspace_id=workspace,
            check_auth_method=check_state,
        )
    print_json(result)


@identities_app.command("revoke-sessions")
@handle_api_errors
def revoke_identity_sessions(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Revoke active sessions without deleting or disabling the identity."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    identity = _rid(identity_id, "identity_id")
    confirm_destructive(settings, f"Revoke all active sessions for identity '{identity}'?", yes)
    client = get_client(settings)
    result = client.secret_store.revoke_secret_identity_sessions(identity, workspace_id=workspace)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Identity '{identity}' sessions revoked.", fg=typer.colors.GREEN)


@identities_app.command("delete")
@handle_api_errors
def delete_identity(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm permanent deletion"),
) -> None:
    """Permanently delete an identity, its scopes, policy, role, and sessions."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    identity = _rid(identity_id, "identity_id")
    confirm_destructive(
        settings,
        f"Delete access? This deletes {identity} and removes its permissions and login details. "
        "This cannot be undone.",
        yes,
    )
    client = get_client(settings)
    result = client.secret_store.delete_secret_identity(identity, workspace_id=workspace)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Identity '{identity}' permanently deleted.", fg=typer.colors.GREEN)


# ── identity scopes: `ibee secrets identities scopes <verb>` ────────────────

@identity_scopes_app.command("list")
@handle_api_errors
def list_identity_scopes(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
) -> None:
    """List store access granted to an application identity."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    client = get_client(settings)
    result = client.secret_store.list_secret_identity_scopes(
        _rid(identity_id, "identity_id"), workspace_id=workspace
    )
    if settings.structured_output:
        print_json(result)
        return
    scopes = getattr(result, "scopes", None) or []
    print_table(
        "Identity scopes",
        ["ID", "Store", "Access", "Version read", "Rollback", "Destroy"],
        [
            (
                cell(scope, "id"),
                cell(scope, "store_id"),
                cell(scope, "access_mode"),
                cell(scope, "allow_version_read"),
                cell(scope, "allow_rollback"),
                cell(scope, "allow_destroy"),
            )
            for scope in scopes
        ],
    )


@identity_scopes_app.command("create")
@handle_api_errors
def create_identity_scope(
    ctx: typer.Context,
    identity_id: str = typer.Argument(..., help="Application identity ID"),
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store to grant (must be active)"),
    access_mode: AccessMode = typer.Option(
        AccessMode.READ_ONLY,
        "--access-mode",
        help="read_only or read_write (a read_only identity can only get read_only)",
    ),
    allow_version_read: bool = typer.Option(
        True,
        "--allow-version-read/--deny-version-read",
        help="Allow reading historical version values (default: allow, as in the portal)",
    ),
    allow_rollback: bool = typer.Option(False, "--allow-rollback", help="Allow rollback (read_write only)"),
    allow_destroy: bool = typer.Option(
        False,
        "--allow-destroy",
        help="Allow irreversible version destruction (read_write only)",
    ),
    check_state: bool = check_state_option(),
) -> None:
    """Grant an application identity access to another secret store.

    Unless --no-check-state, the identity, its scopes and the stores are read first:
    the store must be active and not already granted, and a read_only identity gets
    read_only access only.
    """
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    identity = _rid(identity_id, "identity_id")
    body = build_scope_create_body(
        store_id, access_mode.value, allow_version_read, allow_rollback, allow_destroy
    )
    client = get_client(settings)
    with sdk_warnings():
        result = client.secret_store.create_secret_identity_scope(
            identity,
            workspace_id=workspace,
            store_id=body["store_id"],
            access_mode=body["access_mode"],
            allow_version_read=body["allow_version_read"],
            allow_rollback=body["allow_rollback"],
            allow_destroy=body["allow_destroy"],
            check_store=check_state,
        )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(
        f"Scope created (id {getattr(result, 'id', '?')}) for identity '{identity}'.",
        fg=typer.colors.GREEN,
    )


@identity_scopes_app.command("update")
@handle_api_errors
def update_identity_scope(
    ctx: typer.Context,
    scope_id: str = typer.Argument(..., help="Identity scope ID"),
    access_mode: Optional[AccessMode] = typer.Option(
        None,
        "--access-mode",
        help="Set read_only or read_write",
    ),
    allow_version_read: Optional[bool] = typer.Option(
        None,
        "--allow-version-read/--deny-version-read",
        help="Allow or deny historical version reads",
    ),
    allow_rollback: Optional[bool] = typer.Option(
        None,
        "--allow-rollback/--deny-rollback",
        help="Allow or deny rollback (read_write scopes only)",
    ),
    allow_destroy: Optional[bool] = typer.Option(
        None,
        "--allow-destroy/--deny-destroy",
        help="Allow or deny irreversible version destruction (read_write scopes only)",
    ),
) -> None:
    """Update store access and version permissions for an identity scope."""
    if (
        access_mode is None
        and allow_version_read is None
        and allow_rollback is None
        and allow_destroy is None
    ):
        raise typer.BadParameter("Provide at least one access or permission option to update.")
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    scope = _rid(scope_id, "scope_id")
    changes = build_scope_update_body(
        access_mode.value if access_mode is not None else None,
        allow_version_read,
        allow_rollback,
        allow_destroy,
    )
    client = get_client(settings)
    result = client.secret_store.update_secret_identity_scope(scope, workspace_id=workspace, **changes)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Scope '{scope}' updated.", fg=typer.colors.GREEN)


@identity_scopes_app.command("delete")
@handle_api_errors
def delete_identity_scope(
    ctx: typer.Context,
    scope_id: str = typer.Argument(..., help="Identity scope ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm scope deletion"),
) -> None:
    """Delete an identity's access to a secret store."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    scope = _rid(scope_id, "scope_id")
    confirm_destructive(
        settings,
        f"Remove access? Scope '{scope}' will lose access to the selected store.",
        yes,
    )
    client = get_client(settings)
    result = client.secret_store.delete_secret_identity_scope(scope, workspace_id=workspace)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Scope '{scope}' deleted.", fg=typer.colors.GREEN)


# ── secrets: `ibee secrets <verb>` ──────────────────────────────────────────

@app.command("list")
@handle_api_errors
def list_secrets(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store ID"),
    query: Optional[str] = typer.Option(
        None, "--query", "-q", help="Case-insensitive name search (at most 128 characters)"
    ),
    page: Optional[int] = typer.Option(None, "--page", min=1, help="Page number (default 1)"),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        min=1,
        max=SECRET_STORE_PAGE_LIMIT_MAX,
        help=f"Secrets per page, 1-{SECRET_STORE_PAGE_LIMIT_MAX} (default {PORTAL_PAGE_LIMIT})",
    ),
    all_pages: bool = typer.Option(False, "--all", help="Fetch every page"),
) -> None:
    """List secrets inside a store (metadata only; soft-deleted secrets are included)."""
    _check_list_options(page, all_pages)
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    store = _rid(store_id, "store_id")
    search = normalize_secret_search_query(query)
    client = get_client(settings)
    if all_pages:
        secrets = client.secret_store.list_all_secrets(
            store, workspace_id=workspace, q=search, page_size=limit
        )
        result: Any = secrets
        total: Any = len(secrets)
    else:
        result = client.secret_store.list_secrets(
            store,
            workspace_id=workspace,
            q=search,
            page=page or 1,
            limit=limit or PORTAL_PAGE_LIMIT,
        )
        secrets = getattr(result, "secrets", None) or []
        total = getattr(result, "total", None)
    if settings.structured_output:
        print_json(result)
        return
    print_table(
        "Secrets",
        ["ID", "Name", "Status", "Updated"],
        [
            (
                cell(s, "id"),
                cell(s, "secret_name"),
                cell(s, "status"),
                str(cell(s, "updated_at") or "")[:19],
            )
            for s in secrets
        ],
    )
    if not all_pages:
        _page_hint("secrets", len(secrets), total, page or 1, limit or PORTAL_PAGE_LIMIT)


@app.command("create")
@handle_api_errors
def create_secret(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store ID"),
    name: str = typer.Option(
        ...,
        "--name",
        "-n",
        help="Secret name: 2-64 lowercase letters, digits and hyphens, starting with a letter or digit "
        "(trimmed and lower-cased, as the portal does)",
    ),
    value: str = typer.Option(
        ..., "--value", help="Value: JSON object or key=value,key=value (no blank keys or values)"
    ),
    billing_check: bool = typer.Option(
        True,
        "--billing-check/--no-billing-check",
        help="Deprecated no-op; upstream decides billing and lifecycle admission.",
    ),
) -> None:
    """Create a secret in an active store."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    store = _rid(store_id, "store_id")
    secret_name = normalize_secret_name(name)
    secret_value = normalize_secret_value(parse_value(value))
    if secret_name != name:
        _note(f"Secret name normalized to '{secret_name}'.")
    client = get_client(settings)
    with sdk_warnings():
        result = client.secret_store.create_secret(
            store,
            workspace_id=workspace,
            secret_name=secret_name,
            value=secret_value,
            preflight_billing=False,
        )
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(
        f"Secret '{secret_name}' created (id {getattr(result, 'id', '?')}).", fg=typer.colors.GREEN
    )


@app.command("batch-create")
@handle_api_errors
def batch_create_secrets(
    ctx: typer.Context,
    store_id: str = typer.Option(..., "--store-id", "-s", help="Secret store ID"),
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        exists=True,
        readable=True,
        dir_okay=False,
        resolve_path=True,
        help="JSON file containing an array or {\"secrets\": [...]} of {secret_name, value}",
    ),
) -> None:
    """Create many secrets without overwriting names that already exist.

    Every item is checked like `secrets create`. A file with more than
    500 secrets or over 64 KiB is sent as consecutive requests and the results are
    merged. Exits 1 when any secret failed.
    """
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    store = _rid(store_id, "store_id")
    items, duplicates = _normalize_batch(_load_batch(file))
    chunks = chunk_batch_secrets(items, max_items=SECRET_BATCH_MAX_ITEMS, max_bytes=SECRET_STORE_MAX_BODY_BYTES)
    if duplicates:
        _note(
            "Warning: duplicate secret names in the file are skipped by the API: " + ", ".join(sorted(duplicates))
        )
    client = get_client(settings)
    merged: dict[str, Any] = {"results": [], "created_count": 0, "skipped_count": 0, "failed_count": 0}
    for number, chunk in enumerate(chunks, start=1):
        try:
            with warnings.catch_warnings():
                # Duplicates were already reported for the whole file.
                warnings.simplefilter("ignore", UserWarning)
                response = client.secret_store.batch_create_secrets(store, workspace_id=workspace, secrets=chunk)
        except ApiError:
            if number > 1:
                _note(
                    f"Batch stopped at request {number} of {len(chunks)}. Earlier requests: "
                    f"{merged['created_count']} created, {merged['skipped_count']} skipped, "
                    f"{merged['failed_count']} failed. Running the command again skips secrets that exist."
                )
            raise
        data = to_data(response)
        data = data if isinstance(data, dict) else {}
        merged["results"].extend(data.get("results") or [])
        for key in ("created_count", "skipped_count", "failed_count"):
            merged[key] += int(data.get(key) or 0)
    if settings.structured_output:
        print_json(merged)
    else:
        if len(chunks) > 1:
            typer.echo(f"Sent {len(items)} secrets in {len(chunks)} requests.")
        typer.secho(
            "Batch complete: "
            f"{merged['created_count']} created, "
            f"{merged['skipped_count']} skipped, "
            f"{merged['failed_count']} failed.",
            fg=typer.colors.GREEN if not merged["failed_count"] else typer.colors.YELLOW,
        )
        for row in merged["results"]:
            if isinstance(row, dict) and row.get("status") in ("skipped", "failed"):
                reason = row.get("error") or ""
                typer.echo(f"  {row.get('status')}: {row.get('secret_name')}" + (f" ({reason})" if reason else ""))
    if merged["failed_count"]:
        raise typer.Exit(code=1)


@app.command("get")
@handle_api_errors
def get_secret(ctx: typer.Context, secret_id: str = typer.Argument(..., help="Secret ID")) -> None:
    """Show one secret's metadata (value not included — use `secrets value`)."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    client = get_client(settings)
    result = client.secret_store.get_secret(_rid(secret_id, "secret_id"), workspace_id=workspace)
    print_json(result)


@app.command("value")
@handle_api_errors
def get_value(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
) -> None:
    """Print a secret's current value as JSON. Requires secret-store.read scope.

    A soft-deleted secret has no readable value (404) until it is undeleted or a new value is set.
    """
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    client = get_client(settings)
    result = client.secret_store.get_secret_value(_rid(secret_id, "secret_id"), workspace_id=workspace)
    print_json(result)


def _version_of(result: Any) -> Any:
    metadata = cell(result, "metadata")
    if isinstance(metadata, dict):
        return metadata.get("version")
    return cell(metadata, "version") if metadata is not None else None


@app.command("set-value")
@handle_api_errors
def set_value(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    value: str = typer.Option(
        ..., "--value", help="New full value: JSON object or key=value,key=value (no blank keys or values)"
    ),
    cas: Optional[int] = typer.Option(
        None,
        "--cas",
        min=0,
        help="Check-and-set: write only if the current version equals this number (0: only if no version exists)",
    ),
) -> None:
    """Replace a secret's value with a new version (reactivates a soft-deleted secret)."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    secret = _rid(secret_id, "secret_id")
    secret_value = normalize_secret_value(parse_value(value))
    checked_cas = validate_cas(cas)
    client = get_client(settings)
    kwargs: dict[str, Any] = {"workspace_id": workspace, "value": secret_value}
    if checked_cas is not None:
        kwargs["cas"] = checked_cas
    result = client.secret_store.update_secret_value(secret, **kwargs)
    if settings.structured_output:
        print_json(result)
        return
    version = _version_of(result)
    suffix = f" (version {version})" if version is not None else ""
    typer.secho(f"Secret '{secret}' value updated{suffix}.", fg=typer.colors.GREEN)


@app.command("patch-value")
@handle_api_errors
def patch_value(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    value: str = typer.Option(
        ...,
        "--value",
        help="Keys to merge: JSON object or key=value pairs. In JSON, null deletes that key",
    ),
) -> None:
    """Merge keys into a secret and create a new version."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    secret = _rid(secret_id, "secret_id")
    secret_value = normalize_secret_value(parse_value(value), allow_null_values=True)
    client = get_client(settings)
    result = client.secret_store.patch_secret_value(secret, workspace_id=workspace, value=secret_value)
    if settings.structured_output:
        print_json(result)
        return
    version = _version_of(result)
    suffix = f" (version {version})" if version is not None else ""
    typer.secho(f"Secret '{secret}' value patched{suffix}.", fg=typer.colors.GREEN)


@app.command("versions")
@handle_api_errors
def list_versions(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
) -> None:
    """List version metadata without returning secret values.

    With -o table, versions are listed newest first with the portal's state:
    active (current), available, soft_deleted or destroyed.
    """
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    client = get_client(settings)
    result = client.secret_store.list_secret_versions(_rid(secret_id, "secret_id"), workspace_id=workspace)
    data = to_data(result)
    data = data if isinstance(data, dict) else {}
    current = data.get("current_version")
    summaries = data.get("versions") or {}
    rows = []
    for key, summary in summaries.items():
        summary = summary if isinstance(summary, dict) else {}
        number = summary.get("version") if summary.get("version") is not None else key
        rows.append(
            (
                number,
                secret_version_state({**summary, "version": number}, current),
                summary.get("created_time") or "",
                summary.get("deletion_time") or "",
            )
        )
    rows.sort(key=lambda row: int(row[0]) if str(row[0]).isdigit() else -1, reverse=True)
    emit(
        result,
        settings=settings,
        default="json",
        headers=["Version", "State", "Created", "Deleted"],
        rows=rows,
        title=f"Versions of {data.get('secret_name') or secret_id} (current {current})",
    )


@app.command("version")
@handle_api_errors
def get_version(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    version: int = typer.Argument(..., min=1, help="Version number (>= 1)"),
) -> None:
    """Print one secret version, including its sensitive value."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    client = get_client(settings)
    result = client.secret_store.get_secret_version(
        _rid(secret_id, "secret_id"), version, workspace_id=workspace
    )
    print_json(result)


@app.command("rollback")
@handle_api_errors
def rollback_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    version: int = typer.Option(..., "--version", min=1, help="Previous version to restore"),
    check_state: bool = check_state_option(),
) -> None:
    """Copy a previous value into a new current version.

    Unless --no-check-state, the versions are read first and the current, an unknown
    or a destroyed version is refused, as in the portal.
    """
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    secret = _rid(secret_id, "secret_id")
    client = get_client(settings)
    with sdk_warnings():
        result = client.secret_store.rollback_secret(
            secret,
            workspace_id=workspace,
            version=version,
            check_target=check_state,
        )
    if settings.structured_output:
        print_json(result)
        return
    current = _version_of(result)
    suffix = f" as version {current}" if current is not None else ""
    typer.secho(f"Secret '{secret}' restored from version {version}{suffix}.", fg=typer.colors.GREEN)


@app.command("undelete")
@handle_api_errors
def undelete_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    versions: Optional[str] = typer.Option(
        None,
        "--versions",
        help="Comma-separated versions to restore (1-100). Default: the current version, "
        "which is the one `secrets delete` soft-deletes",
    ),
) -> None:
    """Restore soft-deleted versions and reactivate a secret."""
    parsed = _parse_versions(versions) if versions is not None else None
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    secret = _rid(secret_id, "secret_id")
    client = get_client(settings)
    if parsed is None:
        # Find the current version here, so a token without secret-store.read gets told
        # to pass --versions instead of a bare 403.
        record = _optional_read(
            "Current version lookup",
            lambda: client.secret_store.list_secret_versions(secret, workspace_id=workspace),
        )
        if record is None:
            fail_usage("Pass --versions N: the token cannot read the secret's versions to find the current one.")
        current = (to_data(record) or {}).get("current_version")
        if not isinstance(current, int) or isinstance(current, bool) or current < 1:
            raise IbeeValidationError(
                "The secret has no current version to undelete; pass --versions explicitly.",
                code="invalid_versions",
                field="versions",
            )
        parsed = [current]
    result = client.secret_store.undelete_secret(secret, workspace_id=workspace, versions=parsed)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Secret '{secret}' restored.", fg=typer.colors.GREEN)


@app.command("destroy-versions")
@handle_api_errors
def destroy_versions(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    versions: str = typer.Option(..., "--versions", help="Comma-separated versions to destroy (1-100)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm irreversible destruction"),
) -> None:
    """Irreversibly destroy selected secret versions."""
    parsed_versions = _parse_versions(versions)
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    secret = _rid(secret_id, "secret_id")
    confirm_destructive(
        settings,
        f"Destroy versions {parsed_versions} of secret '{secret}'? This cannot be undone.",
        yes,
    )
    client = get_client(settings)
    result = client.secret_store.destroy_secret_versions(secret, workspace_id=workspace, versions=parsed_versions)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Secret '{secret}' versions destroyed.", fg=typer.colors.GREEN)


def _secret_label(client: Any, workspace: str, secret_id: str, check_state: bool) -> str:
    if not check_state:
        return secret_id
    record = _optional_read(
        "Secret name lookup", lambda: client.secret_store.get_secret(secret_id, workspace_id=workspace)
    )
    return str(cell(record, "secret_name") or secret_id) if record is not None else secret_id


@app.command("delete-permanent")
@handle_api_errors
def permanently_delete_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm irreversible deletion"),
    check_state: bool = check_state_option(),
) -> None:
    """Permanently delete all versions and metadata. This cannot be undone."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    secret = _rid(secret_id, "secret_id")
    client = get_client(settings)
    label = _secret_label(client, workspace, secret, check_state)
    confirm_destructive(
        settings,
        f"Permanently delete secret '{label}' and all versions? This cannot be undone.",
        yes,
    )
    result = client.secret_store.permanently_delete_secret(secret, workspace_id=workspace)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Secret '{label}' permanently deleted.", fg=typer.colors.GREEN)


@app.command("delete")
@handle_api_errors
def delete_secret(
    ctx: typer.Context,
    secret_id: str = typer.Argument(..., help="Secret ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    check_state: bool = check_state_option(),
) -> None:
    """Soft-delete a secret (its current version). Undo with `ibee secrets undelete`."""
    settings = get_settings(ctx)
    workspace = _workspace(settings)
    secret = _rid(secret_id, "secret_id")
    client = get_client(settings)
    label = _secret_label(client, workspace, secret, check_state)
    confirm_destructive(settings, f"Delete secret? This will soft-delete {label}.", yes)
    result = client.secret_store.delete_secret(secret, workspace_id=workspace)
    if settings.structured_output:
        print_json(result)
        return
    typer.secho(f"Secret '{label}' deleted.", fg=typer.colors.GREEN)
