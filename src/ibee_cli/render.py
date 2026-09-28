"""Output rendering and CLI error handling.

Output modes (global ``-o/--output``, env ``IBEE_OUTPUT``; ``--json`` = ``-o json``):

* ``table``: rich tables (the default for list commands that render tables);
* ``json``: indented JSON;
* ``yaml``: block-style YAML (PyYAML when installed, otherwise a built-in emitter);
* ``id``: one identifier per line.

Without ``-o`` each command keeps its 0.3.0 default. Errors always go to stderr.

Exit codes: 0 success; 1 API error, network error, failed operation, declined
confirmation or billing denial; 2 usage or client-side validation error; 3 ``--wait``
reached its timeout while the operation was still running.
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any, Callable, Iterable, Optional, Sequence

import click
import typer
import typer.main
from ibee.billing import TOPUP_GUIDANCE, billing_block_message, is_billing_topup_allowed
from ibee.core.api_error import ApiError
from ibee.errors import (
    ApiKeyInactiveError,
    BillingDeniedError,
    BillingForbiddenError,
    CasConflictError,
    CdnPurgeFailedError,
    DeletionIncompleteError,
    IbeeError,
    InsufficientScopeError,
    OperationFailedError,
    OperationTimeoutError,
    OrganizationLifecycleError,
    OrganizationRestrictedError,
    OrganizationSuspendedError,
    ReservedIpTargetUnsupportedError,
    ResizeBlockedError,
    ResourceNotFoundError,
    SecretValueNotFoundError,
    WorkspaceNotAllowedError,
)
from ibee.validation import IbeeValidationError
from rich.console import Console
from rich.table import Table

console = Console()

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_WAIT_TIMEOUT = 3

#: Identifier fields tried, in order, by ``-o id`` when a command names none.
ID_FIELDS = (
    "_id",
    "id",
    "vm_id",
    "volume_id",
    "store_id",
    "secret_id",
    "identity_id",
    "distribution_id",
    "vpc_id",
    "subnet_id",
    "nat_gateway_id",
    "reserved_ip_id",
    "firewall_group_id",
    "group_id",
    "rule_id",
    "load_balancer_id",
    "session_id",
    "access_key_id",
    "name",
    "operation_id",
)
#: Wrapper keys that hold the items of a collection response.
COLLECTION_KEYS = (
    "items",
    "vms",
    "stores",
    "secrets",
    "buckets",
    "volumes",
    "distributions",
    "vpcs",
    "subnets",
    "reserved_ips",
    "load_balancers",
    "groups",
    "rules",
    "credentials",
    "identities",
    "scopes",
    "snapshots",
    "runs",
    "events",
    "plans",
    "images",
    "sites",
)


# ---------------------------------------------------------------------------
# Data conversion
# ---------------------------------------------------------------------------


def _json_value(payload: Any) -> Any:
    if hasattr(payload, "model_dump"):
        # JSON mode keeps the API's wire format (ISO 8601 timestamps such as
        # 2026-09-28T10:00:00Z) instead of Python ``datetime`` reprs.
        try:
            return _json_value(payload.model_dump(mode="json"))
        except TypeError:
            return _json_value(payload.model_dump())
    if hasattr(payload, "dict") and not isinstance(payload, dict):
        return _json_value(payload.dict())
    if isinstance(payload, dict):
        return {key: _json_value(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_json_value(value) for value in payload]
    if hasattr(payload, "__dict__") and type(payload).__module__ == "types":
        # SimpleNamespace and similar plain objects.
        return {key: _json_value(value) for key, value in vars(payload).items()}
    return payload


def to_data(payload: Any) -> Any:
    """Plain JSON-compatible data for a model, mapping or list."""
    return _json_value(payload)


def _current_context() -> Any:
    getters = (getattr(typer.main, "get_current_context", None), click.get_current_context)
    for getter in getters:
        if getter is None:
            continue
        ctx = getter(silent=True)
        if ctx is not None:
            return ctx
    return None


def _current_output() -> Optional[str]:
    ctx = _current_context()
    if ctx is None:
        return None
    settings = ctx.find_root().obj
    return getattr(settings, "output", None)


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------

#: Strings written unquoted: they must start with a letter or ``_`` and contain no
#: ``:``, spaces, ``@`` or other indicators, so a YAML parser reads them back as the
#: same string (timestamps, times, dates, hex, ``.inf`` and the like are quoted).
_PLAIN_SCALAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_./-]*$")
_YAML_RESERVED = frozenset(
    {"", "~", "null", "true", "false", "yes", "no", "on", "off", "y", "n"}
)


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    text = str(value)
    if _PLAIN_SCALAR.fullmatch(text) and text.lower() not in _YAML_RESERVED:
        return text
    return json.dumps(text, ensure_ascii=False)


def _yaml_lines(value: Any, indent: int) -> list[str]:
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return [pad + "{}"]
        lines = []
        for key, item in value.items():
            key_text = _yaml_scalar(str(key))
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{key_text}:")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{pad}{key_text}: {_yaml_inline(item)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [pad + "[]"]
        lines = []
        for item in value:
            if isinstance(item, (dict, list)) and item:
                nested = _yaml_lines(item, indent + 2)
                lines.append(f"{pad}- {nested[0].lstrip()}")
                lines.extend(nested[1:])
            else:
                lines.append(f"{pad}- {_yaml_inline(item)}")
        return lines
    return [pad + _yaml_scalar(value)]


def _yaml_inline(value: Any) -> str:
    if isinstance(value, dict):
        return "{}"
    if isinstance(value, list):
        return "[]"
    return _yaml_scalar(value)


def to_yaml(payload: Any) -> str:
    """Block-style YAML for ``payload`` (PyYAML when installed)."""
    data = json.loads(json.dumps(to_data(payload), default=str))
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return "\n".join(_yaml_lines(data, 0)) + "\n"
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------


def _items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in COLLECTION_KEYS:
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return [] if data is None else [data]


def _identifier(item: Any, id_field: Optional[str]) -> Optional[str]:
    if not isinstance(item, dict):
        return None if item is None else str(item)
    fields = (id_field,) if id_field else ID_FIELDS
    for name in fields:
        value = item.get(name)
        if value not in (None, ""):
            return str(value)
    return None


def identifiers(payload: Any, id_field: Optional[str] = None) -> list[str]:
    """Identifiers for ``-o id``: one per item of a collection, or one for an object."""
    ids = []
    for item in _items(to_data(payload)):
        value = _identifier(item, id_field)
        if value is not None:
            ids.append(value)
    return ids


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------


def _print_json_text(payload: Any) -> None:
    console.print_json(json.dumps(to_data(payload), default=str))


def print_structured(payload: Any, mode: str, *, id_field: Optional[str] = None) -> None:
    """Print ``payload`` as ``json``, ``yaml`` or ``id``."""
    if mode == "yaml":
        typer.echo(to_yaml(payload), nl=False)
    elif mode == "id":
        for value in identifiers(payload, id_field):
            typer.echo(value)
    else:
        _print_json_text(payload)


def print_json(payload: Any, *, id_field: Optional[str] = None) -> None:
    """Print a command result whose 0.3.0 default is JSON.

    ``-o yaml`` and ``-o id`` are honoured; ``-o table`` and no option print JSON.
    """
    mode = _current_output()
    print_structured(payload, mode if mode in ("yaml", "id") else "json", id_field=id_field)


def print_table(title: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    table = Table(title=title, title_justify="left")
    for col in columns:
        table.add_column(col)
    count = 0
    for row in rows:
        table.add_row(*[("-" if v is None else str(v)) for v in row])
        count += 1
    if count == 0:
        console.print(f"{title}: none found")
        return
    console.print(table)


def cell(item: Any, column: str) -> Any:
    """Read ``column`` from a model or mapping (enum values unwrapped)."""
    if isinstance(item, dict):
        value = item.get(column)
    else:
        value = getattr(item, column, None)
    return getattr(value, "value", value)


def emit(
    result: Any,
    *,
    settings: Any = None,
    default: str = "table",
    columns: Optional[Sequence[str]] = None,
    title: Optional[str] = None,
    id_field: Optional[str] = None,
    rows: Optional[Iterable[Sequence[Any]]] = None,
    headers: Optional[Sequence[str]] = None,
) -> None:
    """Render ``result`` in the selected output mode.

    ``default`` is the command's 0.3.0 behaviour when no ``-o`` is given. For table
    output, pass ``columns`` (field names read from each item) or explicit
    ``headers`` and ``rows``; without either, table mode prints JSON.
    """
    mode = getattr(settings, "output", None) if settings is not None else _current_output()
    mode = mode or default
    if mode != "table" or (columns is None and rows is None):
        print_structured(result, "json" if mode == "table" else mode, id_field=id_field)
        return
    if rows is None:
        items = result if isinstance(result, (list, tuple)) else _items(result)
        rows = [[cell(item, column) for column in columns or ()] for item in items]
    print_table(title or "", headers or columns or (), rows)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def _err(message: str, color: str = typer.colors.RED) -> None:
    typer.secho(message, fg=color, err=True)


def retry_hint(idempotency_key: Optional[str]) -> None:
    """Tell the user how to repeat a keyed write without creating it twice."""
    if idempotency_key:
        _err(f"Retry safely with: --idempotency-key {idempotency_key}", typer.colors.YELLOW)


def _body_text(exc: ApiError) -> str:
    message = getattr(exc, "message", None)
    if message:
        return str(message)
    return repr(getattr(exc, "body", None))


def _billing_message(exc: ApiError) -> list[str]:
    if isinstance(exc, BillingDeniedError):
        lines = [f"Payment required (402): {exc.message}"]
        topup = bool(getattr(exc, "topup_allowed", False))
    else:
        reason = getattr(exc, "reason", None) or ""
        lines = [f"Payment required (402): {billing_block_message(reason)}"]
        topup = is_billing_topup_allowed(reason)
    details = []
    if getattr(exc, "reason", None):
        details.append(f"reason={exc.reason}")
    sku = getattr(exc, "sku_code", None) or getattr(exc, "billing_sku_code", None)
    if sku:
        details.append(f"sku={sku}")
    if getattr(exc, "admission_context_id", None):
        details.append(f"admission_context_id={exc.admission_context_id}")
    if details:
        lines.append("  " + " ".join(details))
    if topup:
        lines.append(TOPUP_GUIDANCE)
    return lines


def api_error_lines(exc: ApiError) -> list[str]:
    """Human-readable lines describing an API error, plus the SDK's hint when it has one."""
    lines = _api_error_lines(exc)
    hint = getattr(exc, "hint", None)
    if isinstance(hint, str) and hint and not isinstance(
        exc, (CasConflictError, DeletionIncompleteError, ResourceNotFoundError)
    ):
        lines.append(f"  hint: {hint}")
    return lines


