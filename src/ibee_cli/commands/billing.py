"""Billing eligibility (a diagnostic: the real request is decided by the server)."""

from __future__ import annotations

from typing import Optional

import typer

from ..context import get_client, get_settings
from ..helpers import compact_payload, field
from ..render import emit, handle_api_errors, to_data

app = typer.Typer(help="Billing resource eligibility", no_args_is_help=True)


@app.command("eligibility")
@handle_api_errors
def resource_eligibility(
    ctx: typer.Context,
    sku_code: Optional[str] = typer.Option(None, "--sku-code", help="Billing SKU from an IBEE product catalog"),
    estimated_cost_minor: Optional[int] = typer.Option(None, "--estimated-cost-minor", help="Cost in the currency's minor unit"),
    operation: Optional[str] = typer.Option(None, "--operation", help="Billing operation, for example CREATE_RESOURCE"),
    resource_type: Optional[str] = typer.Option(None, "--resource-type", help="For example cpu_vm or block_volume"),
    quantity: Optional[int] = typer.Option(None, "--quantity"),
    size_gb: Optional[int] = typer.Option(None, "--size-gb"),
    require: bool = typer.Option(False, "--require", help="Exit 1 unless the answer is allowed=true"),
) -> None:
    """Ask Billing whether the workspace may create a billable resource.

    Prints Billing's decision as returned (JSON by default; -o table|yaml|id). Exits 0 for
    any decision, unless --require is given and allowed is not true.
    """
    settings = get_settings(ctx)
    decision = get_client(settings).billing.check_resource_eligibility(
        **compact_payload(
            sku_code=sku_code, estimated_cost_minor=estimated_cost_minor, operation=operation,
            resource_type=resource_type, quantity=quantity, size_gb=size_gb,
        )
    )
    data = to_data(decision)
    rows = list(data.items()) if isinstance(data, dict) else []
    emit(decision, settings=settings, default="json", title="Billing eligibility", headers=("Field", "Value"), rows=rows, id_field="organization_id")
    if require and field(decision, "allowed") is not True:
        raise typer.Exit(code=1)
