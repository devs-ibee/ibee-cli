"""ibee — the IBEE Solutions CLI."""

from __future__ import annotations

import typer

from . import __version__
from .commands import (
    buckets,
    compute,
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
    help="IBEE Solutions cloud platform CLI. Auth: set IBEE_TOKEN and IBEE_WORKSPACE_ID.",
    no_args_is_help=True,
)

app.add_typer(buckets.app, name="buckets")
app.add_typer(secrets.app, name="secrets")
app.add_typer(vms.app, name="vms")
app.add_typer(gpus.app, name="gpus")
app.add_typer(ops.app, name="ops")
app.add_typer(compute.app, name="compute")
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
    base_url: str = typer.Option(None, "--base-url", help="Override the API base URL"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
    version: bool = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True, help="Show version"
    ),
) -> None:
    ctx.obj = settings_from_env(token, workspace, dev, base_url, as_json)


if __name__ == "__main__":
    app()
