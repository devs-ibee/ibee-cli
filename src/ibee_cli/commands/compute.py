"""Compute catalog commands: sites, plans, images (the IDs that `vms/gpus create` takes)."""

from __future__ import annotations

from typing import Optional

import typer

from ..context import get_client, get_settings
from ..helpers import compact_payload
from ..render import handle_api_errors, print_json

app = typer.Typer(help="Compute catalog: sites, plans, images", no_args_is_help=True)


@app.command("sites")
@handle_api_errors
def list_sites(ctx: typer.Context) -> None:
    """List sites where cloud and GPU VMs can be placed."""
    print_json(get_client(get_settings(ctx)).compute_catalog.list_compute_sites())


@app.command("plans")
@handle_api_errors
def list_plans(
    ctx: typer.Context,
    vm_type: str = typer.Option(..., "--vm-type", help="cloud or gpu"),
    site_id: Optional[str] = typer.Option(None, "--site-id", help="Filter by site"),
    currency: Optional[str] = typer.Option(None, "--currency", help="Price currency (e.g. INR, USD)"),
    billing_interval: Optional[str] = typer.Option(None, "--billing-interval", help="HOURLY or MONTHLY"),
) -> None:
    """List billable plans (use the IDs with `vms/gpus create --plan-id`)."""
    catalog = get_client(get_settings(ctx)).compute_catalog
    print_json(catalog.list_compute_plans(vm_type=vm_type, **compact_payload(site_id=site_id, currency=currency, billing_interval=billing_interval)))


@app.command("images")
@handle_api_errors
def list_images(
    ctx: typer.Context,
    vm_type: str = typer.Option(..., "--vm-type", help="cloud or gpu"),
    site_id: Optional[str] = typer.Option(None, "--site-id", help="Filter by site"),
) -> None:
    """List OS images (use the IDs with `vms/gpus create --template-id`)."""
    catalog = get_client(get_settings(ctx)).compute_catalog
    print_json(catalog.list_compute_images(vm_type=vm_type, **compact_payload(site_id=site_id)))
