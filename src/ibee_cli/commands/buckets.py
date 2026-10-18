"""Object Storage buckets and S3 credentials (``ibee buckets``).

Each command makes one SDK call and prints the server's answer; the server checks
names, regions, Object Lock retention, deletability and credential scopes.
"""

from __future__ import annotations

from typing import List, Optional

import typer

from ..context import get_client, get_settings
from ..helpers import compact_payload, confirm_destructive, field, parse_json_object
from ..render import cell, emit, handle_api_errors, print_json, print_structured, print_table

app = typer.Typer(help="Object storage buckets and S3 credentials", no_args_is_help=True)
credentials_app = typer.Typer(help="Manage S3 access credentials", no_args_is_help=True)
app.add_typer(credentials_app, name="credentials")

BUCKET_HEADERS = ("Name", "Region", "Public", "Object Lock", "Objects", "Size")
CREDENTIAL_HEADERS = ("Name", "Access key", "Permission", "Scope", "Status", "Created")


def _object_storage(ctx: typer.Context):
    settings = get_settings(ctx)
    return settings, get_client(settings).object_storage


def _yes_no(value) -> Optional[str]:
    return None if value is None else ("yes" if value else "no")


def _human_size(num: Optional[int]) -> Optional[str]:
    if num is None:
        return None
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _bucket_row(bucket) -> tuple:
    return (
        cell(bucket, "name"),
        cell(bucket, "region"),
        _yes_no(cell(bucket, "is_public")),
        _yes_no(cell(bucket, "bucket_lock_enabled")),
        cell(bucket, "object_count"),
        _human_size(cell(bucket, "total_size")),
    )


def _credential_row(item) -> tuple:
    scope = cell(item, "bucket_scope")
    if scope == "specific" and cell(item, "allowed_buckets"):
        scope = ", ".join(cell(item, "allowed_buckets"))
    columns = ("name", "access_key_id", "permission_type")
    return (*(cell(item, c) for c in columns), scope, cell(item, "status"), cell(item, "created_at"))


@app.command("list")
@handle_api_errors
def list_buckets(
    ctx: typer.Context,
    limit: Optional[int] = typer.Option(None, "--limit", help="Page size"),
    continuation_token: Optional[str] = typer.Option(
        None, "--continuation-token", help="Pagination token from the previous page"
    ),
) -> None:
    """List buckets in the workspace (one page)."""
    settings, storage = _object_storage(ctx)
    result = storage.list_buckets(**compact_payload(limit=limit, continuation_token=continuation_token))
    if settings.structured_output:
        print_json(result, id_field="name")
        return
    rows = [_bucket_row(bucket) for bucket in field(result, "buckets") or []]
    print_table("Buckets", BUCKET_HEADERS, rows)
    token = field(result, "next_continuation_token")
    if token:
        typer.echo(f"Next page: ibee buckets list --continuation-token {token}")


def _retention(raw: Optional[str], mode: Optional[str], days: Optional[int], years: Optional[int]) -> Optional[dict]:
    if raw is not None and (mode is not None or days is not None or years is not None):
        raise typer.BadParameter("Use --default-retention or --retention-mode/--retention-days/--retention-years.")
    if raw is not None:
        return parse_json_object(raw, "--default-retention")
    return compact_payload(mode=mode, days=days, years=years) or None


@app.command("create")
@handle_api_errors
def create_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name"),
    region: str = typer.Option(..., "--region", envvar="IBEE_REGION", help="Object Storage region"),
    public: bool = typer.Option(False, "--public", help="Allow public reads"),
    bucket_lock: Optional[bool] = typer.Option(None, "--bucket-lock/--no-bucket-lock", help="Enable Object Lock"),
    retention_mode: Optional[str] = typer.Option(None, "--retention-mode", help="GOVERNANCE or COMPLIANCE"),
    retention_days: Optional[int] = typer.Option(None, "--retention-days", help="Default retention in days"),
    retention_years: Optional[int] = typer.Option(None, "--retention-years", help="Default retention in years"),
    default_retention: Optional[str] = typer.Option(
        None, "--default-retention", metavar="JSON", help='Retention JSON, for example {"mode":"GOVERNANCE","days":30}'
    ),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag (repeatable)"),
) -> None:
    """Create a bucket."""
    settings, storage = _object_storage(ctx)
    retention = _retention(default_retention, retention_mode, retention_days, retention_years)
    result = storage.create_bucket(
        name=name,
        region=region,
        **compact_payload(
            is_public=True if public else None,
            object_lock_enabled=bucket_lock,
            default_retention=retention,
            tags=tag or None,
        ),
    )
    print_json(result, id_field="name")


