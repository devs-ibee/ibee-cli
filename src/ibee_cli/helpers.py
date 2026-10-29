"""What the generated commands share: the SDK client, request arguments and confirmations.

Nothing here decides anything the server decides: options the user left out are not
sent, JSON options are parsed as given, and the server's answer is printed.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable, Optional

import typer

from . import context


def sdk(ctx: typer.Context) -> Any:
    """The SDK client for this invocation's global options."""
    return context.get_client(context.get_settings(ctx))


def workspace(ctx: typer.Context) -> str:
    """The global ``--workspace`` (``IBEE_WORKSPACE_ID``): every operation's ``workspace_id``."""
    return context.get_settings(ctx).workspace


def compact(**values: Any) -> dict:
    """The options the user gave (``None`` means left out and is not sent; false and zero are)."""
    return {key: value for key, value in values.items() if value is not None}


def json_arg(value: Optional[str], option: str) -> Any:
    """A JSON option's value: JSON text, ``@FILE`` (the file's JSON) or ``@-`` (JSON on stdin)."""
    if value is None:
        return None
    text = value
    if value.startswith("@"):
        source = value[1:]
        if source == "-":
            text = sys.stdin.read()
        else:
            try:
                with open(source, encoding="utf-8") as handle:
                    text = handle.read()
            except OSError as exc:
                context.fail_usage(f"{option}: cannot read {source}: {exc.strerror or exc}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        context.fail_usage(f"{option} is not valid JSON: {exc}")
    return None  # pragma: no cover - fail_usage always raises


def confirm(ctx: typer.Context, yes: bool, prompt: str) -> None:
    """Ask before a destructive command unless ``--yes`` (or the global ``--yes``/``IBEE_ASSUME_YES``).

    Declining, or no answer on a closed stdin, exits 1 and sends nothing.
    """
    if yes or context.get_settings(ctx).assume_yes:
        return
    typer.confirm(prompt, abort=True)


def write_bytes(chunks: Iterable[bytes]) -> None:
    """Write a binary answer (a downloaded document) to stdout unchanged."""
    stream = getattr(sys.stdout, "buffer", sys.stdout)
    for chunk in chunks:
        stream.write(chunk)
    stream.flush()


__all__ = ["compact", "confirm", "json_arg", "sdk", "workspace", "write_bytes"]
