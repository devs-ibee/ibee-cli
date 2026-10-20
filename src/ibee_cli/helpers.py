"""Shared helpers: confirmations, idempotency keys, ``--wait`` and input parsing.

Compute writes return ``202`` with an ``operation_id``. ``--wait`` polls
``GET /compute/operations/{operation_id}`` until the operation reaches a terminal
status of the contract (``succeeded``, ``failed``, ``cancelled``, ``timed_out``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, Optional

import typer

from .context import fail_usage
from .render import EXIT_FAILURE, EXIT_WAIT_TIMEOUT, print_json, print_structured

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "timed_out"})
DEFAULT_WAIT_TIMEOUT_SECONDS = 1200.0
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

# Indirections so tests can replace time.
_sleep = time.sleep
_clock = time.monotonic


def field(value: Any, *names: str) -> Any:
    """Read a response field from a generated model or a mapping (enum values unwrapped)."""
    for name in names:
        if isinstance(value, Mapping) and name in value:
            found = value[name]
        elif hasattr(value, name):
            found = getattr(value, name)
        else:
            continue
        return getattr(found, "value", found)
    return None


def response_items(result: Any, *wrapper_fields: str) -> list[Any]:
    """The items of a list response (a bare list, or a wrapper with one of ``wrapper_fields``)."""
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        return list(result)
    for name in wrapper_fields:
        items = field(result, name)
        if items is not None:
            return list(items)
    return []


def confirm_destructive(settings: Any, prompt: str, yes: bool = False) -> None:
    """Proceed with ``--yes`` (command or global) or ``IBEE_ASSUME_YES``; otherwise ask. Declining exits 1."""
    if yes or getattr(settings, "assume_yes", False):
        return
    typer.confirm(prompt, abort=True)


def show_sensitive_option() -> Any:
    return typer.Option(False, "--show-sensitive", help="Print the credentials in the answer")


def require_show_sensitive(settings: Any, show_sensitive: bool) -> None:
    """The answer carries credentials: send only with --show-sensitive or -o json|yaml|id."""
    if not (show_sensitive or getattr(settings, "structured_output", False)):
        fail_usage("This command prints credentials: pass --show-sensitive (or -o json|yaml|id).")


def idempotency_key_option() -> Any:
    return typer.Option(
        None, "--idempotency-key", metavar="KEY", help="Reuse a key to retry safely (default: a new key per call)"
    )


def wait_option(what: str = "the operation finishes") -> Any:
    return typer.Option(False, "--wait", help=f"Poll until {what}")


def timeout_option() -> Any:
    return typer.Option(
        DEFAULT_WAIT_TIMEOUT_SECONDS, "--timeout", metavar="SECONDS", help="Seconds to wait (with --wait)"
    )


def poll_interval_option() -> Any:
    return typer.Option(
        DEFAULT_POLL_INTERVAL_SECONDS, "--poll-interval", metavar="SECONDS", help="Seconds between polls (with --wait)"
    )


def wait_for_operation(client: Any, operation_id: str, timeout: float, interval: float) -> Any:
    """Poll the operation until it is terminal; ``None`` when ``timeout`` passes first."""
    deadline = _clock() + timeout
    while True:
        operation = client.cloud_vms.get_compute_operation(operation_id)
        if str(field(operation, "status") or "").lower() in TERMINAL_STATUSES:
            return operation
        if _clock() + interval > deadline:
            return None
        _sleep(interval)


def finish_operation(
    settings: Any,
    client: Any,
    result: Any,
    action: str,
    ident: str,
    wait: bool = False,
    timeout: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> None:
    """Print an accepted operation, or with ``wait`` its final state.

    Exit 1 when the operation ends ``failed``, ``cancelled`` or ``timed_out``; exit 3
    when ``timeout`` passes while it is still running.
    """
    op_id = field(result, "operation_id")
    mode = getattr(settings, "output", None)
    if not wait or not op_id:
        if mode in ("json", "yaml", "id"):
            print_structured(result, mode, id_field="operation_id")
        else:
            tail = f" (operation {op_id}; poll: ibee ops wait {op_id})" if op_id else ""
            typer.secho(f"{action} accepted for {ident}{tail}", fg=typer.colors.GREEN)
        return
    final = wait_for_operation(client, op_id, timeout, poll_interval)
    if final is None:
        typer.secho(
            f"{action} still running for {ident} after {timeout:g}s; resume with: ibee ops wait {op_id}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=EXIT_WAIT_TIMEOUT)
    status = str(field(final, "status")).lower()
    if mode in ("json", "yaml", "id"):
        print_structured(final, mode, id_field="operation_id")
    elif status == "succeeded":
        typer.secho(f"{action} succeeded for {ident} (operation {op_id}).", fg=typer.colors.GREEN)
    if status != "succeeded":
        detail = ": ".join(str(part) for part in (field(final, "error_code"), field(final, "error_message")) if part)
        typer.secho(
            f"{action} {status} for {ident} (operation {op_id}){': ' + detail if detail else ''}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=EXIT_FAILURE)


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
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise typer.BadParameter(f"{option} expects KEY=VALUE, got {item!r}.")
        out[key.strip()] = value
    return out


def parse_value(raw: str) -> dict:
    """A secret value: a JSON object, or comma-separated ``key=value`` pairs."""
    raw = (raw or "").strip()
    if raw.startswith("{"):
        return parse_json_object(raw, "value")
    out: dict[str, str] = {}
    for pair in raw.split(",") if raw else []:
        key, sep, val = pair.partition("=")
        if not sep:
            raise typer.BadParameter(f"expected key=value pairs, got {pair!r}")
        out[key.strip()] = val.strip()
    return out


def parse_json(raw: str, option_name: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{option_name} is not valid JSON: {exc}")


def parse_json_object(raw: str, option_name: str) -> dict:
    data = parse_json(raw, option_name)
    if not isinstance(data, dict):
        raise typer.BadParameter(f"{option_name} must be a JSON object")
    return data


def parse_json_array(raw: str, option_name: str) -> list:
    data = parse_json(raw, option_name)
    if not isinstance(data, list):
        raise typer.BadParameter(f"{option_name} must be a JSON array")
    return data


def compact_payload(**values: Any) -> dict:
    """Drop options the user did not supply (``None``), keeping false and zero."""
    return {key: value for key, value in values.items() if value is not None}


__all__ = [
    "compact_payload",
    "confirm_destructive",
    "field",
    "finish_operation",
    "idempotency_key_option",
    "load_json_input",
    "parse_json_array",
    "parse_json_object",
    "parse_key_value_pairs",
    "parse_value",
    "poll_interval_option",
    "print_json",
    "read_text_files",
    "require_show_sensitive",
    "response_items",
    "show_sensitive_option",
    "timeout_option",
    "wait_for_operation",
    "wait_option",
]
