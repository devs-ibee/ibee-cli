"""Object Storage bucket and S3 credential commands.

Every command calls the Python SDK, which applies the portal's rules before any
request: the bucket-name rule, the region default for the API host, the Object Lock
retention combinations, the delete pre-checks (Object Lock, non-empty bucket) and the
S3 credential permission and bucket-scope rules. A broken rule exits 2.
"""

from __future__ import annotations

from typing import Any, List, Optional

import typer
from ibee.core.api_error import ApiError
from ibee.validation import (
    BUCKET_PRIVATE_SIDE_EFFECT,
    RETENTION_MODES,
    S3_BUCKET_SCOPES,
    S3_PERMISSION_TYPES,
    check_bucket_deletable,
    s3_endpoint_for_workspace,
    validate_bucket_path_name,
    validate_required_text,
)

from ..context import get_client, get_settings, require_workspace
from ..helpers import check_state_option, confirm_destructive, parse_json_object
from ..render import emit, handle_api_errors, print_json, print_structured, print_table, to_data

app = typer.Typer(
    help="Object storage buckets and S3 credentials", no_args_is_help=True
)
credentials_app = typer.Typer(help="Manage S3 access credentials", no_args_is_help=True)
app.add_typer(credentials_app, name="credentials")

CREDENTIAL_COLUMNS = ("Name", "Access key", "Permission", "Scope", "Status", "Created")
RETENTION_HINT = "Bucket cannot be deleted while retention or legal hold is active."
NOT_EMPTY_HINT = "Empty the bucket first (the empty-bucket API is not yet public; use the S3 API)."


def _session(ctx: typer.Context) -> tuple[Any, str, Any]:
    settings = get_settings(ctx)
    client = get_client(settings)  # token first, as in 0.3.0
    return settings, require_workspace(settings), client


def _human_size(num: int | None) -> str:
    if num is None:
        return "-"
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return str(num)


def _yes_no(value: Any) -> str:
    if value is None:
        return "-"
    return "yes" if value else "no"


def _bucket_rows(buckets: list[dict]) -> list[tuple[Any, ...]]:
    return [
        (
            bucket.get("name"),
            bucket.get("region"),
            _yes_no(bucket.get("is_public")),
            _yes_no(bucket.get("bucket_lock_enabled")),
            bucket.get("object_count"),
            _human_size(bucket.get("total_size")),
        )
        for bucket in buckets
    ]


@app.command("list")
@handle_api_errors
def list_buckets(
    ctx: typer.Context,
    limit: Optional[int] = typer.Option(None, "--limit", help="Page size, 1-1000 (default 100)"),
    continuation_token: Optional[str] = typer.Option(
        None, "--continuation-token", help="Pagination token from the previous page"
    ),
    all_pages: bool = typer.Option(False, "--all", help="Read every page (--limit sets the page size)"),
) -> None:
    """List buckets in the workspace."""

    settings, workspace, client = _session(ctx)
    if all_pages:
        if continuation_token is not None:
            raise typer.BadParameter(
                "--continuation-token cannot be used with --all.", param_hint="--continuation-token"
            )
        buckets = [to_data(item) for item in client.object_storage.list_all_buckets(
            workspace_id=workspace, page_size=100 if limit is None else limit
        )]
        result: Any = {"buckets": buckets, "is_truncated": False, "next_continuation_token": None}
    else:
        result = to_data(client.object_storage.list_buckets(
            workspace_id=workspace, limit=100 if limit is None else limit, continuation_token=continuation_token
        ))
        buckets = result.get("buckets") or [] if isinstance(result, dict) else []
    if settings.structured_output:
        print_json(result, id_field="name")
        return
    print_table(
        "Buckets",
        ["Name", "Region", "Public", "Object Lock", "Objects", "Size"],
        _bucket_rows(buckets),
    )
    token = result.get("next_continuation_token") if isinstance(result, dict) else None
    if token:
        typer.echo(f"Next page: ibee buckets list --continuation-token {token}")


def _retention(
    raw: Optional[str], mode: Optional[str], days: Optional[int], years: Optional[int]
) -> Optional[dict]:
    if raw is not None and (mode is not None or days is not None or years is not None):
        raise typer.BadParameter(
            "Use --default-retention JSON or --retention-mode with --retention-days/--retention-years, not both."
        )
    if raw is not None:
        return parse_json_object(raw, "--default-retention")
    if mode is None:
        if days is not None or years is not None:
            raise typer.BadParameter("--retention-days/--retention-years require --retention-mode.")
        return None
    if days is not None and years is not None:
        raise typer.BadParameter("Use only one of --retention-days or --retention-years.")
    if days is None and years is None:
        raise typer.BadParameter("--retention-mode needs --retention-days or --retention-years.")
    retention: dict[str, Any] = {"mode": mode.strip().upper()}
    if days is not None:
        retention["days"] = days
    else:
        retention["years"] = years
    return retention


