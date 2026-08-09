"""Shared helpers for write / async-operation commands.

Compute writes (VM create/delete/power, access, resize, and volume actions)
return `202 + operation_id`; these helpers generate idempotency keys,
optionally poll the operation to a terminal state (`--wait`), and parse secret
values from the command line.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import typer

from .render import print_json

# Terminal states for a compute operation (see OperationStatusStatus). Keep
# ``completed`` as a compatibility alias for older SDK/backend releases.
_SUCCEEDED = {"succeeded", "completed"}
_TERMINAL = _SUCCEEDED | {"failed", "cancelled", "timed_out"}


def _field(value: Any, *names: str) -> Any:
    """Read a response field from either a generated model or a mapping."""

    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def response_items(result: Any, *wrapper_fields: str) -> list[Any]:
    """Normalize SDK collection responses.

    Fern returns some list operations as a bare list while older IBEE SDK
    releases used wrapper models.  Commands should render both forms.
    """

    if isinstance(result, Sequence) and not isinstance(
        result, (str, bytes, bytearray)
    ):
        return list(result)
    for field in wrapper_fields:
        items = _field(result, field)
        if items is not None:
            return list(items)
    return []


def check_billing_eligibility(
    client: Any,
    workspace_id: str,
    *,
    sku_code: str | None = None,
    estimated_cost_minor: int | None = None,
) -> Any:
    """Call the typed SDK billing preflight and fail closed if unavailable."""

    billing = getattr(client, "billing", None)
    method = getattr(billing, "check_resource_eligibility", None)
    if not callable(method):
        typer.secho(
            "Creation blocked: the installed IBEE SDK does not support "
            "billing eligibility. Upgrade the SDK and retry.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    kwargs: dict[str, Any] = {"workspace_id": workspace_id}
    if sku_code:
        kwargs["sku_code"] = sku_code
    if estimated_cost_minor is not None:
        kwargs["estimated_cost_minor"] = estimated_cost_minor
    return method(**kwargs)


def require_billing_eligibility(
    client: Any,
    workspace_id: str,
    *,
    sku_code: str | None = None,
    estimated_cost_minor: int | None = None,
) -> Any:
    """Fail before a billable create when the typed preflight denies it."""

    decision = check_billing_eligibility(
        client,
        workspace_id,
        sku_code=sku_code,
        estimated_cost_minor=estimated_cost_minor,
    )
    allowed = _field(decision, "allowed", "can_create", "canCreate")
    if allowed is True:
        return decision
    reason = _field(decision, "reason") or "billing admission denied"
    sku_suffix = f" (SKU {sku_code})" if sku_code else ""
    if allowed is False:
        typer.secho(
            f"Creation blocked by billing eligibility: {reason}{sku_suffix}.",
            fg=typer.colors.RED,
            err=True,
        )
    else:
        typer.secho(
            "Creation blocked: billing eligibility returned an invalid response.",
            fg=typer.colors.RED,
            err=True,
        )
    raise typer.Exit(code=1)


def compute_plan_sku(
    client: Any,
    workspace_id: str,
    *,
    vm_type: str,
    site_id: str | None,
    plan_id: str,
) -> str | None:
    """Resolve a VM plan to the billing SKU supplied by the compute catalog."""

    catalog = getattr(client, "compute_catalog", None)
    method = getattr(catalog, "list_compute_plans", None)
    if not callable(method):
        return None
    result = method(workspace_id=workspace_id, vm_type=vm_type, site_id=site_id)
    for plan in response_items(result, "plans", "items"):
        if str(_field(plan, "plan_id", "id") or "") != plan_id:
            continue
        sku = _field(plan, "sku_code", "code", "plan_code")
        return str(sku).strip() if sku else None
    return None


def preflight_compute_plan(
    client: Any,
    workspace_id: str,
    *,
    vm_type: str,
    site_id: str | None,
    plan_id: str,
) -> Any:
    """Require a trusted catalog SKU and preflight a VM create."""

    sku_code = compute_plan_sku(
        client,
        workspace_id,
        vm_type=vm_type,
        site_id=site_id,
        plan_id=plan_id,
    )
    if not sku_code:
        typer.secho(
            f"Creation blocked: billing SKU for compute plan {plan_id!r} "
            "could not be resolved from the catalog.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    return require_billing_eligibility(client, workspace_id, sku_code=sku_code)


def new_idempotency_key(action: str, ident: str = "") -> str:
    """A unique, human-readable idempotency key for a write call."""
    suffix = f"-{ident}" if ident else ""
    return f"cli-{action}{suffix}-{uuid.uuid4().hex[:8]}"


def parse_value(raw: str) -> dict:
    """Parse a secret value into a dict.

    Accepts either a JSON object (``'{"url":"..."}'``) or comma-separated
    ``key=value`` pairs (``'user=admin,pass=s3cr3t'``).
    """
    raw = (raw or "").strip()
    if not raw:
        raise typer.BadParameter("A value is required.")
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"value is not valid JSON: {exc}")
        if not isinstance(data, dict):
            raise typer.BadParameter("value must be a JSON object")
        return data
    out: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" not in pair:
            raise typer.BadParameter(f"expected key=value pairs, got {pair!r}")
        key, val = pair.split("=", 1)
        out[key.strip()] = val.strip()
    return out


def parse_json_object(raw: str, option_name: str) -> dict:
    """Parse an option containing a JSON object."""

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{option_name} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise typer.BadParameter(f"{option_name} must be a JSON object")
    return data


def parse_json_array(raw: str, option_name: str) -> list:
    """Parse an option containing a JSON array."""

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{option_name} is not valid JSON: {exc}")
    if not isinstance(data, list):
        raise typer.BadParameter(f"{option_name} must be a JSON array")
    return data


def compact_payload(**values):
    """Drop options the user did not supply while retaining false and zero."""

    return {key: value for key, value in values.items() if value is not None}


def wait_for_operation(client, workspace_id: str, operation_id: str,
                       timeout: float = 300.0, interval: float = 3.0):
    """Poll a compute operation until it reaches a terminal state or times out.

    Uses ``cloud_vms.get_compute_operation`` which resolves both cloud-VM and
    GPU-VM operations. Returns the last ``OperationStatus`` observed.
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.cloud_vms.get_compute_operation(
            operation_id=operation_id, workspace_id=workspace_id
        )
        status = getattr(last, "status", None)
        status = getattr(status, "value", status)
        if status in _TERMINAL:
            return last
        time.sleep(interval)
    return last


