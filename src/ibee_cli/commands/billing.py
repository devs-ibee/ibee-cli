"""Billing admission commands."""

from __future__ import annotations

from typing import Any, Optional

import typer
from ibee.billing import TOPUP_GUIDANCE, billing_block_message, is_billing_topup_allowed
from ibee.validation import ENFORCEMENT_OPERATIONS, normalize_eligibility_operation

from ..context import get_client, get_settings, require_workspace
from ..helpers import check_billing_eligibility
from ..render import emit, handle_api_errors, to_data

app = typer.Typer(help="Billing resource eligibility", no_args_is_help=True)

_TABLE_FIELDS = (
    "allowed",
    "reason",
    "billing_state",
    "billing_mode",
    "currency",
    "sku_code",
    "estimated_cost_minor",
    "effective_balance_minor",
    "credit_headroom_minor",
)


def _operation(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return normalize_eligibility_operation(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--operation") from exc


def _rows(decision: Any) -> list[tuple[str, Any]]:
    data = to_data(decision)
    data = data if isinstance(data, dict) else {}
    rows = [(name, data.get(name)) for name in _TABLE_FIELDS]
    if data.get("allowed") is not True:
        rows.append(("message", billing_block_message(decision)))
    return rows


@app.command("eligibility")
@handle_api_errors
def resource_eligibility(
    ctx: typer.Context,
    sku_code: Optional[str] = typer.Option(
        None, "--sku-code", help="Billing SKU from an IBEE product catalog (1-64 characters)"
    ),
    estimated_cost_minor: Optional[int] = typer.Option(
        None,
        "--estimated-cost-minor",
        min=0,
        help="Optional catalog-derived cost in the currency's minor unit",
    ),
    operation: Optional[str] = typer.Option(
        None,
        "--operation",
        help=(
            "Billing operation to evaluate (case-insensitive): "
            + ", ".join(ENFORCEMENT_OPERATIONS)
            + ". Default CREATE_RESOURCE. Not yet part of the published API contract; "
            "behaviour may change."
        ),
    ),
    require: bool = typer.Option(
        False,
        "--require",
        help="Exit 1 unless billing answers allowed=true (the portal's create preflight)",
    ),
) -> None:
    """Check whether the workspace may create a billable resource.

    Prints the decision (JSON by default; -o table|yaml|id). Exits 0 even when the
    decision is a denial, unless --require is given.
    """

    settings = get_settings(ctx)
    normalized_operation = _operation(operation)
    workspace = require_workspace(settings)
    decision = check_billing_eligibility(
        get_client(settings),
        workspace,
        sku_code=sku_code,
        estimated_cost_minor=estimated_cost_minor,
        operation=normalized_operation,
    )
    if decision is None:
        typer.secho(
            "The installed ibee SDK does not expose billing eligibility; upgrade the SDK.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    emit(
        decision,
        settings=settings,
        default="json",
        title="Billing eligibility",
        headers=("Field", "Value"),
        rows=_rows(decision),
        id_field="organization_id",
    )
    data = to_data(decision)
    allowed = data.get("allowed") if isinstance(data, dict) else None
    if require and allowed is not True:
        typer.secho(
            f"Billing denied: {billing_block_message(decision)}", fg=typer.colors.RED, err=True
        )
        if is_billing_topup_allowed(decision):
            typer.secho(TOPUP_GUIDANCE, fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1)
