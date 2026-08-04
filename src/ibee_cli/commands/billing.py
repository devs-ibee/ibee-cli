"""Billing eligibility commands."""

from __future__ import annotations

from typing import Optional

import typer

from ..context import get_client, get_settings, require_workspace
from ..render import handle_api_errors, print_json, print_table

app = typer.Typer(help="Check billing eligibility before creating resources", no_args_is_help=True)


@app.command("eligibility")
@handle_api_errors
def check_resource_eligibility(
    ctx: typer.Context,
    sku_code: Optional[str] = typer.Option(
        None,
        "--sku-code",
        help="Optional product SKU to evaluate",
    ),
    estimated_cost_minor: Optional[int] = typer.Option(
        None,
        "--estimated-cost-minor",
        min=0,
        help="Optional estimated cost in the currency's minor unit",
    ),
) -> None:
    """Check whether billing currently allows a proposed resource creation.

    This is a point-in-time preflight. It does not reserve funds or guarantee
    that a later product operation will succeed.
    """
    settings = get_settings(ctx)
    client = get_client(settings)
    result = client.billing.check_resource_eligibility(
        workspace_id=require_workspace(settings),
        sku_code=sku_code,
        estimated_cost_minor=estimated_cost_minor,
    )

    if settings.as_json:
        print_json(result)
        return

    allowed = bool(result.allowed)
    typer.secho(
        f"Billing eligibility: {'ALLOWED' if allowed else 'DENIED'}",
        fg=typer.colors.GREEN if allowed else typer.colors.RED,
    )
    print_table(
        "Billing eligibility",
        ["Field", "Value"],
        [
            ("Reason", result.reason),
            ("Billing mode", result.billing_mode),
            ("Billing state", result.billing_state),
            ("Currency", result.currency),
            ("SKU", result.sku_code),
            ("Estimated cost (minor)", result.estimated_cost_minor),
            ("Effective balance (minor)", result.effective_balance_minor),
            ("Credit headroom (minor)", result.credit_headroom_minor),
            ("Evaluated at", result.evaluated_at),
        ],
    )