def _secret_store_error_lines(exc: ApiError, message: str) -> Optional[list[str]]:
    """Secret Store errors that need more than the generic status line."""
    if isinstance(exc, CasConflictError):
        return [
            "Check-and-set conflict (502): the secret's current version differs from --cas.",
            "  Run 'ibee secrets versions SECRET_ID' and retry with the current version.",
        ]
    if isinstance(exc, DeletionIncompleteError):
        steps = ", ".join(str(step) for step in getattr(exc, "failed_steps", None) or []) or "unknown"
        return [
            f"Deletion incomplete (503): {message}",
            f"  failed steps: {steps}",
            "  The store stays in 'deleting'; run the same command again to finish the deletion.",
        ]
    if isinstance(exc, OrganizationLifecycleError):
        state = getattr(exc, "state", None) or "restricted"
        operation = getattr(exc, "operation", None) or "this"
        return [f"Forbidden (403): the organization is {state}, so {operation} operations are not allowed."]
    if isinstance(exc, ResourceNotFoundError):
        kind = (getattr(exc, "kind", None) or "resource").capitalize()
        ident = getattr(exc, "resource_id", None)
        what = f"{kind} '{ident}'" if ident else kind
        return [
            f"Not found (403): {what} does not exist or belongs to a different workspace. "
            "Verify --workspace or IBEE_WORKSPACE_ID matches the workspace used when it was created."
        ]
    if isinstance(exc, SecretValueNotFoundError):
        return [f"Not found (404): {message}"]
    return None


