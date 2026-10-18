"""CDN distributions, website delivery, custom domains, signed URLs and purges (``ibee cdn``).

Each command makes one SDK call and prints the server's answer; the server checks
origins, cache policies, domains and purge selectors. A purge the CDN answers with
``success: false`` exits 1.
"""

from __future__ import annotations

from typing import Any, List, Optional

import typer

from ..context import get_client, get_settings
from ..helpers import compact_payload, confirm_destructive, field
from ..render import EXIT_FAILURE, cell, emit, handle_api_errors, print_json

DISTRIBUTION_HEADERS = ("ID", "Name", "Origin", "Cache policy", "Status", "Default URL")

app = typer.Typer(help="CDN distributions and delivery configuration", no_args_is_help=True)
website_app = typer.Typer(help="Static website (SPA) configuration", no_args_is_help=True)
domains_app = typer.Typer(help="Distribution custom domains", no_args_is_help=True)
app.add_typer(website_app, name="website")
app.add_typer(domains_app, name="domains")


def _cdn(ctx: typer.Context):
    settings = get_settings(ctx)
    return settings, get_client(settings).cdn


def _print(result: Any) -> None:
    if result is not None:
        print_json(result)


@app.command("generate-url")
@handle_api_errors
def generate_url(
    ctx: typer.Context,
    bucket_name: str = typer.Argument(..., help="Bucket name"),
    object_key: str = typer.Argument(..., help="Object key"),
    expires_in: Optional[int] = typer.Option(None, "--expires-in", help="Seconds until the URL expires"),
    disposition: Optional[str] = typer.Option(None, "--disposition", help="inline or attachment"),
) -> None:
    """Generate a CDN URL for an object."""
    _settings, cdn = _cdn(ctx)
    _print(cdn.generate_cdn_url(
        bucket_name=bucket_name,
        object_key=object_key,
        **compact_payload(expires_in=expires_in, disposition=disposition),
    ))


@app.command("list")
@handle_api_errors
def list_distributions(ctx: typer.Context) -> None:
    """List CDN distributions (-o table shows a table)."""
    settings, cdn = _cdn(ctx)
    result = cdn.list_cdn_distributions()
    rows = [
        (cell(d, "id"), cell(d, "name"), cell(d, "bucket_name") or cell(d, "origin_id"), cell(d, "cache_policy"),
         cell(d, "status"), cell(d, "default_url"))
        for d in field(result, "distributions") or []
    ]
    emit(result, settings=settings, default="json", headers=DISTRIBUTION_HEADERS, rows=rows,
         title="CDN distributions", id_field="id")


@app.command("create")
@handle_api_errors
def create_distribution(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Distribution name"),
    origin_id: str = typer.Option(..., "--origin-id", help="Origin bucket name"),
    origin_type: Optional[str] = typer.Option(None, "--origin-type", help="bucket or custom"),
    cache_policy: Optional[str] = typer.Option(None, "--cache-policy", help="Cache policy"),
) -> None:
    """Create a CDN distribution."""
    _settings, cdn = _cdn(ctx)
    _print(cdn.create_cdn_distribution(
        name=name, origin_id=origin_id, **compact_payload(origin_type=origin_type, cache_policy=cache_policy)
    ))


@app.command("get")
@handle_api_errors
def get_distribution(ctx: typer.Context, distribution_id: str = typer.Argument(..., help="Distribution ID")) -> None:
    """Show a CDN distribution."""
    _settings, cdn = _cdn(ctx)
    _print(cdn.get_cdn_distribution(distribution_id))


@app.command("update")
@handle_api_errors
def update_distribution(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    cache_policy: Optional[str] = typer.Option(None, "--cache-policy"),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled"),
) -> None:
    """Update a distribution's name, cache policy or enabled state."""
    changes = compact_payload(name=name, cache_policy=cache_policy, enabled=enabled)
    if not changes:
        raise typer.BadParameter("Provide at least one of --name, --cache-policy, --enabled/--disabled.")
    _settings, cdn = _cdn(ctx)
    _print(cdn.update_cdn_distribution(distribution_id, **changes))


