"""Billing eligibility commands."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Optional

import httpx
import typer
from ibee.environment import IbeeEnvironment

from ..context import get_client, get_settings, require_token, require_workspace
from ..render import handle_api_errors, print_json, print_table

app = typer.Typer(help="Check billing eligibility before creating resources", no_args_is_help=True)


def _base_url(settings) -> str:
    if settings.base_url:
        return settings.base_url.rstrip("/")
    env = IbeeEnvironment.DEVELOPMENT if settings.dev else IbeeEnvironment.DEFAULT
    return env.value.rstrip("/")


def _check_eligibility(
    ctx: typer.Context,
    *,
    sku_code: Optional[str],
    estimated_cost_minor: Optional[int],
):
    """Use the SDK when available, with a fallback for earlier SDK releases."""
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    client = get_client(settings)
    billing = getattr(client, "billing", None)
    check = getattr(billing, "check_resource_eligibility", None)
    if check is not None:
        return check(
            workspace_id=workspace,
            sku_code=sku_code,
            estimated_cost_minor=estimated_cost_minor,
        )

    payload = {
        key: value
        for key, value in {
            "sku_code": sku_code,
            "estimated_cost_minor": estimated_cost_minor,
        }.items()
        if value is not None
    }
    response = httpx.post(
        f"{_base_url(settings)}/billing/resource-eligibility",
        params={"workspace_id": workspace},
        json=payload,
        headers={
            "Authorization": f"Bearer {require_token(settings)}",
            "accept": "application/json",
            "content-type": "application/json",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        typer.secho(
            f"API error {response.status_code}: {response.text[:300]}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    return response.json()


def _field(result, name: str):
    if isinstance(result, Mapping):
        return result.get(name)
    return getattr(result, name)


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
    result = _check_eligibility(
        ctx,
        sku_code=sku_code,
        estimated_cost_minor=estimated_cost_minor,
    )

    if settings.as_json:
        print_json(result)
        return

    allowed = bool(_field(result, "allowed"))
    typer.secho(
        f"Billing eligibility: {'ALLOWED' if allowed else 'DENIED'}",
        fg=typer.colors.GREEN if allowed else typer.colors.RED,
    )
    print_table(
        "Billing eligibility",
        ["Field", "Value"],
        [
            ("Reason", _field(result, "reason")),
            ("Billing mode", _field(result, "billing_mode")),
            ("Billing state", _field(result, "billing_state")),
            ("Currency", _field(result, "currency")),
            ("SKU", _field(result, "sku_code")),
            ("Estimated cost (minor)", _field(result, "estimated_cost_minor")),
            ("Effective balance (minor)", _field(result, "effective_balance_minor")),
            ("Credit headroom (minor)", _field(result, "credit_headroom_minor")),
            ("Evaluated at", _field(result, "evaluated_at")),
        ],
    )
