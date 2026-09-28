"""CDN distribution, website, custom-domain, URL, metrics and purge commands.

Every command calls the Python SDK, which applies the portal's rules before any
request: distribution names of 1-128 characters, the portal's four cache policies,
a public bucket as origin, index-document and custom-domain syntax, and one purge
selector matching the purge mode. A broken rule exits 2. A purge the CDN reports as
failed (``success: false``) exits 1.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

import typer
from ibee.errors import NotFoundError, OperationTimeoutError
from ibee.validation import (
    CDN_CACHE_POLICIES,
    CDN_METRICS_RANGES,
    CDN_ORIGIN_TYPES,
    CDN_PURGE_ALL_WARNING,
    CDN_PURGE_MODES,
    CDN_URL_DISPOSITIONS,
    build_cdn_purge_body,
    normalize_cdn_domain,
    validate_required_text,
)

from ..context import get_client, get_settings, require_workspace
from ..helpers import confirm_destructive, poll_interval_option, resolve_wait, timeout_option
from ..render import EXIT_FAILURE, EXIT_WAIT_TIMEOUT, emit, handle_api_errors, print_json, to_data

UNCONTRACTED = "Not yet part of the published API contract; behaviour may change."
DOMAIN_PENDING_MESSAGE = "DNS records not yet propagated. Try again in a few minutes."
DOMAIN_WAIT_TIMEOUT_SECONDS = 600.0
DOMAIN_POLL_INTERVAL_SECONDS = 15.0
METRICS_TIMEOUT_SECONDS = 15
DISTRIBUTION_COLUMNS = ("ID", "Name", "Origin", "Cache policy", "Status", "Default URL")

app = typer.Typer(help="CDN distributions and delivery configuration", no_args_is_help=True)
website_app = typer.Typer(help="Static website (SPA) configuration", no_args_is_help=True)
domains_app = typer.Typer(help="Distribution custom domains", no_args_is_help=True)
app.add_typer(website_app, name="website")
app.add_typer(domains_app, name="domains")


def _session(ctx: typer.Context) -> tuple[Any, str, Any]:
    settings = get_settings(ctx)
    client = get_client(settings)  # token first, as in 0.3.0
    return settings, require_workspace(settings), client


def _print(result: Any) -> None:
    if result is not None:
        print_json(result)


def _shorten_origin(origin_id: Any) -> str:
    """The portal's origin label: the bucket name, else a shortened opaque id."""

    value = str(origin_id or "")
    return f"{value[:6]}…{value[-4:]}" if len(value) > 14 else value


def _origin_label(distribution: dict) -> str:
    if distribution.get("origin_type", "bucket") == "bucket":
        name = str(distribution.get("bucket_name") or "").strip()
        if name:
            return name
    return _shorten_origin(distribution.get("origin_id"))


@app.command("generate-url")
@handle_api_errors
def generate_url(
    ctx: typer.Context,
    bucket_name: str = typer.Argument(..., help="Bucket name"),
    object_key: str = typer.Argument(..., help="Object key"),
    expires_in: Optional[int] = typer.Option(None, "--expires-in", help="Seconds until the URL expires (>= 1)"),
    disposition: Optional[str] = typer.Option(
        None, "--disposition", metavar="|".join(CDN_URL_DISPOSITIONS), help="Content-Disposition of the download"
    ),
) -> None:
    """Generate a CDN URL for an object."""

    _settings, workspace, client = _session(ctx)
    _print(client.cdn.generate_cdn_url(
        workspace_id=workspace,
        bucket_name=bucket_name,
        object_key=object_key,
        expires_in=expires_in,
        disposition=disposition,
    ))


@app.command("list")
@handle_api_errors
def list_distributions(ctx: typer.Context) -> None:
    """List CDN distributions (-o table shows the origin bucket, cache policy and URL)."""

    settings, workspace, client = _session(ctx)
    result = to_data(client.cdn.list_cdn_distributions(workspace_id=workspace))
    items = result.get("distributions") or [] if isinstance(result, dict) else list(result or [])
    rows = [
        (
            item.get("id"),
            item.get("name"),
            _origin_label(item),
            item.get("cache_policy"),
            item.get("status"),
            item.get("default_url") or item.get("default_domain"),
        )
        for item in items
        if isinstance(item, dict)
    ]
    emit(result, settings=settings, default="json", headers=DISTRIBUTION_COLUMNS, rows=rows,
         title="CDN distributions", id_field="id")