def finish_operation(settings, client, workspace_id: str, result, action: str,
                     ident: str, wait: bool) -> None:
    """Render an accepted async operation, optionally waiting for completion.

    Exits non-zero if a waited-for operation ends in ``failed``.
    """
    op_id = getattr(result, "operation_id", None) or getattr(result, "id", None)

    if wait and op_id:
        final = wait_for_operation(client, workspace_id, op_id)
        status = getattr(final, "status", None)
        status = getattr(status, "value", status)
        if settings.as_json:
            print_json(final)
        if status in _SUCCEEDED:
            typer.secho(f"{action} completed for {ident} (operation {op_id}).",
                        fg=typer.colors.GREEN)
        elif status == "failed":
            err = getattr(final, "error_message", None) or getattr(final, "error_code", None) or ""
            typer.secho(f"{action} failed for {ident}: {err} (operation {op_id}).",
                        fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        else:
            typer.secho(
                f"{action} still {status} for {ident} after wait "
                f"(operation {op_id}); poll: ibee ops get {op_id}",
                fg=typer.colors.YELLOW,
            )
        return

    if settings.as_json:
        print_json(result)
        return
    tail = f" (operation {op_id}) — poll: ibee ops get {op_id}" if op_id else ""
    typer.secho(f"{action} accepted for {ident}{tail}", fg=typer.colors.GREEN)