def _api_error_lines(exc: ApiError) -> list[str]:
    status = exc.status_code
    message = _body_text(exc)
    lowered = message.lower() + " " + str(getattr(exc, "body", "")).lower()
    if isinstance(exc, CdnPurgeFailedError):
        mode = getattr(exc, "mode", None) or "unknown"
        return [
            f"Cache purge failed (mode {mode}): {message}",
            "  Nothing was purged. Prefix and tag purges may not be available for this distribution; "
            "try --mode url or --mode all.",
        ]
    secret_store = _secret_store_error_lines(exc, message)
    if secret_store is not None:
        return secret_store
    if status == 401:
        return ["Unauthorized (401): the API token is invalid, revoked, or for the other environment."]
    if status == 402 or isinstance(exc, BillingDeniedError):
        return _billing_message(exc)
    if status == 403:
        if isinstance(exc, InsufficientScopeError) or getattr(exc, "code", None) == "insufficient_scope":
            scope = getattr(exc, "required_scope", None) or "required"
            return [f"Forbidden (403): the token is missing scope {scope}."]
        if (
            isinstance(exc, WorkspaceNotAllowedError)
            or "does not belong to workspace" in lowered
            or "does not match api token context" in lowered
        ):
            return [
                "Forbidden (403): the resource belongs to a different workspace. "
                "Verify --workspace or IBEE_WORKSPACE_ID matches the workspace used "
                f"when the resource was created. ({message})"
            ]
        if isinstance(exc, ApiKeyInactiveError):
            return [f"Forbidden (403): the API token is not active ({exc.code})."]
        if isinstance(exc, OrganizationRestrictedError):
            return [f"Forbidden (403): the organization is restricted: {message}"]
        if isinstance(exc, BillingForbiddenError):
            reason = getattr(exc, "reason", None) or ""
            lines = [f"Forbidden (403): {billing_block_message(reason)}"]
            if is_billing_topup_allowed(reason):
                lines.append(TOPUP_GUIDANCE)
            return lines
        return [f"Forbidden (403): {message}"]
    if isinstance(exc, ReservedIpTargetUnsupportedError):
        return [
            "Not supported (404): this VM has no VPC attachment. To keep its current public IP "
            "as a Reserved IP use 'ibee reserved-ips convert --vm-id VM_ID --site-id SITE_ID'; "
            "attaching a held Reserved IP to a VM outside a VPC is not yet available in the public API."
        ]
    if status == 404:
        return ["Not found (404): the resource does not exist in this workspace."]
    if isinstance(exc, ResizeBlockedError):
        decision = getattr(exc, "decision", None) or "blocked"
        return [f"Resize not possible (409, decision {decision}): {message}", *_decision_lines(exc)]
    if status == 409:
        return [f"Conflict (409): {message}"]
    if status == 413:
        return ["Request too large (413): the request body is over the 64 KiB limit."]
    if status == 422:
        return [f"Validation failed (422): {message}"]
    if status == 423 or isinstance(exc, OrganizationSuspendedError):
        return [
            "Organization suspended (423): billing has suspended this organization, "
            f"so this change is blocked. {message}"
        ]
    if status == 429:
        retry_after = getattr(exc, "retry_after", None)
        after = f"; retry after {retry_after:g} s" if retry_after is not None else ""
        return [f"Rate limited (429){after}. {message}"]
    if status is not None and status >= 500:
        lines = [f"Service error ({status}, {exc.code}): {message}"]
        refs = []
        if getattr(exc, "request_id", None):
            refs.append(f"request_id={exc.request_id}")
        if getattr(exc, "admission_context_id", None):
            refs.append(f"admission_context_id={exc.admission_context_id}")
        if refs:
            lines.append("  " + " ".join(refs))
        return lines
    return [f"API error {status}: {message}"]


