"""Shared helpers for write / async-operation commands.

Compute writes (VM create/delete/power) return `202 + operation_id`; these
helpers generate idempotency keys, optionally poll the operation to a terminal
state (`--wait`), and parse secret values from the command line.
"""

from __future__ import annotations

import json
import time
import uuid

import typer

from .render import print_json

# Terminal states for a compute operation (see OperationStatusStatus).
_TERMINAL = {"completed", "failed"}


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
        if getattr(last, "status", None) in _TERMINAL:
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
        if settings.as_json:
            print_json(final)
        if status == "completed":
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