@app.command("delete")
@handle_api_errors
def delete_distribution(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a distribution."""
    settings, cdn = _cdn(ctx)
    confirm_destructive(settings, f"Delete CDN distribution '{distribution_id}'?", yes)
    _print(cdn.delete_cdn_distribution(distribution_id))


@website_app.command("get")
@handle_api_errors
def get_website(ctx: typer.Context, distribution_id: str = typer.Argument(..., help="Distribution ID")) -> None:
    """Show the static website configuration."""
    _settings, cdn = _cdn(ctx)
    _print(cdn.get_cdn_website_config(distribution_id))


@website_app.command("set")
@handle_api_errors
def set_website(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    index_document: Optional[str] = typer.Option(
        None, "--index-document", help="Object served for the site root and unknown paths"
    ),
) -> None:
    """Enable static website (SPA) delivery."""
    _settings, cdn = _cdn(ctx)
    _print(cdn.update_cdn_website_config(distribution_id, **compact_payload(index_document=index_document)))


@website_app.command("delete")
@handle_api_errors
def delete_website(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Disable static website delivery."""
    settings, cdn = _cdn(ctx)
    confirm_destructive(settings, f"Disable static website delivery for distribution '{distribution_id}'?", yes)
    _print(cdn.delete_cdn_website_config(distribution_id))


@domains_app.command("list")
@handle_api_errors
def list_domains(ctx: typer.Context, distribution_id: str = typer.Argument(..., help="Distribution ID")) -> None:
    """List a distribution's custom domains."""
    _settings, cdn = _cdn(ctx)
    _print(cdn.list_cdn_custom_domains(distribution_id))


@domains_app.command("create")
@handle_api_errors
def create_domain(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    domain: str = typer.Argument(..., help="Host name, for example cdn.example.com"),
) -> None:
    """Add a custom domain; the answer carries the DNS record to create."""
    _settings, cdn = _cdn(ctx)
    _print(cdn.create_cdn_custom_domain(distribution_id, domain=domain))


@domains_app.command("get")
@handle_api_errors
def get_domain(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    domain: str = typer.Argument(..., help="Custom domain"),
) -> None:
    """Show a custom domain and its validation record."""
    _settings, cdn = _cdn(ctx)
    _print(cdn.get_cdn_custom_domain(distribution_id, domain))


@domains_app.command("verify")
@handle_api_errors
def verify_domain(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    domain: str = typer.Argument(..., help="Custom domain"),
) -> None:
    """Check the domain's DNS and certificate once."""
    _settings, cdn = _cdn(ctx)
    _print(cdn.verify_cdn_custom_domain(distribution_id, domain))


@domains_app.command("delete")
@handle_api_errors
def delete_domain(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    domain: str = typer.Argument(..., help="Custom domain"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a custom domain (then delete its CNAME record at your DNS provider)."""
    settings, cdn = _cdn(ctx)
    confirm_destructive(settings, f"Remove custom domain '{domain}' from distribution '{distribution_id}'?", yes)
    _print(cdn.delete_cdn_custom_domain(distribution_id, domain))


@app.command("purge")
@handle_api_errors
def purge_cache(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    mode: str = typer.Option(..., "--mode", help="url, hostname, tag, prefix or all"),
    path: Optional[List[str]] = typer.Option(None, "--path", help="--mode url: path or URL (repeatable)"),
    hostname: Optional[List[str]] = typer.Option(None, "--hostname", help="--mode hostname: host name (repeatable)"),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="--mode tag: cache tag (repeatable)"),
    prefix: Optional[List[str]] = typer.Option(None, "--prefix", help="--mode prefix: path prefix (repeatable)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation for --mode all"),
) -> None:
    """Purge cached content (--mode all asks for confirmation unless --yes).

    Exits 1 when the CDN answers that the purge failed.
    """
    settings, cdn = _cdn(ctx)
    if mode == "all":
        confirm_destructive(settings, f"Purge all cached content of distribution '{distribution_id}'?", yes)
    result = cdn.purge_cdn_cache(
        distribution_id,
        mode=mode,
        **compact_payload(paths=path or None, hostnames=hostname or None, tags=tag or None, prefixes=prefix or None),
    )
    _print(result)
    if field(result, "success") is False:
        raise typer.Exit(code=EXIT_FAILURE)
