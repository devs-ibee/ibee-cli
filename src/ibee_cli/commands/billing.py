"""Billing admission commands."""

from __future__ import annotations

from typing import Optional

import typer

from ..context import get_client, get_settings, require_workspace
from ..helpers import check_billing_eligibility
from ..render import handle_api_errors, print_json

app = typer.Typer(help="Billing resource eligibility", no_args_is_help=True)


@app.command("eligibility")
@handle_api_errors
def resource_eligibility(
    ctx: typer.Context,
    sku_code: Optional[str] = typer.Option(
        None, "--sku-code", help="Billing SKU from an IBEE product catalog"
    ),
    estimated_cost_minor: Optional[int] = typer.Option(
        None,
        "--estimated-cost-minor",
        min=0,
        help="Optional catalog-derived cost in the currency's minor unit",
    ),
) -> None:
    """Check whether the workspace may create a billable resource."""

    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    decision = check_billing_eligibility(
        get_client(settings),
        workspace,
        sku_code=sku_code,
        estimated_cost_minor=estimated_cost_minor,
    )
    if decision is None:
        typer.secho(
            "The installed ibee SDK does not expose billing eligibility; upgrade the SDK.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    print_json(decision)
