"""Output and errors.

Every command prints the server's answer as received. Output modes (global
``-o/--output``, env ``IBEE_OUTPUT``; ``--json`` is ``-o json``): ``json`` (the
default), ``yaml``, ``table`` (the items of a list, or the fields of one object) and
``id`` (one identifier per line). An empty answer (HTTP 204) prints nothing.

Errors go to stderr: the HTTP status and the server's code, message and request id
exactly as the server sent them (``{"error": {...}}`` with the raw body under ``-o
json``). The exit code tells the error class apart (see ``EXIT_CODES``).
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any, Callable, Optional

import click
import httpx
import typer
import typer.main
from ibee.core.api_error import ApiError
from rich.console import Console
from rich.table import Table

from .context import IbeeValidationError

try:  # a 2xx answer whose body does not match the SDK's models
    from ibee.core.parse_error import ParsingError
except ImportError:  # pragma: no cover - an SDK without it
    class ParsingError(Exception):  # type: ignore[no-redef]
        pass

try:  # Fern's encoder: a model as the JSON the server sent (wire names, no unset fields)
    from ibee.core.jsonable_encoder import jsonable_encoder as _encode
except ImportError:  # pragma: no cover - an SDK without it
    _encode = None

console = Console()

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_NETWORK = 4
EXIT_SERVER = 19
#: Exit code for each HTTP error status; 5xx gives EXIT_SERVER and any other status EXIT_FAILURE.
EXIT_CODES = {400: 10, 401: 11, 402: 12, 403: 13, 404: 14, 409: 15, 422: 16, 423: 17, 429: 18}

#: Identifier fields ``-o id`` tries, in order: an object's own id before its parents'.
ID_FIELDS = (
    "id",
    "operation_id",
    "restore_id",
    "run_id",
    "snapshot_set_id",
    "port_forward_rule_id",
    "rule_id",
    "nat_gateway_id",
    "subnet_id",
    "allocation_id",
    "virtual_ip_id",
    "public_ip_id",
    "reserved_ip_id",
    "firewall_group_id",
    "lb_id",
    "load_balancer_id",
    "volume_id",
    "distribution_id",
    "access_key_id",
    "registry_id",
    "token_id",
    "scope_id",
    "identity_id",
    "secret_id",
    "store_id",
    "template_id",
    "sku_id",
    "plan_id",
    "vpc_id",
    "vm_id",
    "site_id",
    "domain",
    "digest",
    "name",
)
#: Table columns shown at most (``-o json`` prints every field).
MAX_TABLE_COLUMNS = 8


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def to_data(payload: Any) -> Any:
    """Plain JSON data for an SDK model, mapping or list, as the server sent it."""
    if isinstance(payload, dict):
        return {key: to_data(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [to_data(value) for value in payload]
    if _encode is not None and hasattr(type(payload), "model_fields"):
        return _encode(payload)
    if hasattr(payload, "model_dump"):
        return to_data(payload.model_dump(mode="json", by_alias=True, exclude_unset=True))
    if hasattr(payload, "__dict__") and type(payload).__module__ == "types":  # SimpleNamespace
        return {key: to_data(value) for key, value in vars(payload).items()}
    return getattr(payload, "value", payload)


def _current_context() -> Any:
    getters = (getattr(typer.main, "get_current_context", None), click.get_current_context)
    for getter in getters:
        if getter is None:
            continue
        ctx = getter(silent=True)
        if ctx is not None:
            return ctx
    return None


def current_output() -> Optional[str]:
    ctx = _current_context()
    if ctx is None:
        return None
    return getattr(ctx.find_root().obj, "output", None)


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------

#: Strings written unquoted: they start with a letter or ``_`` and contain no
#: ``:``, spaces, ``@`` or other indicators, so a YAML parser reads them back as the
#: same string (timestamps, dates, hex, ``.inf`` and the like are quoted).
_PLAIN_SCALAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_./-]*$")
_YAML_RESERVED = frozenset({"", "~", "null", "true", "false", "yes", "no", "on", "off", "y", "n"})


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


def _yaml_lines(value: Any, indent: int) -> list:
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
# Items, identifiers and tables
# ---------------------------------------------------------------------------


def items(data: Any, collection: Optional[str] = None) -> Optional[list]:
    """The items of a list answer (a JSON array, or the ``collection`` array of a list
    operation's wrapper object); ``None`` for a single object."""
    if isinstance(data, list):
        return data
    if collection and isinstance(data, dict) and isinstance(data.get(collection), list):
        return data[collection]
    return None


def _identifier(item: Any) -> Optional[str]:
    if not isinstance(item, dict):
        return None if item is None else str(item)
    for name in ID_FIELDS:
        value = item.get(name)
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return str(value)
    return None


def identifiers(payload: Any, collection: Optional[str] = None, id_field: Optional[str] = None) -> list:
    """``-o id``: one identifier per item of a list answer (its ``id_field`` when the command
    names one), or the object's own."""
    data = to_data(payload)
    listed = items(data, collection)
    values = []
    for item in listed if listed is not None else [data]:
        if id_field and isinstance(item, dict) and item.get(id_field) not in (None, ""):
            values.append(str(item[id_field]))
        else:
            values.append(_identifier(item))
    return [value for value in values if value is not None]


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def print_table(payload: Any, collection: Optional[str] = None) -> None:
    """A list answer as one row per item (its first scalar fields); an object as field/value rows."""
    data = to_data(payload)
    listed = items(data, collection)
    if listed is None:
        table = Table(show_header=True)
        table.add_column("Field")
        table.add_column("Value", overflow="fold")
        for key, value in (data.items() if isinstance(data, dict) else [("value", data)]):
            table.add_row(str(key), _cell(value))
        console.print(table)
        return
    if not listed:
        console.print("No items.")
        return
    columns: list = []
    for item in listed:
        for key, value in (item.items() if isinstance(item, dict) else [("value", item)]):
            if key not in columns and not isinstance(value, (dict, list)) and len(columns) < MAX_TABLE_COLUMNS:
                columns.append(key)
    table = Table(show_header=True)
    for column in columns:
        table.add_column(str(column), overflow="fold")
    for item in listed:
        row = item if isinstance(item, dict) else {"value": item}
        table.add_row(*[_cell(row.get(column)) for column in columns])
    console.print(table)


def print_structured(
    payload: Any, mode: str, *, collection: Optional[str] = None, id_field: Optional[str] = None
) -> None:
    """Print ``payload`` as ``json``, ``yaml``, ``table`` or ``id``."""
    if mode == "yaml":
        typer.echo(to_yaml(payload), nl=False)
    elif mode == "id":
        for value in identifiers(payload, collection, id_field):
            typer.echo(value)
    elif mode == "table":
        print_table(payload, collection)
    else:
        console.print_json(json.dumps(to_data(payload), default=str))


def emit(result: Any, *, collection: Optional[str] = None, id_field: Optional[str] = None) -> None:
    """Print a command's answer in the selected output mode (JSON by default); nothing for an empty answer.

    ``collection`` names the array a list operation wraps its items in, ``id_field`` the
    item field ``-o id`` prints (both from the contract).
    """
    if result is None:
        return
    print_structured(result, current_output() or "json", collection=collection, id_field=id_field)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def _err(message: str, color: str = typer.colors.RED) -> None:
    typer.secho(message, fg=color, err=True)


def _get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _header(headers: Any, name: str) -> Optional[str]:
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return value
    return None


def error_record(exc: ApiError) -> dict:
    """The server's answer, field by field, as received (nothing reworded)."""
    body = exc.body
    detail = _get(body, "detail")
    error = _get(body, "error")
    code = _get(error, "code") if isinstance(error, dict) else error
    code = code or _get(detail, "code") or _get(body, "code")
    if isinstance(detail, str):
        message = detail
    elif isinstance(body, str):
        message = body or None
    else:
        message = _get(detail, "message") or _get(error, "message") or _get(body, "message")
    record = {
        "status": exc.status_code,
        "code": code,
        "reason": _get(body, "billing_reason") or _get(detail, "reason"),
        "message": message,
        "request_id": _header(exc.headers, "x-request-id"),
    }
    admission = _get(body, "admission_context_id") or _get(detail, "admission_context_id")
    if admission:
        record["admission_context_id"] = admission
    if _get(body, "required_scope"):
        record["required_scope"] = _get(body, "required_scope")
    key = getattr(exc, "idempotency_key", None)
    if key:
        record["idempotency_key"] = key
    record["body"] = to_data(body)
    return record


def _print_error(record: dict) -> None:
    if current_output() == "json":
        typer.echo(json.dumps({"error": record}, indent=2, default=str), err=True)
        return
    _err(f"Error: HTTP {record['status'] if record.get('status') is not None else '-'}")
    for key in ("code", "reason", "message", "request_id", "admission_context_id", "required_scope"):
        if key in record:
            value = record[key]
            _err(f"  {key}: {'-' if value in (None, '') else value}")


def exit_code(exc: ApiError) -> int:
    status = exc.status_code or 0
    return EXIT_CODES.get(status) or (EXIT_SERVER if status >= 500 else EXIT_FAILURE)


def retry_hint(idempotency_key: Optional[str]) -> None:
    """Tell the user how to repeat a keyed write without doing it twice."""
    if idempotency_key:
        _err(f"Retry safely with: --idempotency-key {idempotency_key}", typer.colors.YELLOW)


def handle_api_errors(fn: Callable) -> Callable:
    """Print SDK, API and network errors and exit with their class's code."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ApiError as exc:
            _print_error(error_record(exc))
            if (exc.status_code or 0) in (429, 502, 503, 504) or (exc.status_code or 0) >= 500:
                retry_hint(getattr(exc, "idempotency_key", None))
            raise typer.Exit(code=exit_code(exc))
        except ParsingError as exc:
            # A 2xx answer whose body does not match the SDK's models: printed as received.
            _print_error({
                "status": getattr(exc, "status_code", None),
                "code": None,
                "reason": None,
                "message": f"response not parsed: {getattr(exc, 'cause', exc)}",
                "request_id": _header(getattr(exc, "headers", None), "x-request-id"),
                "body": to_data(getattr(exc, "body", None)),
            })
            raise typer.Exit(code=EXIT_FAILURE)
        except IbeeValidationError as exc:
            # Bad configuration (endpoint): nothing was sent.
            message = getattr(exc, "message", None) or str(exc)
            if current_output() == "json":
                typer.echo(json.dumps({"error": {"code": getattr(exc, "code", None), "message": message}}, indent=2), err=True)
            else:
                _err(message)
            raise typer.Exit(code=EXIT_USAGE)
        except httpx.TransportError as exc:
            key = getattr(exc, "idempotency_key", None)
            if current_output() == "json":
                record = {"status": None, "code": type(exc).__name__, "message": str(exc), "idempotency_key": key}
                typer.echo(json.dumps({"error": record}, indent=2), err=True)
            else:
                _err(f"Connection error: {exc}")
            retry_hint(key)
            raise typer.Exit(code=EXIT_NETWORK)

    return wrapper
