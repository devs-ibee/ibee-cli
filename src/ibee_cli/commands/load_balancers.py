"""L4 and L7 load-balancer commands."""

from __future__ import annotations

from typing import Optional

import typer

from ..context import api_request, get_settings
from ..helpers import compact_payload, parse_json_array, parse_json_object
from ..render import handle_api_errors, print_json

app = typer.Typer(help="L4 and L7 load balancers", no_args_is_help=True)


def _call(
    ctx: typer.Context,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
    success: str | None = None,
) -> None:
    result = api_request(
        get_settings(ctx), method, path, params=params, json_body=payload
    )
    if result is not None:
        print_json(result)
    elif success:
        typer.secho(success, fg=typer.colors.GREEN)


def _optional_object(raw: Optional[str], option_name: str):
    return parse_json_object(raw, option_name) if raw is not None else None


def _optional_array(raw: Optional[str], option_name: str):
    return parse_json_array(raw, option_name) if raw is not None else None


def _payload(
    *,
    name: Optional[str],
    protocol: Optional[str] = None,
    backends: Optional[str],
    routing: Optional[str],
    tls: Optional[str],
    custom_domain: Optional[str] = None,
    rules: Optional[str] = None,
) -> dict:
    return compact_payload(
        name=name,
        protocol=protocol,
        backends=_optional_array(backends, "--backends"),
        routing=_optional_object(routing, "--routing"),
        tls=_optional_object(tls, "--tls"),
        custom_domain=(
            {"hostname": custom_domain} if custom_domain is not None else None
        ),
        rules=_optional_array(rules, "--rules"),
    )


@app.command("list")
@handle_api_errors
def list_load_balancers(
    ctx: typer.Context,
    status: Optional[str] = typer.Option(None, "--status"),
    layer: Optional[str] = typer.Option(None, "--layer", help="l4 or l7"),
    protocol: Optional[str] = typer.Option(None, "--protocol"),
    limit: int = typer.Option(100, "--limit", min=1, max=500),
    skip: int = typer.Option(0, "--skip", min=0),
) -> None:
    """List load balancers in the workspace."""

    _call(
        ctx,
        "GET",
        "networking/load-balancers",
        params={
            "status": status,
            "layer": layer,
            "protocol": protocol,
            "limit": limit,
            "skip": skip,
        },
    )


@app.command("create-l4")
@handle_api_errors
def create_l4_load_balancer(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Load-balancer name"),
    protocol: str = typer.Option("tcp", "--protocol", help="tcp or tls_passthrough"),
    backends: str = typer.Option(
        ...,
        "--backends",
        help='JSON array, e.g. \'[{"type":"ip","target":"10.0.0.5","port":80}]\'',
    ),
    routing: Optional[str] = typer.Option(None, "--routing", help="Routing JSON object"),
    tls: Optional[str] = typer.Option(None, "--tls", help="TLS JSON object"),
) -> None:
    """Create an L4 load balancer."""

    _call(
        ctx,
        "POST",
        "networking/load-balancers/l4",
        payload=_payload(
            name=name,
            protocol=protocol,
            backends=backends,
            routing=routing,
            tls=tls,
        ),
    )


@app.command("create-l7")
@handle_api_errors
def create_l7_load_balancer(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Load-balancer name"),
    protocol: str = typer.Option("http", "--protocol", help="http or https"),
    backends: str = typer.Option(
        ...,
        "--backends",
        help='JSON array, e.g. \'[{"type":"service","target":"api","port":8080}]\'',
    ),
    routing: Optional[str] = typer.Option(None, "--routing", help="Routing JSON object"),
    tls: Optional[str] = typer.Option(None, "--tls", help="TLS JSON object"),
    custom_domain: Optional[str] = typer.Option(None, "--custom-domain"),
    rules: Optional[str] = typer.Option(None, "--rules", help="Rules JSON array"),
) -> None:
    """Create an L7 load balancer."""

    _call(
        ctx,
        "POST",
        "networking/load-balancers/l7",
        payload=_payload(
            name=name,
            protocol=protocol,
            backends=backends,
            routing=routing,
            tls=tls,
            custom_domain=custom_domain,
            rules=rules,
        ),
    )


@app.command("get")
@handle_api_errors
def get_load_balancer(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
) -> None:
    """Show a load balancer."""

    _call(ctx, "GET", f"networking/load-balancers/{load_balancer_id}")


@app.command("update-l4")
@handle_api_errors
def update_l4_load_balancer(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    backends: Optional[str] = typer.Option(None, "--backends", help="Backends JSON array"),
    routing: Optional[str] = typer.Option(None, "--routing", help="Routing JSON object"),
    tls: Optional[str] = typer.Option(None, "--tls", help="TLS JSON object"),
) -> None:
    """Update an L4 load balancer."""

    payload = _payload(
        name=name, backends=backends, routing=routing, tls=tls
    )
    if not payload:
        raise typer.BadParameter("Provide at least one update option.")
    _call(
        ctx,
        "PATCH",
        f"networking/load-balancers/l4/{load_balancer_id}",
        payload=payload,
    )


@app.command("update-l7")
@handle_api_errors
def update_l7_load_balancer(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    backends: Optional[str] = typer.Option(None, "--backends", help="Backends JSON array"),
    routing: Optional[str] = typer.Option(None, "--routing", help="Routing JSON object"),
    tls: Optional[str] = typer.Option(None, "--tls", help="TLS JSON object"),
    custom_domain: Optional[str] = typer.Option(None, "--custom-domain"),
    rules: Optional[str] = typer.Option(None, "--rules", help="Rules JSON array"),
) -> None:
    """Update an L7 load balancer."""

    payload = _payload(
        name=name,
        backends=backends,
        routing=routing,
        tls=tls,
        custom_domain=custom_domain,
        rules=rules,
    )
    if not payload:
        raise typer.BadParameter("Provide at least one update option.")
    _call(
        ctx,
        "PATCH",
        f"networking/load-balancers/l7/{load_balancer_id}",
        payload=payload,
    )


@app.command("delete")
@handle_api_errors
def delete_load_balancer(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a load balancer."""

    if not yes:
        typer.confirm(f"Delete load balancer '{load_balancer_id}'?", abort=True)
    _call(
        ctx,
        "DELETE",
        f"networking/load-balancers/{load_balancer_id}",
        success=f"Load balancer '{load_balancer_id}' deletion accepted.",
    )


@app.command("status")
@handle_api_errors
def get_load_balancer_status(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
) -> None:
    """Show provisioning status and endpoint details."""

    _call(
        ctx, "GET", f"networking/load-balancers/{load_balancer_id}/status"
    )
