"""Output rendering and CLI error handling.

Output modes (global ``-o/--output``, env ``IBEE_OUTPUT``; ``--json`` = ``-o json``):
``table`` (rich tables, the default for list commands that render tables), ``json``,
``yaml`` and ``id`` (one identifier per line). Without ``-o`` each command keeps its
usual format.

Errors go to stderr: the HTTP status and the server's ``error`` code, billing reason,
message, request id and admission context id exactly as the server sent them (JSON
with ``-o json``). The exit code tells the error class apart (see ``EXIT_CODES``).
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any, Callable, Iterable, Optional, Sequence

import click
import httpx
import typer
import typer.main
from ibee.client import IbeeValidationError
from ibee.core.api_error import ApiError
from ibee.core.parse_error import ParsingError
from rich.console import Console
from rich.table import Table

console = Console()

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_WAIT_TIMEOUT = 3
EXIT_NETWORK = 4
EXIT_SERVER = 19
#: Exit code for each HTTP error status (one per SDK error class); 5xx gives
#: EXIT_SERVER and any other status EXIT_FAILURE.
EXIT_CODES = {400: 10, 401: 11, 402: 12, 403: 13, 404: 14, 409: 15, 422: 16, 423: 17, 429: 18}

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


def _items(data: Any, id_field: Optional[str] = None) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if id_field and data.get(id_field) not in (None, ""):
            # One object that carries the command's id (a firewall group, a load
            # balancer): its nested ``rules`` or ``subnets`` are not the result.
            return [data]
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
    for item in _items(to_data(payload), id_field):
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
    """Tell the user how to repeat a keyed write without doing it twice."""
    if idempotency_key:
        _err(f"Retry safely with: --idempotency-key {idempotency_key}", typer.colors.YELLOW)


def exit_code(exc: ApiError) -> int:
    status = exc.status_code or 0
    return EXIT_CODES.get(status) or (EXIT_SERVER if status >= 500 else EXIT_FAILURE)


def error_record(exc: ApiError) -> dict:
    """The server's answer, field by field, as received."""
    record = {
        "status": exc.status_code,
        "code": exc.code,
        "reason": exc.billing_reason,
        "message": exc.message,
        "request_id": exc.request_id,
    }
    if exc.admission_context_id:
        record["admission_context_id"] = exc.admission_context_id
    if exc.required_scope:
        record["required_scope"] = exc.required_scope
    if exc.idempotency_key:
        record["idempotency_key"] = exc.idempotency_key
    record["body"] = to_data(exc.body)
    return record


def _print_error(record: dict) -> None:
    if _current_output() == "json":
        typer.echo(json.dumps({"error": record}, indent=2, default=str), err=True)
        return
    _err(f"Error: HTTP {record['status'] if record.get('status') is not None else '-'}")
    for key in ("code", "reason", "message", "request_id", "admission_context_id", "required_scope"):
        if key in record:
            value = record[key]
            _err(f"  {key}: {'-' if value in (None, '') else value}")


def _api_error_exit(exc: ApiError) -> None:
    _print_error(error_record(exc))
    if exc.status_code in (429, 502, 503, 504) or (exc.status_code or 0) >= 500:
        retry_hint(exc.idempotency_key)
    raise typer.Exit(code=exit_code(exc))


def handle_api_errors(fn: Callable) -> Callable:
    """Print SDK, API and network errors and exit with their class's code."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ApiError as exc:
            _api_error_exit(exc)
        except ParsingError as exc:
            # A success answer whose body does not match the SDK's models.
            headers = {key.lower(): value for key, value in (exc.headers or {}).items()}
            _print_error({
                "status": exc.status_code,
                "code": None,
                "reason": None,
                "message": f"response not parsed: {exc.cause}",
                "request_id": headers.get("x-request-id"),
                "body": to_data(exc.body),
            })
            raise typer.Exit(code=EXIT_FAILURE)
        except IbeeValidationError as exc:
            # Bad configuration (endpoint, token): nothing was sent.
            if _current_output() == "json":
                typer.echo(json.dumps({"error": {"code": exc.code, "message": exc.message}}, indent=2), err=True)
            else:
                _err(exc.message)
            raise typer.Exit(code=EXIT_USAGE)
        except httpx.TransportError as exc:
            key = getattr(exc, "idempotency_key", None)
            if _current_output() == "json":
                record = {"status": None, "code": type(exc).__name__, "message": str(exc), "idempotency_key": key}
                typer.echo(json.dumps({"error": record}, indent=2), err=True)
            else:
                _err(f"Connection error: {exc}")
            retry_hint(key)
            raise typer.Exit(code=EXIT_NETWORK)

    return wrapper
