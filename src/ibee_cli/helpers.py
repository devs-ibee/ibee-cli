"""Shared helpers for write / async-operation commands.

Compute writes (VM create/delete/power, access, resize, and volume actions)
return `202 + operation_id`; these helpers build idempotency keys, optionally
poll the operation to a terminal state (`--wait`), retain deprecated no-op billing
flags, ask for confirmation of destructive actions,
and parse structured values from the command line.
"""

from __future__ import annotations

import contextlib
import json
import time
import warnings
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import typer
from ibee.core.api_error import ApiError
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


def timeout_option(default: Optional[float] = None) -> Any:
    """Shared ``--timeout`` option (seconds) for ``--wait``; ``default`` only changes the help."""

    shown = DEFAULT_WAIT_TIMEOUT_SECONDS if default is None else default
    return typer.Option(
        None,
        "--timeout",
        metavar="SECONDS",
        show_default=False,
        help=(
            f"Seconds to wait ({MIN_WAIT_TIMEOUT_SECONDS:g}-{MAX_WAIT_TIMEOUT_SECONDS:g}, "
            f"default {shown:g}; used with --wait)"
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
    default_timeout: Optional[float] = None,
) -> Optional[WaitConfig]:
    """Validate ``--wait``/``--timeout``/``--poll-interval`` before any request.

    Returns ``None`` when not waiting. ``--timeout``/``--poll-interval`` without
    ``--wait`` exit 2 (unless ``require_wait`` is false, as for ``ops wait``).
    ``default_timeout`` replaces the 1200 s default (recovery waits use 1800 s).
    """

    if not wait and require_wait:
        if timeout is not None or poll_interval is not None:
            raise typer.BadParameter("--timeout and --poll-interval require --wait.")
        return None
    if timeout is None:
        timeout = DEFAULT_WAIT_TIMEOUT_SECONDS if default_timeout is None else default_timeout
    wait_timeout = validate_wait_timeout(timeout)
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


def _status_check_failed(action: str, ident: str, op_id: str, idempotency_key: Optional[str]) -> None:
    """The write was accepted but polling it failed: say how to resume instead of re-running it."""

    typer.secho(
        f"{action} accepted for {ident} (operation {op_id}), but checking its status failed; "
        f"resume with: ibee ops wait {op_id}",
        fg=typer.colors.YELLOW,
        err=True,
    )
    retry_hint(idempotency_key)


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
        except (ApiError, httpx.TransportError):
            _status_check_failed(action, ident, op_id, idempotency_key)
            raise
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


def finish_operations(
    settings: Any,
    client: Any,
    workspace_id: str,
    results: Sequence[Any],
    action: str,
    idents: Sequence[str],
    wait: Optional[WaitConfig],
    *,
    idempotency_keys: Optional[Sequence[Optional[str]]] = None,
) -> None:
    """Render several accepted operations (``--count`` batch creates).

    One result behaves exactly like :func:`finish_operation`. With several, every
    operation is reported (or waited for) before the command exits: 1 when any
    failed, 3 when any wait timed out. In json/yaml/id mode one list is printed.
    """

    keys = list(idempotency_keys or [None] * len(results))
    if len(results) == 1:
        finish_operation(
            settings, client, workspace_id, results[0], action, idents[0], wait,
            idempotency_key=keys[0],
        )
        return
    mode = getattr(settings, "output", None)
    structured = mode in ("json", "yaml", "id")
    finals: list[Any] = []
    failed = timed_out = False
    for result, ident, key in zip(results, idents, keys):
        op_id = _field(result, "operation_id") or _field(result, "id")
        if wait is None or not op_id:
            finals.append(result)
            if not structured:
                tail = f" (operation {op_id})" if op_id else ""
                typer.secho(f"{action} accepted for {ident}{tail}", fg=typer.colors.GREEN)
            continue
        try:
            final = wait_for_operation(client, workspace_id, op_id, wait.timeout, wait.poll_interval)
        except (ApiError, httpx.TransportError):
            _status_check_failed(action, ident, op_id, key)
            raise
        except OperationTimeoutError as exc:
            timed_out = True
            finals.append(result)
            typer.secho(
                f"{action} still {exc.last_status or 'pending'} for {ident} after {wait.timeout:g}s; "
                f"resume with: ibee ops wait {op_id}",
                fg=typer.colors.YELLOW,
                err=True,
            )
            retry_hint(key)
            continue
        finals.append(final)
        if _status_text(final) in _SUCCEEDED:
            if not structured:
                typer.secho(
                    f"{action} completed for {ident} (operation {op_id}).", fg=typer.colors.GREEN
                )
        else:
            failed = True
            typer.secho(
                _operation_failure_text(final, action, ident, op_id), fg=typer.colors.RED, err=True
            )
    if structured:
        print_structured(finals, mode, id_field="operation_id")
    if failed:
        raise typer.Exit(code=EXIT_FAILURE)
    if timed_out:
        raise typer.Exit(code=EXIT_WAIT_TIMEOUT)


def wait_for_recovery(
    settings: Any,
    waiter: Any,
    ident: str,
    wait: WaitConfig,
    *,
    label: str,
    resume: str,
    **kwargs: Any,
) -> Any:
    """Poll a snapshot, backup run or restore with an SDK ``wait_for_*`` helper.

    Prints the final object (JSON by default). A failed or cancelled result raises the
    SDK's ``RecoveryFailedError`` (exit 1); when ``--timeout`` passes first the command
    prints how to resume and exits 3.
    """

    try:
        final = waiter(ident, timeout=wait.timeout, poll_interval=wait.poll_interval, **kwargs)
    except OperationTimeoutError as exc:
        typer.secho(
            f"{label} still {exc.last_status or 'pending'} for {ident} after {wait.timeout:g}s; "
            f"resume with: {resume}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=EXIT_WAIT_TIMEOUT)
    print_json(final)
    return final


def check_state_option() -> Any:
    """Shared ``--check-state/--no-check-state`` option (default on)."""

    return typer.Option(
        True,
        "--check-state/--no-check-state",
        help="Read the current state first and apply the portal's rules before sending (default: on)",
    )


def load_json_input(value: Optional[str], path: Optional[str], option: str) -> Optional[dict]:
    """A JSON object from ``--<option> JSON`` or ``--<option>-file PATH`` (not both)."""

    if value is not None and path is not None:
        raise typer.BadParameter(f"Use only one of --{option} or --{option}-file.")
    if path is not None:
        try:
            with open(path, encoding="utf-8") as handle:
                value = handle.read()
        except OSError as exc:
            raise typer.BadParameter(f"Cannot read --{option}-file {path}: {exc.strerror or exc}")
    if value is None:
        return None
    return parse_json_object(value, f"--{option}")


def read_text_files(paths: Optional[Sequence[str]], option: str) -> list[str]:
    """Contents of each ``--<option> PATH`` (for example public SSH key files), stripped."""

    values: list[str] = []
    for path in paths or []:
        try:
            with open(path, encoding="utf-8") as handle:
                values.append(handle.read().strip())
        except OSError as exc:
            raise typer.BadParameter(f"Cannot read {option} {path}: {exc.strerror or exc}")
    return values


def parse_key_value_pairs(raw: Optional[Sequence[str]], option: str) -> Optional[dict]:
    """``KEY=VALUE`` options (repeatable) as a dict; ``None`` when none are given."""

    if not raw:
        return None
    out: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise typer.BadParameter(f"{option} expects KEY=VALUE, got {item!r}.")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter(f"{option} expects KEY=VALUE, got {item!r}.")
        if key in out:
            raise typer.BadParameter(f"{option} repeats {key!r}.")
        out[key] = value
    return out


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
    """Deprecated compatibility no-op; the mutation endpoint decides admission."""
    return None


def preflight_create(
    settings: Any,
    resource_type: str,
    *,
    client: Any = None,
    sku_code: Optional[str] = None,
    estimated_cost_minor: Optional[int | float] = None,
) -> Any:
    """Deprecated compatibility no-op; no client or workspace is resolved."""
    return None


# ---------------------------------------------------------------------------
# SDK warnings and error hints
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def sdk_warnings() -> Iterator[None]:
    """Print warnings the SDK raises during a call (for example a missing billing
    catalog, or a deprecated option) as ``Warning: ...`` lines on stderr."""

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            yield
        finally:
            seen: set[str] = set()
            for item in caught:
                text = str(item.message)
                if text in seen:
                    continue
                seen.add(text)
                typer.secho(f"Warning: {text}", fg=typer.colors.YELLOW, err=True)


@contextlib.contextmanager
def api_hints(hints: Mapping[int, str]) -> Iterator[None]:
    """Attach a follow-up line to API errors with the given status codes.

    ``handle_api_errors`` prints the hint after the usual error message. A validation
    error the SDK raised from a server response is matched by that response's status.
    """

    try:
        yield
    except (ApiError, IbeeValidationError) as exc:
        source = exc if isinstance(exc, ApiError) else exc.__cause__
        if not isinstance(source, ApiError):
            raise
        hint = hints.get(source.status_code or 0)
        if hint and not getattr(exc, "cli_hint", None):
            try:
                exc.cli_hint = hint  # type: ignore[attr-defined]
            except AttributeError:  # pragma: no cover - slotted error classes
                pass
        raise


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
    "api_hints",
    "sdk_warnings",
    "WaitConfig",
    "check_billing_eligibility",
    "check_state_option",
    "compact_payload",
    "confirm_destructive",
    "finish_operation",
    "finish_operations",
    "load_json_input",
    "parse_key_value_pairs",
    "idempotency_key_option",
    "new_idempotency_key",
    "parse_json_array",
    "parse_json_object",
    "parse_value",
    "poll_interval_option",
    "preflight_billing",
    "preflight_create",
    "print_json",
    "read_text_files",
    "resolve_idempotency_key",
    "resolve_wait",
    "response_items",
    "timeout_option",
    "wait_for_operation",
    "wait_for_recovery",
]