@app.command("cache-policies")
@handle_api_errors
def list_cache_policies(ctx: typer.Context) -> None:
    """List CDN cache policies. Not yet part of the published API contract; behaviour may change.

    Create and update accept static-assets, media, short and no-cache only (as the portal).
    """

    _settings, workspace, client = _session(ctx)
    _print(client.cdn.list_cdn_cache_policies(workspace_id=workspace))


@app.command("create")
@handle_api_errors
def create_distribution(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="1-128 characters"),
    origin_id: str = typer.Option(..., "--origin-id", help="Origin bucket name (a public bucket)"),
    origin_type: str = typer.Option(
        "bucket",
        "--origin-type",
        metavar="|".join(CDN_ORIGIN_TYPES),
        help="'custom' needs a custom-origin ID, which the public API cannot create yet",
    ),
    cache_policy: str = typer.Option(
        "static-assets", "--cache-policy", metavar="|".join(CDN_CACHE_POLICIES), help="Cache policy"
    ),
    check_origin: bool = typer.Option(
        True,
        "--check-origin/--no-check-origin",
        help="Read the origin bucket first and refuse a private one (default: on)",
    ),
    preflight: bool = typer.Option(
        False,
        "--preflight-billing",
        help="Check billing eligibility first (same as the global --check-billing)",
    ),
) -> None:
    """Create a CDN distribution for a public bucket.

    The API returns the existing distribution when the bucket already has one.
    """

    settings, workspace, client = _session(ctx)
    _print(client.cdn.create_cdn_distribution(
        workspace_id=workspace,
        name=name,
        origin_id=origin_id,
        origin_type=origin_type,
        cache_policy=cache_policy,
        check_origin_public=check_origin,
        preflight_billing=preflight or settings.check_billing,
    ))


@app.command("get")
@handle_api_errors
def get_distribution(ctx: typer.Context, distribution_id: str = typer.Argument(..., help="Distribution ID")) -> None:
    """Show a CDN distribution."""

    _settings, workspace, client = _session(ctx)
    _print(client.cdn.get_cdn_distribution(distribution_id, workspace_id=workspace))


@app.command("update")
@handle_api_errors
def update_distribution(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    name: Optional[str] = typer.Option(None, "--name", help="1-128 characters"),
    cache_policy: Optional[str] = typer.Option(
        None, "--cache-policy", metavar="|".join(CDN_CACHE_POLICIES)
    ),
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled"),
) -> None:
    """Update a distribution's name, cache policy or enabled state (at least one)."""

    if name is None and cache_policy is None and enabled is None:
        raise typer.BadParameter("Provide at least one update option.")
    _settings, workspace, client = _session(ctx)
    _print(client.cdn.update_cdn_distribution(
        distribution_id, workspace_id=workspace, name=name, cache_policy=cache_policy, enabled=enabled
    ))


