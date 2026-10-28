"""ibee — the IBEE Solutions CLI."""

from __future__ import annotations

from typing import Optional

import typer

from . import __version__
from .commands import GROUPS
from .context import settings_from_env

EXIT_CODES_HELP = (
    "Exit codes: 0 ok; 1 other error or declined confirmation; 2 usage or configuration error (nothing sent); "
    "4 network error; 10-18 the API refused the request (400, 401, 402, 403, 404, 409, 422, 423, 429); 19 5xx."
)

app = typer.Typer(
    name="ibee",
    help=(
        "IBEE Solutions cloud platform CLI: one command per public API operation, printing the server's answer. "
        "Auth: set IBEE_TOKEN and IBEE_WORKSPACE_ID. " + EXIT_CODES_HELP
    ),
    no_args_is_help=True,
)

for _name, _group in GROUPS:
    app.add_typer(_group, name=_name)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ibee-cli {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    token: str = typer.Option(None, "--token", help="API token (default: IBEE_TOKEN env)"),
    workspace: str = typer.Option(
        None, "--workspace", "-w", help="Workspace ID, sent as workspace_id (default: IBEE_WORKSPACE_ID env)"
    ),
    dev: bool = typer.Option(False, "--dev", help="Use the development environment"),
    base_url: str = typer.Option(
        None,
        "--base-url",
        help="Override the API base URL (default: IBEE_BASE_URL, then IBEE_ENDPOINT, then IBEE_ENV)",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        envvar="IBEE_OUTPUT",
        help="Output format: json (default), yaml, table or id",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output JSON (same as -o json)"),
    assume_yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Answer yes to every confirmation (env IBEE_ASSUME_YES=1)",
    ),
    version: bool = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True, help="Show version"
    ),
) -> None:
    """IBEE Solutions cloud platform CLI."""
    if as_json and output is not None:
        source = ctx.get_parameter_source("output")
        if source is not None and source.name == "ENVIRONMENT":
            output = None
    ctx.obj = settings_from_env(
        token,
        workspace,
        dev,
        base_url,
        as_json,
        output=output,
        assume_yes=assume_yes,
    )


if __name__ == "__main__":
    app()