@app.command("create")
@handle_api_errors
def create_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(
        ..., help="3-63 lowercase letters, numbers and hyphens; starts and ends with a letter or number"
    ),
    region: Optional[str] = typer.Option(
        None,
        "--region",
        envvar="IBEE_REGION",
        help=(
            "Object Storage region (not a compute site ID). Default: in-south-1 on api.ibee.ai, "
            "in-south-2 on api.ibee.co.in; required for other endpoints"
        ),
    ),
    public: bool = typer.Option(False, "--public", help="Allow public reads (the portal creates private buckets)"),
    bucket_lock: Optional[bool] = typer.Option(
        None,
        "--bucket-lock/--no-bucket-lock",
        help="Enable Object Lock (switched on automatically with a default retention)",
    ),
    retention_mode: Optional[str] = typer.Option(
        None, "--retention-mode", metavar="|".join(RETENTION_MODES), help="Default retention mode"
    ),
    retention_days: Optional[int] = typer.Option(None, "--retention-days", help="Default retention, 1-36500 days"),
    retention_years: Optional[int] = typer.Option(None, "--retention-years", help="Default retention, 1-100 years"),
    default_retention: Optional[str] = typer.Option(
        None,
        "--default-retention",
        help='Default retention JSON, for example {"mode":"GOVERNANCE","days":30} (kept for 0.3.0)',
    ),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag (repeatable)"),
    preflight: bool = typer.Option(
        False,
        "--preflight-billing",
        help="Check OBJECTST-STD billing eligibility first (same as the global --check-billing)",
    ),
) -> None:
    """Create a bucket."""

    settings, workspace, client = _session(ctx)
    retention = _retention(default_retention, retention_mode, retention_days, retention_years)
    result = client.object_storage.create_bucket(
        workspace_id=workspace,
        name=name,
        region=region,
        is_public=public,
        object_lock_enabled=bucket_lock,
        default_retention=retention,
        tags=tag or None,
        preflight_billing=preflight or settings.check_billing,
    )
    print_json(result, id_field="name")


@app.command("get")
@handle_api_errors
def get_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name"),
) -> None:
    """Show bucket configuration and usage."""

    _settings, workspace, client = _session(ctx)
    print_json(client.object_storage.get_bucket(name, workspace_id=workspace), id_field="name")


@app.command("update")
@handle_api_errors
def update_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name"),
    public: Optional[bool] = typer.Option(
        None, "--public/--private", help="Enable or disable public reads"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation for --private"),
) -> None:
    """Make a bucket public or private.

    Making a bucket private disables its public URL and deletes any CDN distribution that
    uses it as origin; --private asks for confirmation unless --yes.
    """

    if public is None:
        raise typer.BadParameter("Provide --public or --private.")
    settings, workspace, client = _session(ctx)
    bucket = validate_bucket_path_name(name, field="name")
    if not public:
        confirm_destructive(
            settings,
            f"{BUCKET_PRIVATE_SIDE_EFFECT} Make '{bucket}' private?",
            yes,
        )
    print_json(
        client.object_storage.update_bucket(bucket, workspace_id=workspace, is_public=public), id_field="name"
    )


def _delete_hint(exc: ApiError) -> Optional[str]:
    text = f"{getattr(exc, 'message', '')} {getattr(exc, 'body', '')}".lower()
    if exc.status_code == 409 and "not empty" in text:
        return NOT_EMPTY_HINT
    if exc.status_code == 403 and any(word in text for word in ("retention", "legal hold", "object lock")):
        return RETENTION_HINT
    return None


@app.command("delete")
@handle_api_errors
def delete_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    skip_preflight: bool = typer.Option(
        False,
        "--skip-preflight",
        help="Skip the object-count check (bucket statistics can lag); the Object Lock check still runs",
    ),
    check_state: bool = check_state_option(),
) -> None:
    """Delete an empty bucket.

    Like the portal, a bucket with Object Lock enabled, or one that still holds objects,
    is refused before anything is deleted. The API also refuses a bucket that is not empty.
    """

    settings, workspace, client = _session(ctx)
    bucket = validate_bucket_path_name(name, field="name")
    if check_state:
        check_bucket_deletable(
            client.object_storage.get_bucket(bucket, workspace_id=workspace), skip_preflight=skip_preflight
        )
    confirm_destructive(settings, f"Delete empty bucket '{bucket}'?", yes)
    try:
        result = client.object_storage.delete_bucket(bucket, workspace_id=workspace, check_state=False)
    except ApiError as exc:
        hint = _delete_hint(exc)
        if hint and not getattr(exc, "cli_hint", None):
            exc.cli_hint = hint  # type: ignore[attr-defined]
        raise
    if result is not None:
        print_json(result)
    else:
        typer.secho(f"Bucket '{bucket}' deleted.", fg=typer.colors.GREEN)


