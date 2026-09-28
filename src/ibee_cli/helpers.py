"""Shared helpers for write / async-operation commands.

Compute writes (VM create/delete/power, access, resize, and volume actions)
return `202 + operation_id`; these helpers build idempotency keys, optionally
poll the operation to a terminal state (`--wait`), run the optional billing
preflight (`--check-billing`), ask for confirmation of destructive actions,
and parse structured values from the command line.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

import typer
from ibee.errors import BillingDeniedError, OperationFailedError, OperationTimeoutError
from ibee.idempotency import build_idempotency_key
from ibee.operations import FAILURE_STATUSES, SUCCESS_STATUSES, poll_until
from ibee.validation import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_WAIT_TIMEOUT_SECONDS,
    MAX_POLL_INTERVAL_SECONDS,
    MAX_WAIT_TIMEOUT_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    MIN_WAIT_TIMEOUT_SECONDS,
    IbeeValidationError,
    normalize_estimated_cost_minor,
    normalize_sku_code,
    validate_idempotency_key,
    validate_operation_id,
    validate_poll_interval,
    validate_wait_timeout,
)

from .render import (
    EXIT_FAILURE,
    EXIT_WAIT_TIMEOUT,
    print_json,
    print_structured,
    retry_hint,
)

# Terminal states for a compute operation. ``completed`` is kept as a legacy
# alias for ``succeeded``.
_SUCCEEDED = set(SUCCESS_STATUSES)
_TERMINAL = _SUCCEEDED | set(FAILURE_STATUSES)

# Indirections so tests can replace time.
_sleep = time.sleep
_clock = time.monotonic


def _field(value: Any, *names: str) -> Any:
    """Read a response field from either a generated model or a mapping."""

    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _status_text(value: Any) -> str:
    status = _field(value, "status")
    status = getattr(status, "value", status)
    return str(status if status is not None else "").strip().lower()


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


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


def confirm_destructive(settings: Any, prompt: str, yes: bool = False) -> None:
    """Proceed when ``--yes`` (command or global) or ``IBEE_ASSUME_YES`` is set; otherwise ask.

    Declining exits 1.
    """

    if yes or getattr(settings, "assume_yes", False):
        return
    typer.confirm(prompt, abort=True)


# ---------------------------------------------------------------------------
# Idempotency keys
# ---------------------------------------------------------------------------


def idempotency_key_option() -> Any:
    """Shared ``--idempotency-key`` option for keyed writes."""

    return typer.Option(
        None,
        "--idempotency-key",
        metavar="KEY",
        help="Reuse a key to retry safely (1-128 printable ASCII; default: generated)",
    )


def new_idempotency_key(action: str, ident: str = "") -> str:
    """A unique idempotency key for a write call (portal key format, ``cli-`` scope)."""

    return build_idempotency_key(f"cli-{action}", ident)


def resolve_idempotency_key(explicit: Optional[str], action: str, ident: str = "") -> str:
    """Validate a user-supplied key, or generate one for this call."""

    if explicit is not None:
        return validate_idempotency_key(explicit)
    return new_idempotency_key(action, ident)


# ---------------------------------------------------------------------------
# Waiting for asynchronous operations
# ---------------------------------------------------------------------------


def timeout_option() -> Any:
    """Shared ``--timeout`` option (seconds) for ``--wait``."""

    return typer.Option(
        None,
        "--timeout",
        metavar="SECONDS",
        show_default=False,
        help=(
            f"Seconds to wait ({MIN_WAIT_TIMEOUT_SECONDS:g}-{MAX_WAIT_TIMEOUT_SECONDS:g}, "
            f"default {DEFAULT_WAIT_TIMEOUT_SECONDS:g}; used with --wait)"
        ),
    )


def poll_interval_option() -> Any:
    """Shared ``--poll-interval`` option (seconds) for ``--wait``."""

    return typer.Option(
        None,
        "--poll-interval",
        metavar="SECONDS",
        show_default=False,
        help=(
            f"Seconds between polls ({MIN_POLL_INTERVAL_SECONDS:g}-"
            f"{MAX_POLL_INTERVAL_SECONDS:g}, default {DEFAULT_POLL_INTERVAL_SECONDS:g}; used with --wait)"
        ),
    )


@dataclass(frozen=True)
class WaitConfig:
    timeout: float
    poll_interval: float


def resolve_wait(
    wait: bool,
    timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
    *,
    require_wait: bool = True,
) -> Optional[WaitConfig]:
    """Validate ``--wait``/``--timeout``/``--poll-interval`` before any request.

    Returns ``None`` when not waiting. ``--timeout``/``--poll-interval`` without
    ``--wait`` exit 2 (unless ``require_wait`` is false, as for ``ops wait``).
    """

    if not wait and require_wait:
        if timeout is not None or poll_interval is not None:
            raise typer.BadParameter("--timeout and --poll-interval require --wait.")
        return None
    wait_timeout = validate_wait_timeout(
        DEFAULT_WAIT_TIMEOUT_SECONDS if timeout is None else timeout
    )
    interval = DEFAULT_POLL_INTERVAL_SECONDS if poll_interval is None else poll_interval
    if poll_interval is None:
        interval = min(interval, wait_timeout)
    return WaitConfig(wait_timeout, validate_poll_interval(interval, wait_timeout))


def wait_for_operation(
    client: Any,
    workspace_id: str,
    operation_id: str,
    timeout: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    *,
    raise_on_failure: bool = False,
) -> Any:
    """Poll a compute operation until it reaches a terminal state.

    Uses ``cloud_vms.get_compute_operation`` (which resolves both cloud-VM and
    GPU-VM operations) through the SDK's polling engine: up to two consecutive
    transient poll failures are tolerated, 404 aborts at once. Returns the final
    ``OperationStatus``; raises ``OperationTimeoutError`` when ``timeout`` elapses and,
    with ``raise_on_failure``, ``OperationFailedError`` for failed, cancelled or
    timed-out operations.
    """

    op_id = validate_operation_id(operation_id)

    def fetch() -> Any:
        return client.cloud_vms.get_compute_operation(
            operation_id=op_id, workspace_id=workspace_id
        )

    try:
        return poll_until(
            fetch,
            lambda op: _status_text(op),
            timeout=timeout,
            poll_interval=interval,
            sleep=_sleep,
            clock=_clock,
            operation_id=op_id,
        )
    except OperationFailedError as exc:
        if raise_on_failure:
            raise
        return exc.operation


def _operation_failure_text(final: Any, action: str, ident: str, op_id: str) -> str:
    status = _status_text(final)
    code = _field(final, "error_code")
    message = _field(final, "error_message")
    detail = ": ".join(str(part) for part in (code, message) if part)
    text = f"{action} {status} for {ident}"
    if detail:
        text += f": {detail}"
    return f"{text} (operation {op_id})"


def finish_operation(
    settings: Any,
    client: Any,
    workspace_id: str,
    result: Any,
    action: str,
    ident: str,
    wait: Any,
    *,
    timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
    idempotency_key: Optional[str] = None,
) -> None:
    """Render an accepted async operation, optionally waiting for completion.

    ``wait`` is a bool or a :class:`WaitConfig`. Exit codes: 1 when a waited-for
    operation ends ``failed``, ``cancelled`` or ``timed_out``; 3 when the wait
    times out while the operation is still running.
    """

    op_id = _field(result, "operation_id") or _field(result, "id")
    config = wait if isinstance(wait, WaitConfig) else resolve_wait(bool(wait), timeout, poll_interval)
    mode = getattr(settings, "output", None)

    if config is not None and op_id:
        try:
            final = wait_for_operation(
                client, workspace_id, op_id, config.timeout, config.poll_interval
            )
        except OperationTimeoutError as exc:
            status = exc.last_status or "pending"
            typer.secho(
                f"{action} still {status} for {ident} after {config.timeout:g}s; "
                f"resume with: ibee ops wait {op_id}",
                fg=typer.colors.YELLOW,
                err=True,
            )
            retry_hint(idempotency_key)
            raise typer.Exit(code=EXIT_WAIT_TIMEOUT)
        status = _status_text(final)
        if mode in ("json", "yaml", "id"):
            print_structured(final, mode)
        if status in _SUCCEEDED:
            if mode not in ("json", "yaml", "id"):
                typer.secho(
                    f"{action} completed for {ident} (operation {op_id}).",
                    fg=typer.colors.GREEN,
                )
            return
        typer.secho(
            _operation_failure_text(final, action, ident, op_id),
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=EXIT_FAILURE)

    if mode == "id":
        typer.echo(str(op_id) if op_id else "")
        return
    if mode in ("json", "yaml"):
        print_structured(result, mode)
        return
    tail = f" (operation {op_id}) — poll: ibee ops wait {op_id}" if op_id else ""
    typer.secho(f"{action} accepted for {ident}{tail}", fg=typer.colors.GREEN)


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------


def check_billing_eligibility(
    client: Any,
    workspace_id: str,
    *,
    sku_code: str | None = None,
    estimated_cost_minor: int | float | None = None,
    operation: str | None = None,
) -> Any:
    """Call the typed SDK billing check and fail closed if unavailable."""

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
    sku = normalize_sku_code(sku_code)
    if sku:
        kwargs["sku_code"] = sku
    cost = normalize_estimated_cost_minor(estimated_cost_minor)
    if cost is not None:
        kwargs["estimated_cost_minor"] = cost
    if operation is not None:
        kwargs["operation"] = operation
    return method(**kwargs)


def preflight_billing(
    settings: Any,
    client: Any,
    workspace_id: str,
    *,
    sku_code: Optional[str] = None,
    estimated_cost_minor: Optional[int | float] = None,
    resource_type: str = "resource",
) -> Any:
    """Portal billing preflight before a billable create, when ``--check-billing`` is set.

    Without ``--check-billing`` (or ``IBEE_CHECK_BILLING``) nothing is called, so a
    create stays a single request and the API edge alone decides admission. With it,
    the create continues only when billing answers ``allowed`` exactly ``true``;
    otherwise ``BillingDeniedError`` is raised (printed with the portal wording, exit 1).
    """

    if not getattr(settings, "check_billing", False):
        return None
    billing = getattr(client, "billing", None)
    require = getattr(billing, "require_resource_eligibility", None)
    if callable(require):
        kwargs: dict[str, Any] = {"workspace_id": workspace_id, "resource_type": resource_type}
        sku = normalize_sku_code(sku_code)
        if sku:
            kwargs["sku_code"] = sku
        cost = normalize_estimated_cost_minor(estimated_cost_minor)
        if cost is not None:
            kwargs["estimated_cost_minor"] = cost
        return require(**kwargs)
    decision = check_billing_eligibility(
        client,
        workspace_id,
        sku_code=sku_code,
        estimated_cost_minor=estimated_cost_minor,
    )
    if _field(decision, "allowed") is not True:
        raise BillingDeniedError(decision=decision, create_type=resource_type)
    return decision


def preflight_create(
    settings: Any,
    resource_type: str,
    *,
    client: Any = None,
    sku_code: Optional[str] = None,
    estimated_cost_minor: Optional[int | float] = None,
) -> Any:
    """``preflight_billing`` for a billable create command, building the client on demand.

    Does nothing (and sends nothing) unless ``--check-billing``/``IBEE_CHECK_BILLING`` is set.
    """

    if not getattr(settings, "check_billing", False):
        return None
    from .context import get_client, require_workspace

    workspace = require_workspace(settings)
    return preflight_billing(
        settings,
        client if client is not None else get_client(settings),
        workspace,
        sku_code=sku_code,
        estimated_cost_minor=estimated_cost_minor,
        resource_type=resource_type,
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


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


__all__ = [
    "IbeeValidationError",
    "WaitConfig",
    "check_billing_eligibility",
    "compact_payload",
    "confirm_destructive",
    "finish_operation",
    "idempotency_key_option",
    "new_idempotency_key",
    "parse_json_array",
    "parse_json_object",
    "parse_value",
    "poll_interval_option",
    "preflight_billing",
    "preflight_create",
    "print_json",
    "resolve_idempotency_key",
    "resolve_wait",
    "response_items",
    "timeout_option",
    "wait_for_operation",
]
