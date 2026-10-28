"""The CLI's one convenience: a VM create's ``billing_catalog`` from ``GET /compute/plans``.

The contract's ``ComputeBillingSelection`` (``billing_catalog`` of ``POST
/compute/cloud-vms`` and ``POST /compute/gpu-vms``) is the chosen plan's
``billing_catalog`` with one entry of its ``billing_options`` merged in. With
``--billing-interval HOURLY|MONTHLY``, ``vms create`` and ``gpus create`` do that with
the server's own data, as the portal does: read the ``--plan-id`` plan from ``GET
/compute/plans`` (``vm_type`` and ``site_id`` of the create), copy its
``billing_catalog`` and merge in the whole ``billing_options`` entry whose
``billing_interval`` is the one asked for (``billing_interval``, ``committed``,
``commitment_period``, ``commitment_months``, ``committed_hours``, ``price_unit``,
``unit_price_minor``...), keeping the catalog's ``price_currency``. Nothing is
invented or checked here: the server validates the selection and decides the price.
"""

from __future__ import annotations

from typing import Any, Optional

from .context import fail_usage
from .helpers import json_arg
from .render import items, to_data


def vm_billing_catalog(
    client: Any,
    workspace_id: str,
    vm_type: str,
    plan_id: str,
    site_id: Optional[str],
    billing_catalog: Optional[str],
    billing_interval: Optional[str],
) -> Any:
    """The ``billing_catalog`` to send: ``--billing-catalog`` as given, or built for ``--billing-interval``."""
    if billing_catalog is not None and billing_interval is not None:
        fail_usage("Use --billing-catalog or --billing-interval, not both.")
    if billing_catalog is not None:
        return json_arg(billing_catalog, "--billing-catalog")
    if billing_interval is None:
        fail_usage(
            "billing_catalog is required: pass --billing-interval HOURLY|MONTHLY (built from the plan in "
            "GET /compute/plans) or --billing-catalog JSON."
        )
    interval = str(billing_interval).strip().upper()
    answer = client.compute_catalog.list_compute_plans(
        workspace_id=workspace_id, vm_type=vm_type, **({"site_id": site_id} if site_id else {})
    )
    plans = items(to_data(answer), "plans") or []
    plan = next((p for p in plans if isinstance(p, dict) and str(p.get("plan_id")) == str(plan_id)), None)
    where = f"GET /compute/plans?vm_type={vm_type}" + (f"&site_id={site_id}" if site_id else "")
    if plan is None:
        fail_usage(f"Plan {plan_id} is not in {where}, so its billing_catalog cannot be read.")
    catalog = plan.get("billing_catalog")
    if not isinstance(catalog, dict):
        fail_usage(f"Plan {plan_id} in {where} has no billing_catalog.")
    options = [o for o in catalog.get("billing_options") or [] if isinstance(o, dict)]
    option = next((o for o in options if str(o.get("billing_interval")).upper() == interval), None)
    if option is None:
        offered = ", ".join(str(o.get("billing_interval")) for o in options) or "none"
        fail_usage(f"Plan {plan_id} has no {interval} entry in billing_catalog.billing_options (offered: {offered}).")
    selection = {**catalog, **option}
    if "price_currency" in catalog:
        selection["price_currency"] = catalog["price_currency"]
    return selection


__all__ = ["vm_billing_catalog"]
