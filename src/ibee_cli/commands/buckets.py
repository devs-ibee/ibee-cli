"""Object storage bucket commands."""

from __future__ import annotations

import typer

from ..context import get_client, get_settings, require_workspace
from ..render import handle_api_errors, print_json, print_table

app = typer.Typer(help="Object storage buckets", no_args_is_help=True)


def _human_size(num: int | None) -> str:
    if num is None:
        return "-"
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return str(num)


@app.command("list")
@handle_api_errors
def list_buckets(ctx: typer.Context) -> None:
    """List buckets in the workspace."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.object_storage.list_buckets(workspace_id=require_workspace(settings))
    if settings.as_json:
        print_json(result)
        return
    buckets = result.buckets or []
    print_table(
        "Buckets",
        ["Name", "Objects", "Size"],
        [(b.name, b.object_count, _human_size(b.total_size)) for b in buckets],
    )


@app.command("create")
@handle_api_errors
def create_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name (unique within the workspace)"),
    public: bool = typer.Option(False, "--public", help="Allow unauthenticated read access"),
    region: str = typer.Option(
        "in-south-1",
        "--region",
        envvar="IBEE_REGION",
        help="Storage region for the bucket",
    ),
) -> None:
    """Create a bucket."""
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.object_storage.create_bucket(
        workspace_id=require_workspace(settings),
        name=name,
        region=region,
        is_public=public,
    )
    if settings.as_json:
        print_json(result)
        return
    typer.secho(f"Bucket '{name}' created in {region}.", fg=typer.colors.GREEN)


@app.command("delete")
@handle_api_errors
def delete_bucket(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Bucket name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a bucket."""
    settings = get_settings(ctx)
    if not yes:
        typer.confirm(f"Delete bucket '{name}'?", abort=True)
    client = get_client(settings)
    client.object_storage.delete_bucket(workspace_id=require_workspace(settings), bucket_name=name)
    typer.secho(f"Bucket '{name}' deleted.", fg=typer.colors.GREEN)