@app.command("delete")
@handle_api_errors
def delete_distribution(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a distribution, its DNS record and custom domains, and purge its cache."""

    settings, workspace, client = _session(ctx)
    dist = validate_required_text(distribution_id, field="distribution_id")
    confirm_destructive(
        settings,
        f"Delete CDN distribution '{dist}'? This removes its DNS record, all custom domains and purges its cache.",
        yes,
    )
    _print(client.cdn.delete_cdn_distribution(dist, workspace_id=workspace))


@app.command("metrics")
@handle_api_errors
def distribution_metrics(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    range_: str = typer.Option("24h", "--range", metavar="|".join(CDN_METRICS_RANGES), help="Time range"),
) -> None:
    """Show traffic, cache and response metrics. Not yet part of the published API contract;
    behaviour may change."""

    _settings, workspace, client = _session(ctx)
    _print(client.cdn.get_cdn_distribution_metrics(
        distribution_id,
        workspace_id=workspace,
        range=range_,
        request_options={"timeout_in_seconds": METRICS_TIMEOUT_SECONDS},
    ))


@website_app.command("get")
@handle_api_errors
def get_website(ctx: typer.Context, distribution_id: str = typer.Argument(..., help="Distribution ID")) -> None:
    """Show the static website configuration (not configured means disabled)."""

    settings, workspace, client = _session(ctx)
    try:
        _print(client.cdn.get_cdn_website_config(distribution_id, workspace_id=workspace))
        return
    except NotFoundError:
        # The portal treats a missing configuration as "disabled"; tell that apart from
        # a missing distribution.
        client.cdn.get_cdn_distribution(distribution_id, workspace_id=workspace)
    if settings.structured_output:
        print_json({"distribution_id": distribution_id, "enabled": False, "index_document": "index.html"})
        return
    typer.echo("Website hosting is not configured (disabled).")


@website_app.command("set")
@handle_api_errors
def set_website(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    index_document: str = typer.Option(
        "index.html",
        "--index-document",
        help="Object served for the site root and unknown paths (no leading '/', printable ASCII, <= 1024 bytes)",
    ),
) -> None:
    """Enable static website (SPA) delivery for a bucket origin."""

    _settings, workspace, client = _session(ctx)
    _print(client.cdn.update_cdn_website_config(distribution_id, workspace_id=workspace, index_document=index_document))


@website_app.command("delete")
@handle_api_errors
def delete_website(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Disable static website delivery."""

    settings, workspace, client = _session(ctx)
    dist = validate_required_text(distribution_id, field="distribution_id")
    confirm_destructive(settings, "Disable static website delivery?", yes)
    _print(client.cdn.delete_cdn_website_config(dist, workspace_id=workspace))


@domains_app.command("list")
@handle_api_errors
def list_domains(ctx: typer.Context, distribution_id: str = typer.Argument(..., help="Distribution ID")) -> None:
    """List a distribution's custom domains."""

    _settings, workspace, client = _session(ctx)
    _print(client.cdn.list_cdn_custom_domains(distribution_id, workspace_id=workspace))


def _dns_instructions(result: Any) -> list[str]:
    data = to_data(result)
    if not isinstance(data, dict):
        return []
    lines = []
    record = (data.get("validation") or {}).get("cname_record") if isinstance(data.get("validation"), dict) else None
    if isinstance(record, dict) and record.get("name"):
        lines.append(
            f"Add this DNS record: {record.get('type') or 'CNAME'} {record.get('name')} -> {record.get('value')}"
        )
    for item in data.get("instructions") or []:
        lines.append(f"  {item}")
    return lines


@domains_app.command("create")
@handle_api_errors
def create_domain(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    domain: str = typer.Argument(..., help="Host name with a subdomain, for example cdn.example.com"),
    preflight: bool = typer.Option(
        False,
        "--preflight-billing",
        help="Check CUSTOMDO-STD billing eligibility first (same as the global --check-billing)",
    ),
) -> None:
    """Add a custom domain (billed), then print the CNAME record to create."""

    settings, workspace, client = _session(ctx)
    result = client.cdn.create_cdn_custom_domain(
        distribution_id,
        workspace_id=workspace,
        domain=domain,
        preflight_billing=preflight or settings.check_billing,
    )
    _print(result)
    if not settings.structured_output:
        for line in _dns_instructions(result):
            typer.secho(line, err=True)
        name = normalize_cdn_domain(domain)
        typer.secho(
            f"Then run: ibee cdn domains verify {distribution_id} {name} --wait",
            fg=typer.colors.YELLOW,
            err=True,
        )


@domains_app.command("get")
@handle_api_errors
def get_domain(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    domain: str = typer.Argument(..., help="Custom domain"),
) -> None:
    """Show a custom domain and its validation record."""

    _settings, workspace, client = _session(ctx)
    _print(client.cdn.get_cdn_custom_domain(distribution_id, domain, workspace_id=workspace))


def _status(result: Any) -> str:
    data = to_data(result)
    return str((data or {}).get("status") or "").strip().lower() if isinstance(data, dict) else ""


@domains_app.command("verify")
@handle_api_errors
def verify_domain(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    domain: str = typer.Argument(..., help="Custom domain"),
    wait: bool = typer.Option(
        False, "--wait", help="Verify every 15 s until the domain is active or failed (default up to 600 s)"
    ),
    timeout: Optional[float] = timeout_option(DOMAIN_WAIT_TIMEOUT_SECONDS),
    poll_interval: Optional[float] = poll_interval_option(),
) -> None:
    """Check the domain's DNS and certificate. With --wait, exits 1 when it fails and 3 on timeout."""

    settings, workspace, client = _session(ctx)
    config = resolve_wait(
        wait,
        timeout,
        DOMAIN_POLL_INTERVAL_SECONDS if wait and poll_interval is None else poll_interval,
        default_timeout=DOMAIN_WAIT_TIMEOUT_SECONDS,
    )
    if config is None:
        result = client.cdn.verify_cdn_custom_domain(distribution_id, domain, workspace_id=workspace)
    else:
        try:
            result = client.cdn.wait_for_cdn_custom_domain(
                distribution_id,
                domain,
                workspace_id=workspace,
                timeout=config.timeout,
                poll_interval=config.poll_interval,
            )
        except OperationTimeoutError as exc:
            if exc.operation is not None and settings.structured_output:
                print_json(exc.operation)
            typer.secho(
                f"{exc.message}; check again with: ibee cdn domains verify {distribution_id} "
                f"{normalize_cdn_domain(domain)} --wait",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(code=EXIT_WAIT_TIMEOUT)
    _print(result)
    status = _status(result)
    if status == "active":
        if not settings.structured_output:
            typer.secho("Custom domain is active.", fg=typer.colors.GREEN, err=True)
        return
    if status == "failed":
        data = to_data(result)
        message = data.get("message") if isinstance(data, dict) else None
        typer.secho(f"Custom domain verification failed: {message or status}", fg=typer.colors.RED, err=True)
        if config is not None:
            raise typer.Exit(code=EXIT_FAILURE)
        return
    if not settings.structured_output:
        typer.secho(DOMAIN_PENDING_MESSAGE, fg=typer.colors.YELLOW, err=True)


@domains_app.command("delete")
@handle_api_errors
def delete_domain(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    domain: str = typer.Argument(..., help="Custom domain"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a custom domain."""

    settings, workspace, client = _session(ctx)
    validate_required_text(distribution_id, field="distribution_id")
    name = normalize_cdn_domain(domain)
    confirm_destructive(
        settings, f"Remove custom domain '{name}'? Also delete its CNAME record at your DNS provider.", yes
    )
    _print(client.cdn.delete_cdn_custom_domain(distribution_id, name, workspace_id=workspace))


def _split(values: Optional[List[str]]) -> Optional[List[str]]:
    """Repeatable selector values; each may also hold a comma- or newline-separated list."""

    if values is None:
        return None
    items: list[str] = []
    for value in values:
        items.extend(part for part in re.split(r"[,\n]", value))
    return items


@app.command("purge")
@handle_api_errors
def purge_cache(
    ctx: typer.Context,
    distribution_id: str = typer.Argument(..., help="Distribution ID"),
    mode: str = typer.Option(..., "--mode", metavar="|".join(CDN_PURGE_MODES), help="What to purge (required)"),
    path: Optional[List[str]] = typer.Option(
        None, "--path", help="--mode url: path or https:// URL (repeatable or comma-separated, 1-30)"
    ),
    hostname: Optional[List[str]] = typer.Option(
        None, "--hostname", help="--mode hostname: distribution host name (1-100)"
    ),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="--mode tag: cache tag (1-100)"),
    prefix: Optional[List[str]] = typer.Option(
        None, "--prefix", help="--mode prefix: path prefix without query or fragment (1-100)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation for --mode all"),
) -> None:
    """Purge cached content. --mode all asks for confirmation unless --yes.

    Exits 1 when the CDN reports the purge failed (common for prefix and tag purges).
    """

    settings, workspace, client = _session(ctx)
    dist = validate_required_text(distribution_id, field="distribution_id")
    selectors = {"url": ("paths", path, "--path"), "hostname": ("hostnames", hostname, "--hostname"),
                 "tag": ("tags", tag, "--tag"), "prefix": ("prefixes", prefix, "--prefix")}
    purge_mode = mode.strip().lower()
    if purge_mode in selectors and not selectors[purge_mode][1]:
        raise typer.BadParameter(f"--mode {purge_mode} requires at least one {selectors[purge_mode][2]}.")
    body = build_cdn_purge_body(
        purge_mode, paths=_split(path), hostnames=_split(hostname), tags=_split(tag), prefixes=_split(prefix)
    )
    if body["mode"] == "all":
        confirm_destructive(settings, f"{CDN_PURGE_ALL_WARNING}. Continue?", yes)
    kwargs = {key: value for key, value in body.items() if key != "mode"}
    _print(client.cdn.purge_cdn_cache(dist, workspace_id=workspace, mode=body["mode"], **kwargs))
