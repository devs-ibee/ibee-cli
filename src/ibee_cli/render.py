"""Output rendering: rich tables by default, raw JSON with --json."""

from __future__ import annotations

import functools
import json
from typing import Any, Callable, Iterable, Sequence

import typer
from ibee.core.api_error import ApiError
from rich.console import Console
from rich.table import Table

from .context import CliApiError

console = Console()


def _json_value(payload: Any) -> Any:
    if hasattr(payload, "model_dump"):
        return _json_value(payload.model_dump())
    if hasattr(payload, "dict"):
        return _json_value(payload.dict())
    if isinstance(payload, dict):
        return {key: _json_value(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_json_value(value) for value in payload]
    return payload


def print_json(payload: Any) -> None:
    console.print_json(json.dumps(_json_value(payload), default=str))


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


def handle_api_errors(fn: Callable) -> Callable:
    """Convert ApiError / connection failures into clean CLI errors."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (ApiError, CliApiError) as exc:
            if exc.status_code == 401:
                msg = "Unauthorized (401): the API token is invalid or revoked."
            elif exc.status_code == 402:
                msg = (
                    "Payment required (402): billing denied this resource "
                    f"creation. body={exc.body!r}"
                )
            elif exc.status_code == 403:
                msg = f"Forbidden (403): the token is missing a required scope. body={exc.body!r}"
            elif exc.status_code == 404:
                msg = (
                    "Not found (404): this API route is not enabled on the gateway yet "
                    "(compute routes are rolling out) or the resource does not exist."
                )
            else:
                msg = f"API error {exc.status_code}: {exc.body!r}"
            typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        except Exception as exc:  # httpx connection errors etc.
            if type(exc).__module__.startswith("httpx"):
                typer.secho(f"Connection error: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)
            raise

    return wrapper