def _text_items(values: Any) -> list[str]:
    items = values if isinstance(values, (list, tuple)) else [values]
    out = []
    for item in items:
        if item in (None, "", [], {}):
            continue
        if isinstance(item, dict):
            item = item.get("message") or item.get("reason") or item.get("code") or json.dumps(item, default=str)
        out.append(str(item))
    return out


def _decision_lines(source: Any) -> list[str]:
    """Reasons, warnings and migration steps of a resize precheck (object or mapping)."""
    lines = []
    for label, name in (("reason", "reasons"), ("warning", "warnings"), ("migration step", "migration_checklist")):
        value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
        for text in _text_items(value or []):
            lines.append(f"  {label}: {text}")
    return lines


def validation_error_lines(exc: IbeeValidationError) -> list[str]:
    """The message of a client-side validation error plus any structured details."""
    lines = [str(exc)]
    details = to_data(getattr(exc, "details", None))
    if isinstance(details, dict):
        decision = details.get("decision")
        if decision:
            lines.append(f"  decision: {decision}")
        lines.extend(_decision_lines(details))
    return lines


def _is_transport_error(exc: BaseException) -> bool:
    return type(exc).__module__.startswith("httpx")


def _api_error_exit(exc: ApiError) -> None:
    for line in api_error_lines(exc):
        _err(line)
    hint = getattr(exc, "cli_hint", None)
    if hint:
        _err(hint, typer.colors.YELLOW)
    if exc.status_code in (429, 502, 503, 504) or (exc.status_code or 0) >= 500:
        retry_hint(getattr(exc, "idempotency_key", None))
    raise typer.Exit(code=EXIT_FAILURE)


