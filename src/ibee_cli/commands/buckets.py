"""Object storage bucket and S3 credential commands."""

from __future__ import annotations

from typing import List, Optional

import typer

from ..context import api_request, get_settings
from ..helpers import (
    preflight_create,
    confirm_destructive,
    compact_payload,
    parse_json_object,
)
from ..render import handle_api_errors, print_json, print_table

app = typer.Typer(
    help="Object storage buckets and S3 credentials", no_args_is_help=True
)
credentials_app = typer.Typer(help="Manage S3 access credentials", no_args_is_help=True)
app.add_typer(credentials_app, name="credentials")


def _human_size(num: int | None) -> str:
    if num is None:
        return "-"
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return str(num)


def _call(
    ctx: typer.Context,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
) -> object:
    return api_request(
        get_settings(ctx), method, path, params=params, json_body=payload
    )


@app.command("list")
@handle_api_errors
def list_buckets(
    ctx: typer.Context,
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
    continuation_token: Optional[str] = typer.Option(
        None, "--continuation-token", help="Pagination token from the previous page"
    ),
) -> None:
    """List buckets in the workspace."""

    settings = get_settings(ctx)
    result = _call(
        ctx,
        "GET",
        "object-storage/buckets",
        params={"limit": limit, "continuation_token": continuation_token},
    )
    if settings.structured_output:
        print_json(result)
        return
    buckets = result.get("buckets", []) if isinstance(result, dict) else []
    print_table(
        "Buckets",
        ["Name", "Objects", "Size"],
        [
            (
                bucket.get("name"),
                bucket.get("object_count"),
                _human_size(bucket.get("total_size")),
            )
            for bucket in buckets
        ],
    )
    if isinstance(result, dict) and result.get("next_continuation_token"):
        typer.echo(
            "Next page: ibee buckets list --continuation-token "
            f"{result['next_continuation_token']}"
        )


@app.command("create")
@handle_api_errors
def create_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name (unique within the workspace)"),
    region: str = typer.Option(
        ...,
        "--region",
        envvar="IBEE_REGION",
        help="Required Object Storage region identifier (not a compute site ID)",
    ),
    public: bool = typer.Option(False, "--public", help="Allow public reads"),
    bucket_lock: bool = typer.Option(
        False, "--bucket-lock", help="Enable object-lock support"
    ),
    default_retention: Optional[str] = typer.Option(
        None,
        "--default-retention",
        help='Default retention JSON, for example {"mode":"GOVERNANCE","days":30}',
    ),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Tag (repeatable)"),
) -> None:
    """Create a bucket."""

    preflight_create(get_settings(ctx), "object_storage")

    if default_retention is not None and not bucket_lock:
        raise typer.BadParameter(
            "--default-retention requires --bucket-lock."
        )
    result = _call(
        ctx,
        "POST",
        "object-storage/buckets",
        payload=compact_payload(
            name=name,
            region=region,
            is_public=public,
            object_lock_enabled=bucket_lock,
            default_retention=(
                parse_json_object(default_retention, "--default-retention")
                if default_retention is not None
                else None
            ),
            tags=tag or None,
        ),
    )
    print_json(result)


@app.command("get")
@handle_api_errors
def get_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name"),
) -> None:
    """Show bucket configuration and usage."""

    print_json(_call(ctx, "GET", f"object-storage/buckets/{name}"))


@app.command("update")
@handle_api_errors
def update_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name"),
    public: Optional[bool] = typer.Option(
        None, "--public/--private", help="Enable or disable public reads"
    ),
) -> None:
    """Update mutable bucket settings."""

    if public is None:
        raise typer.BadParameter("Provide --public or --private.")
    print_json(
        _call(
            ctx,
            "PATCH",
            f"object-storage/buckets/{name}",
            payload={"is_public": public},
        )
    )


@app.command("delete")
@handle_api_errors
def delete_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a bucket and all of its contents."""

    confirm_destructive(get_settings(ctx), f"Delete bucket '{name}' and all of its contents?", yes)
    result = _call(ctx, "DELETE", f"object-storage/buckets/{name}")
    if result is not None:
        print_json(result)
    else:
        typer.secho(f"Bucket '{name}' deleted.", fg=typer.colors.GREEN)


@credentials_app.command("list")
@handle_api_errors
def list_credentials(ctx: typer.Context) -> None:
    """List S3 credentials without their secret keys."""

    print_json(_call(ctx, "GET", "object-storage/credentials"))


@credentials_app.command("create")
@handle_api_errors
def create_credential(
    ctx: typer.Context,
    name: str = typer.Option("Default Key", "--name"),
    permission_type: str = typer.Option("admin_rw", "--permission-type"),
    bucket_scope: str = typer.Option(
        "all", "--bucket-scope", help="all or specific"
    ),
    allowed_bucket: Optional[List[str]] = typer.Option(
        None,
        "--allowed-bucket",
        help="Bucket name allowed by a specific scope (repeatable)",
    ),
) -> None:
    """Create an S3 access key; its secret is displayed only once."""

    preflight_create(get_settings(ctx), "resource")

    if bucket_scope == "specific" and not allowed_bucket:
        raise typer.BadParameter(
            "Provide at least one --allowed-bucket for a specific scope."
        )
    if bucket_scope == "all" and allowed_bucket:
        raise typer.BadParameter(
            "--allowed-bucket can only be used with --bucket-scope specific."
        )
    result = _call(
        ctx,
        "POST",
        "object-storage/credentials",
        payload=compact_payload(
            name=name,
            permission_type=permission_type,
            bucket_scope=bucket_scope,
            allowed_buckets=allowed_bucket or None,
        ),
    )
    print_json(result)
    typer.secho(
        "Save secret_access_key now; it cannot be retrieved again.",
        fg=typer.colors.YELLOW,
        err=True,
    )


@credentials_app.command("get")
@handle_api_errors
def get_credential(
    ctx: typer.Context,
    access_key_id: str = typer.Argument(..., help="S3 access key ID"),
) -> None:
    """Show S3 credential metadata without its secret key."""

    print_json(
        _call(ctx, "GET", f"object-storage/credentials/{access_key_id}")
    )


@credentials_app.command("revoke")
@handle_api_errors
def revoke_credential(
    ctx: typer.Context,
    access_key_id: str = typer.Argument(..., help="S3 access key ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Permanently revoke an S3 credential."""

    confirm_destructive(get_settings(ctx), f"Revoke S3 credential '{access_key_id}'?", yes)
    result = _call(
        ctx, "DELETE", f"object-storage/credentials/{access_key_id}"
    )
    if result is not None:
        print_json(result)
    else:
        typer.secho(
            f"S3 credential '{access_key_id}' revoked.", fg=typer.colors.GREEN
        )
