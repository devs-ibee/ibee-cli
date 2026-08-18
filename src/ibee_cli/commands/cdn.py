"""CDN distribution, website, custom-domain, URL, and purge commands."""

from __future__ import annotations

from typing import List, Optional
from urllib.parse import quote

import typer

from ..context import api_request, get_settings
from ..helpers import compact_payload
from ..render import handle_api_errors, print_json

app = typer.Typer(help="CDN distributions and delivery configuration", no_args_is_help=True)
website_app = typer.Typer(help="Static website configuration", no_args_is_help=True)
domains_app = typer.Typer(help="Distribution custom domains", no_args_is_help=True)
app.add_typer(website_app, name="website")
app.add_typer(domains_app, name="domains")


def _segment(value: str) -> str:
    return quote(value, safe="")


def _call(ctx: typer.Context, method: str, path: str, *, payload: dict | None = None) -> None:
    result = api_request(get_settings(ctx), method, path, json_body=payload)
    if result is not None:
        print_json(result)


@app.command("generate-url")
@handle_api_errors
def generate_url(ctx: typer.Context, bucket_name: str, object_key: str,
                 expires_in: Optional[int] = typer.Option(None, "--expires-in", min=1),
                 disposition: Optional[str] = typer.Option(None, "--disposition")) -> None:
    _call(ctx, "POST", "cdn/generate-url", payload=compact_payload(
        bucket_name=bucket_name, object_key=object_key,
        expires_in=expires_in, disposition=disposition,
    ))


@app.command("list")
@handle_api_errors
def list_distributions(ctx: typer.Context) -> None:
    _call(ctx, "GET", "cdn/distributions")


@app.command("create")
@handle_api_errors
def create_distribution(ctx: typer.Context, name: str,
                        origin_id: str = typer.Option(..., "--origin-id"),
                        origin_type: str = typer.Option("bucket", "--origin-type"),
                        cache_policy: str = typer.Option("static-assets", "--cache-policy")) -> None:
    _call(ctx, "POST", "cdn/distributions", payload={
        "name": name, "origin_id": origin_id, "origin_type": origin_type,
        "cache_policy": cache_policy,
    })


@app.command("get")
@handle_api_errors
def get_distribution(ctx: typer.Context, distribution_id: str) -> None:
    _call(ctx, "GET", f"cdn/distributions/{_segment(distribution_id)}")


@app.command("update")
@handle_api_errors
def update_distribution(ctx: typer.Context, distribution_id: str,
                        name: Optional[str] = typer.Option(None, "--name"),
                        cache_policy: Optional[str] = typer.Option(None, "--cache-policy"),
                        enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled")) -> None:
    payload = compact_payload(name=name, cache_policy=cache_policy, enabled=enabled)
    if not payload:
        raise typer.BadParameter("Provide at least one update option.")
    _call(ctx, "PATCH", f"cdn/distributions/{_segment(distribution_id)}", payload=payload)


@app.command("delete")
@handle_api_errors
def delete_distribution(ctx: typer.Context, distribution_id: str,
                        yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    if not yes:
        typer.confirm(f"Delete CDN distribution '{distribution_id}'?", abort=True)
    _call(ctx, "DELETE", f"cdn/distributions/{_segment(distribution_id)}")


@website_app.command("get")
@handle_api_errors
def get_website(ctx: typer.Context, distribution_id: str) -> None:
    _call(ctx, "GET", f"cdn/distributions/{_segment(distribution_id)}/website-config")


@website_app.command("set")
@handle_api_errors
def set_website(ctx: typer.Context, distribution_id: str,
                index_document: str = typer.Option("index.html", "--index-document")) -> None:
    _call(ctx, "PUT", f"cdn/distributions/{_segment(distribution_id)}/website-config",
          payload={"index_document": index_document})


@website_app.command("delete")
@handle_api_errors
def delete_website(ctx: typer.Context, distribution_id: str,
                   yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    if not yes:
        typer.confirm("Disable static website delivery?", abort=True)
    _call(ctx, "DELETE", f"cdn/distributions/{_segment(distribution_id)}/website-config")


@domains_app.command("list")
@handle_api_errors
def list_domains(ctx: typer.Context, distribution_id: str) -> None:
    _call(ctx, "GET", f"cdn/distributions/{_segment(distribution_id)}/custom-domains")


@domains_app.command("create")
@handle_api_errors
def create_domain(ctx: typer.Context, distribution_id: str, domain: str) -> None:
    _call(ctx, "POST", f"cdn/distributions/{_segment(distribution_id)}/custom-domains",
          payload={"domain": domain})


@domains_app.command("get")
@handle_api_errors
def get_domain(ctx: typer.Context, distribution_id: str, domain: str) -> None:
    _call(ctx, "GET", f"cdn/distributions/{_segment(distribution_id)}/custom-domains/{_segment(domain)}")


@domains_app.command("verify")
@handle_api_errors
def verify_domain(ctx: typer.Context, distribution_id: str, domain: str) -> None:
    _call(ctx, "POST", f"cdn/distributions/{_segment(distribution_id)}/custom-domains/{_segment(domain)}/verify")


@domains_app.command("delete")
@handle_api_errors
def delete_domain(ctx: typer.Context, distribution_id: str, domain: str,
                  yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    if not yes:
        typer.confirm(f"Remove custom domain '{domain}'?", abort=True)
    _call(ctx, "DELETE", f"cdn/distributions/{_segment(distribution_id)}/custom-domains/{_segment(domain)}")


@app.command("purge")
@handle_api_errors
def purge_cache(ctx: typer.Context, distribution_id: str,
                mode: str = typer.Option("all", "--mode"),
                path: Optional[List[str]] = typer.Option(None, "--path"),
                hostname: Optional[List[str]] = typer.Option(None, "--hostname"),
                tag: Optional[List[str]] = typer.Option(None, "--tag"),
                prefix: Optional[List[str]] = typer.Option(None, "--prefix")) -> None:
    fields = {"url": path, "hostname": hostname, "tag": tag, "prefix": prefix}
    if mode not in (*fields, "all"):
        raise typer.BadParameter("--mode must be url, hostname, tag, prefix, or all.")
    if mode == "all" and any(fields.values()):
        raise typer.BadParameter("--mode all does not accept selector options.")
    if mode != "all" and not fields[mode]:
        raise typer.BadParameter(f"--mode {mode} requires at least one --{mode if mode != 'url' else 'path'}.")
    payload = {"mode": mode}
    if mode != "all":
        payload[{"url": "paths", "hostname": "hostnames", "tag": "tags", "prefix": "prefixes"}[mode]] = fields[mode]
    _call(ctx, "POST", f"cdn/distributions/{_segment(distribution_id)}/purge", payload=payload)
