"""ibee — the IBEE Solutions CLI."""

from __future__ import annotations

from typing import Optional

import typer

from . import __version__
from .commands import (
    billing,
    block_storage,
    buckets,
    cdn,
    compute,
    console,
    firewalls,
    gpus,
    load_balancers,
    networking,
    ops,
    reserved_ips,
    secrets,
    vms,
)
from .context import settings_from_env

app = typer.Typer(
    name="ibee",
    help=(
        "IBEE Solutions cloud platform CLI. Auth: set IBEE_TOKEN and IBEE_WORKSPACE_ID. "
        "Exit codes: 0 ok, 1 API/operation failure, 2 usage/validation, 3 --wait timeout."
    ),
    no_args_is_help=True,
)

app.add_typer(buckets.app, name="buckets")
app.add_typer(block_storage.app, name="block-storage")
app.add_typer(cdn.app, name="cdn")
app.add_typer(billing.app, name="billing")
app.add_typer(secrets.app, name="secrets")
app.add_typer(vms.app, name="vms")
app.add_typer(gpus.app, name="gpus")
app.add_typer(ops.app, name="ops")
app.add_typer(compute.app, name="compute")
app.add_typer(console.app, name="console")
app.add_typer(networking.app, name="vpcs")
app.add_typer(reserved_ips.app, name="reserved-ips")
app.add_typer(firewalls.app, name="firewalls")
app.add_typer(load_balancers.app, name="load-balancers")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ibee-cli {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    token: str = typer.Option(None, "--token", help="API token (default: IBEE_TOKEN env)"),
    workspace: str = typer.Option(
        None, "--workspace", "-w", help="Workspace ID (default: IBEE_WORKSPACE_ID env)"
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
        help="Output format: table, json, yaml or id (default: each command's usual format)",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output JSON (same as -o json)"),
    assume_yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Answer yes to every confirmation (env IBEE_ASSUME_YES=1)",
    ),
    check_billing: bool = typer.Option(
        False,
        "--check-billing",
        help=(
            "Deprecated no-op; upstream decides mutation admission "
            "(also applies to IBEE_CHECK_BILLING)"
        ),
    ),
    version: bool = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True, help="Show version"
    ),
) -> None:
    """IBEE Solutions cloud platform CLI.

    Exit codes: 0 success; 1 API, network or operation failure (or a declined
    confirmation); 2 usage or validation error; 3 --wait timed out while the
    operation was still running.
    """
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
        check_billing=check_billing,
    )


if __name__ == "__main__":
    app()