def handle_api_errors(fn: Callable) -> Callable:
    """Convert SDK, API and network errors into clean CLI errors and exit codes."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except IbeeValidationError as exc:
            if isinstance(exc, ApiError):
                # A typed server response that is also a validation error
                # (e.g. ReservedIpTargetUnsupportedError): the API refused it.
                _api_error_exit(exc)
            for line in validation_error_lines(exc):
                _err(line)
            if isinstance(exc.__cause__, ApiError):
                # The SDK translated a server refusal into a validation error: the
                # request was sent, so this is an API error (exit 1), not usage.
                hint = getattr(exc, "cli_hint", None)
                if hint:
                    _err(hint, typer.colors.YELLOW)
                raise typer.Exit(code=EXIT_FAILURE)
            raise typer.Exit(code=EXIT_USAGE)
        except ApiError as exc:
            _api_error_exit(exc)
        except OperationTimeoutError as exc:
            _err(f"{exc}; resume with: ibee ops wait {exc.operation_id}", typer.colors.YELLOW)
            raise typer.Exit(code=EXIT_WAIT_TIMEOUT)
        except OperationFailedError as exc:
            _err(str(exc))
            raise typer.Exit(code=EXIT_FAILURE)
        except IbeeError as exc:
            # Any other SDK error is a server-state condition (for example a NAT gateway
            # deletion that is still reconciling), not a usage error: exit 1 with its hint.
            _err(str(exc))
            hint = getattr(exc, "cli_hint", None)
            if hint:
                _err(hint, typer.colors.YELLOW)
            raise typer.Exit(code=EXIT_FAILURE)
        except Exception as exc:  # httpx connection errors etc.
            if _is_transport_error(exc):
                _err(f"Connection error: {exc}")
                retry_hint(getattr(exc, "idempotency_key", None))
                raise typer.Exit(code=EXIT_FAILURE)
            raise

    return wrapper
