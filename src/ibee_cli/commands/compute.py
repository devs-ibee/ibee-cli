"""Compute catalog (discovery) commands: sites, plans, images.

These back the choices needed to create a VM (`--plan-id`, `--template-id`,
placement). They call the SDK's ``compute_catalog`` resource when the installed
``ibee`` exposes it (>= 0.2.0); on older SDKs they fall back to a direct GET
against the same gateway, so the commands work regardless of SDK version.
"""

from __future__ import annotations

from typing import Optional

import httpx
import typer
from ibee.environment import IbeeEnvironment

from ..context import get_client, get_settings, require_token, require_workspace
from ..render import handle_api_errors, print_json

app = typer.Typer(help="Compute catalog: sites, plans, images", no_args_is_help=True)


def _base_url(settings) -> str:
    if settings.base_url:
        return settings.base_url.rstrip("/")
    env = IbeeEnvironment.DEVELOPMENT if settings.dev else IbeeEnvironment.DEFAULT
    return env.value.rstrip("/")


def _fetch(ctx: typer.Context, sdk_method: str, path: str, filters: dict):
    """Return catalog data via the SDK method if available, else a direct GET."""
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    client = get_client(settings)
    params = {k: v for k, v in filters.items() if v is not None}

    catalog = getattr(client, "compute_catalog", None)
    if catalog is not None:
        return getattr(catalog, sdk_method)(workspace_id=workspace, **params)

    # Fallback for SDKs without compute_catalog (< 0.2.0): same gateway + auth.
    token = require_token(settings)
    resp = httpx.get(
        f"{_base_url(settings)}/{path}",
        params={"workspace_id": workspace, **params},
        headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
        timeout=30,
    )
    if resp.status_code >= 400:
        typer.secho(f"API error {resp.status_code}: {resp.text[:300]}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    return resp.json()


@app.command("sites")
@handle_api_errors
def list_sites(
    ctx: typer.Context,
    region_id: Optional[str] = typer.Option(None, "--region-id", help="Filter by region"),
    country_id: Optional[str] = typer.Option(None, "--country-id", help="Filter by country"),
) -> None:
    """List sites where cloud and GPU VMs can be placed."""
    result = _fetch(ctx, "list_compute_sites", "compute/sites",
                    {"region_id": region_id, "country_id": country_id})
    print_json(result)


@app.command("plans")
@handle_api_errors
def list_plans(
    ctx: typer.Context,
    vm_type: Optional[str] = typer.Option(None, "--vm-type", help="cloud or gpu"),
    site_id: Optional[str] = typer.Option(None, "--site-id", help="Filter by site"),
    currency: Optional[str] = typer.Option(None, "--currency", help="Price currency (e.g. INR, USD)"),
    billing_interval: Optional[str] = typer.Option(None, "--billing-interval", help="e.g. hourly, monthly"),
) -> None:
    """List billable plans for cloud or GPU VMs (use the IDs with `vms/gpus create --plan-id`)."""
    result = _fetch(ctx, "list_compute_plans", "compute/plans", {
        "vm_type": vm_type, "site_id": site_id,
        "currency": currency, "billing_interval": billing_interval,
    })
    print_json(result)


@app.command("images")
@handle_api_errors
def list_images(
    ctx: typer.Context,
    vm_type: Optional[str] = typer.Option(None, "--vm-type", help="cloud or gpu"),
    currency: Optional[str] = typer.Option(None, "--currency", help="Price currency (e.g. INR, USD)"),
) -> None:
    """List OS templates/images (use the IDs with `vms/gpus create --template-id`)."""
    result = _fetch(ctx, "list_compute_images", "compute/images",
                    {"vm_type": vm_type, "currency": currency})
    print_json(result)