def _credential_rows(credentials: list[dict]) -> list[tuple[Any, ...]]:
    rows = []
    for item in credentials:
        scope = item.get("bucket_scope") or "all"
        if scope == "specific":
            scope = ", ".join(item.get("allowed_buckets") or []) or "specific"
        rows.append(
            (
                item.get("name"),
                item.get("access_key_id"),
                item.get("permission_type"),
                scope,
                item.get("status"),
                item.get("created_at"),
            )
        )
    return rows


@credentials_app.command("list")
@handle_api_errors
def list_credentials(ctx: typer.Context) -> None:
    """List S3 credentials without their secret keys (the API returns at most the 100 newest).

    -o table shows name, access key, permission, bucket scope, status and creation time.
    """

    settings, workspace, client = _session(ctx)
    result = to_data(client.object_storage.list_s3credentials(workspace_id=workspace))
    credentials = result.get("credentials") or [] if isinstance(result, dict) else []
    emit(
        result,
        settings=settings,
        default="json",
        headers=CREDENTIAL_COLUMNS,
        rows=_credential_rows(credentials),
        title="S3 credentials",
        id_field="access_key_id",
    )


@credentials_app.command("create")
@handle_api_errors
def create_credential(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(None, "--name", help="1-100 characters (default 'Default Key')"),
    permission_type: Optional[str] = typer.Option(
        None,
        "--permission-type",
        metavar="|".join(S3_PERMISSION_TYPES),
        help="Default admin_rw (as the portal)",
    ),
    bucket_scope: Optional[str] = typer.Option(
        None,
        "--bucket-scope",
        metavar="|".join(S3_BUCKET_SCOPES),
        help="Default all; 'specific' is only for object_rw/object_ro and needs --allowed-bucket",
    ),
    allowed_bucket: Optional[List[str]] = typer.Option(
        None,
        "--allowed-bucket",
        help="Bucket name allowed by a specific scope (repeatable)",
    ),
    preflight: bool = typer.Option(
        False,
        "--preflight-billing",
        help="Check OBJECTST-STD billing eligibility first (same as the global --check-billing)",
    ),
) -> None:
    """Create an S3 access key; its secret is displayed only once.

    The request is never retried automatically, so the one-time secret cannot be lost.
    """

    settings, workspace, client = _session(ctx)
    result = client.object_storage.create_s3credential(
        workspace_id=workspace,
        name=name,
        permission_type=permission_type,
        bucket_scope=bucket_scope,
        allowed_buckets=allowed_bucket or None,
        preflight_billing=preflight or settings.check_billing,
    )
    # Never print only the ID: the one-time secret would be lost. -o id falls back to JSON.
    print_structured(result, "yaml" if settings.output == "yaml" else "json", id_field="access_key_id")
    typer.secho(
        "Save secret_access_key now; it cannot be retrieved again.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    typer.secho(f"S3 endpoint: {s3_endpoint_for_workspace(workspace)}", err=True)


@credentials_app.command("get")
@handle_api_errors
def get_credential(
    ctx: typer.Context,
    access_key_id: str = typer.Argument(..., help="S3 access key ID"),
) -> None:
    """Show S3 credential metadata without its secret key."""

    _settings, workspace, client = _session(ctx)
    print_json(client.object_storage.get_s3credential(access_key_id, workspace_id=workspace), id_field="access_key_id")


def _delete_credential(ctx: typer.Context, access_key_id: str, yes: bool) -> None:
    settings, workspace, client = _session(ctx)
    key = validate_required_text(access_key_id, field="access_key_id")
    confirm_destructive(
        settings, f"Permanently delete S3 credential '{key}'? It cannot be restored or re-enabled.", yes
    )
    result = client.object_storage.delete_s3credential(key, workspace_id=workspace)
    if result is not None:
        print_json(result)
    else:
        typer.secho(f"S3 credential '{key}' deleted.", fg=typer.colors.GREEN)


@credentials_app.command("revoke")
@handle_api_errors
def revoke_credential(
    ctx: typer.Context,
    access_key_id: str = typer.Argument(..., help="S3 access key ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Permanently delete an S3 credential (same as 'delete'; it cannot be re-enabled)."""

    _delete_credential(ctx, access_key_id, yes)


@credentials_app.command("delete")
@handle_api_errors
def delete_credential(
    ctx: typer.Context,
    access_key_id: str = typer.Argument(..., help="S3 access key ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Permanently delete an S3 credential; it cannot be restored or re-enabled."""

    _delete_credential(ctx, access_key_id, yes)