@app.command("get")
@handle_api_errors
def get_bucket(ctx: typer.Context, name: str = typer.Argument(..., help="Bucket name")) -> None:
    """Show bucket configuration and usage."""
    _settings, storage = _object_storage(ctx)
    print_json(storage.get_bucket(name), id_field="name")


@app.command("update")
@handle_api_errors
def update_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name"),
    public: Optional[bool] = typer.Option(None, "--public/--private", help="Enable or disable public reads"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation for --private"),
) -> None:
    """Make a bucket public or private (--private asks for confirmation unless --yes)."""
    if public is None:
        raise typer.BadParameter("Provide --public or --private.")
    settings, storage = _object_storage(ctx)
    if not public:
        confirm_destructive(settings, f"Make bucket '{name}' private?", yes)
    print_json(storage.update_bucket(name, is_public=public), id_field="name")


@app.command("delete")
@handle_api_errors
def delete_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a bucket."""
    settings, storage = _object_storage(ctx)
    confirm_destructive(settings, f"Delete bucket '{name}'?", yes)
    print_json(storage.delete_bucket(name))


@credentials_app.command("list")
@handle_api_errors
def list_credentials(ctx: typer.Context) -> None:
    """List S3 credentials without their secret keys (-o table shows a table)."""
    settings, storage = _object_storage(ctx)
    result = storage.list_s3credentials()
    rows = [_credential_row(item) for item in field(result, "credentials") or []]
    emit(result, settings=settings, default="json", headers=CREDENTIAL_HEADERS, rows=rows, title="S3 credentials",
         id_field="access_key_id")


@credentials_app.command("create")
@handle_api_errors
def create_credential(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(None, "--name", help="Credential name"),
    permission_type: Optional[str] = typer.Option(None, "--permission-type", help="For example admin_rw or object_ro"),
    bucket_scope: Optional[str] = typer.Option(None, "--bucket-scope", help="all or specific"),
    allowed_bucket: Optional[List[str]] = typer.Option(
        None, "--allowed-bucket", help="Bucket allowed by a specific scope (repeatable)"
    ),
) -> None:
    """Create an S3 access key; its secret is shown only once.

    The full answer, secret included, is always printed (-o id prints JSON too) so the
    secret cannot be lost.
    """
    settings, storage = _object_storage(ctx)
    result = storage.create_s3credential(
        **compact_payload(
            name=name,
            permission_type=permission_type,
            bucket_scope=bucket_scope,
            allowed_buckets=allowed_bucket or None,
        )
    )
    print_structured(result, "yaml" if settings.output == "yaml" else "json")
    typer.secho("Save secret_access_key now; it cannot be retrieved again.", fg=typer.colors.YELLOW, err=True)


@credentials_app.command("get")
@handle_api_errors
def get_credential(ctx: typer.Context, access_key_id: str = typer.Argument(..., help="S3 access key ID")) -> None:
    """Show S3 credential metadata without its secret key."""
    _settings, storage = _object_storage(ctx)
    print_json(storage.get_s3credential(access_key_id), id_field="access_key_id")


@credentials_app.command("revoke")
@handle_api_errors
def revoke_credential(
    ctx: typer.Context,
    access_key_id: str = typer.Argument(..., help="S3 access key ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Revoke an S3 credential."""
    settings, storage = _object_storage(ctx)
    confirm_destructive(settings, f"Revoke S3 credential '{access_key_id}'?", yes)
    print_json(storage.revoke_s3credential(access_key_id))
