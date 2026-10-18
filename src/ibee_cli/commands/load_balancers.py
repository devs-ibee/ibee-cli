"""L4 and L7 load-balancer commands.

Each command makes one SDK call and prints the server's answer. Backends, routing,
TLS and rules are sent as the JSON you give; the server applies its own defaults and
rules.
"""

from __future__ import annotations

from typing import Any, List, Optional

import typer

from ..context import get_client, get_settings
from ..helpers import compact_payload, confirm_destructive, parse_json_array, parse_json_object
from ..render import emit, handle_api_errors, print_json

ID_FIELD = "lb_id"
COLUMNS = ("lb_id", "name", "layer", "protocol", "status", "endpoint_url")

app = typer.Typer(help="L4 and L7 load balancers", no_args_is_help=True)


def _lbs(ctx: typer.Context) -> tuple[Any, Any]:
    settings = get_settings(ctx)
    return settings, get_client(settings).load_balancers


# -- option parsing (JSON only) -----------------------------------------------------


def _items(single: Optional[List[str]], many: Optional[str], flag: str) -> Optional[list]:
    """``--<flag>s JSON_ARRAY`` or ``--<flag> JSON_OBJECT`` (repeatable), not both."""
    if single and many is not None:
        raise typer.BadParameter(f"Use --{flag}s or --{flag}, not both.")
    if many is not None:
        return parse_json_array(many, f"--{flag}s")
    return [parse_json_object(value, f"--{flag}") for value in single] if single else None


def _routing(routing: Optional[str], algorithm: Optional[str], sticky_header: Optional[str]) -> Optional[dict]:
    if routing is not None and (algorithm is not None or sticky_header is not None):
        raise typer.BadParameter("Use --routing or --algorithm/--sticky-header, not both.")
    if routing is not None:
        return parse_json_object(routing, "--routing")
    return compact_payload(algorithm=algorithm, sticky_header=sticky_header) or None


def _fields(
    backend: Optional[List[str]], backends: Optional[str], routing: Optional[str], algorithm: Optional[str],
    sticky_header: Optional[str], tls: Optional[str], **extra: Any,
) -> dict:
    return compact_payload(
        backends=_items(backend, backends, "backend"),
        routing=_routing(routing, algorithm, sticky_header),
        tls=parse_json_object(tls, "--tls") if tls is not None else None,
        **extra,
    )


def _l7_extra(custom_domain: Optional[str], rule: Optional[List[str]], rules: Optional[str]) -> dict:
    return {"custom_domain": {"hostname": custom_domain} if custom_domain is not None else None, "rules": _items(rule, rules, "rule")}


def _backend() -> Any:
    example = '{"type":"ip","target":"10.0.0.5","port":80}'
    return typer.Option(None, "--backend", metavar="JSON", help=f"One backend JSON object (repeatable), e.g. '{example}'")


def _backends() -> Any:
    return typer.Option(None, "--backends", metavar="JSON", help="Backends JSON array")


def _algorithm() -> Any:
    return typer.Option(None, "--algorithm", help="routing.algorithm, for example round_robin")


def _sticky_header() -> Any:
    return typer.Option(None, "--sticky-header", help="routing.sticky_header")


def _routing_json() -> Any:
    return typer.Option(None, "--routing", metavar="JSON", help="Routing JSON object (instead of --algorithm/--sticky-header)")


def _tls() -> Any:
    return typer.Option(None, "--tls", metavar="JSON", help="TLS JSON object")


def _custom_domain() -> Any:
    return typer.Option(None, "--custom-domain", help="Hostname, sent as custom_domain.hostname")


def _rule() -> Any:
    return typer.Option(None, "--rule", metavar="JSON", help="One rule JSON object (repeatable)")


def _rules() -> Any:
    return typer.Option(None, "--rules", metavar="JSON", help="Rules JSON array")


def _create(ctx: typer.Context, layer: str, name: str, protocol: str, fields: dict) -> None:
    if fields.get("backends") is None:
        raise typer.BadParameter("Provide --backends JSON or --backend JSON (repeatable).")
    method = getattr(_lbs(ctx)[1], f"create_{layer}load_balancer")
    print_json(method(name=name, protocol=protocol, **fields), id_field=ID_FIELD)


def _update(ctx: typer.Context, layer: str, load_balancer_id: str, fields: dict) -> None:
    method = getattr(_lbs(ctx)[1], f"update_{layer}load_balancer")
    print_json(method(load_balancer_id, **fields), id_field=ID_FIELD)


# -- commands -----------------------------------------------------------------------


@app.command("list")
@handle_api_errors
def list_load_balancers(
    ctx: typer.Context,
    status: Optional[str] = typer.Option(None, "--status"),
    layer: Optional[str] = typer.Option(None, "--layer", help="l4 or l7"),
    protocol: Optional[str] = typer.Option(None, "--protocol"),
    limit: Optional[int] = typer.Option(None, "--limit"),
    skip: Optional[int] = typer.Option(None, "--skip", help="Skip N load balancers"),
) -> None:
    """List load balancers in the workspace."""
    settings, lbs = _lbs(ctx)
    items = lbs.list_load_balancers(**compact_payload(status=status, layer=layer, protocol=protocol, limit=limit, skip=skip))
    emit(items, settings=settings, default="json", columns=COLUMNS, title="Load balancers", id_field=ID_FIELD)


@app.command("create-l4")
@handle_api_errors
def create_l4_load_balancer(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Load-balancer name"),
    protocol: str = typer.Option("tcp", "--protocol", help="For example tcp or tls_passthrough"),
    backend: Optional[List[str]] = _backend(),
    backends: Optional[str] = _backends(),
    algorithm: Optional[str] = _algorithm(),
    routing: Optional[str] = _routing_json(),
    tls: Optional[str] = _tls(),
) -> None:
    """Create an L4 load balancer."""
    _create(ctx, "l4", name, protocol, _fields(backend, backends, routing, algorithm, None, tls))


@app.command("create-l7")
@handle_api_errors
def create_l7_load_balancer(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Load-balancer name"),
    protocol: str = typer.Option("http", "--protocol", help="For example http or https"),
    backend: Optional[List[str]] = _backend(),
    backends: Optional[str] = _backends(),
    algorithm: Optional[str] = _algorithm(),
    sticky_header: Optional[str] = _sticky_header(),
    routing: Optional[str] = _routing_json(),
    tls: Optional[str] = _tls(),
    custom_domain: Optional[str] = _custom_domain(),
    rule: Optional[List[str]] = _rule(),
    rules: Optional[str] = _rules(),
) -> None:
    """Create an L7 load balancer."""
    fields = _fields(backend, backends, routing, algorithm, sticky_header, tls, **_l7_extra(custom_domain, rule, rules))
    _create(ctx, "l7", name, protocol, fields)


@app.command("get")
@handle_api_errors
def get_load_balancer(ctx: typer.Context, load_balancer_id: str = typer.Argument(..., help="Load-balancer ID")) -> None:
    """Show a load balancer."""
    print_json(_lbs(ctx)[1].get_load_balancer(load_balancer_id), id_field=ID_FIELD)


@app.command("update-l4")
@handle_api_errors
def update_l4_load_balancer(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    backend: Optional[List[str]] = _backend(),
    backends: Optional[str] = _backends(),
    algorithm: Optional[str] = _algorithm(),
    routing: Optional[str] = _routing_json(),
    tls: Optional[str] = _tls(),
) -> None:
    """Update an L4 load balancer."""
    _update(ctx, "l4", load_balancer_id, _fields(backend, backends, routing, algorithm, None, tls, name=name))


@app.command("update-l7")
@handle_api_errors
def update_l7_load_balancer(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    backend: Optional[List[str]] = _backend(),
    backends: Optional[str] = _backends(),
    algorithm: Optional[str] = _algorithm(),
    sticky_header: Optional[str] = _sticky_header(),
    routing: Optional[str] = _routing_json(),
    tls: Optional[str] = _tls(),
    custom_domain: Optional[str] = _custom_domain(),
    clear_custom_domain: bool = typer.Option(False, "--clear-custom-domain", help="Send custom_domain: null"),
    rule: Optional[List[str]] = _rule(),
    rules: Optional[str] = _rules(),
) -> None:
    """Update an L7 load balancer."""
    if clear_custom_domain and custom_domain is not None:
        raise typer.BadParameter("Use --custom-domain or --clear-custom-domain, not both.")
    fields = _fields(backend, backends, routing, algorithm, sticky_header, tls, name=name, **_l7_extra(custom_domain, rule, rules))
    if clear_custom_domain:
        fields["custom_domain"] = None
    _update(ctx, "l7", load_balancer_id, fields)


@app.command("delete")
@handle_api_errors
def delete_load_balancer(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a load balancer."""
    settings, lbs = _lbs(ctx)
    confirm_destructive(settings, f"Delete load balancer '{load_balancer_id}'?", yes)
    lbs.delete_load_balancer(load_balancer_id)
    if not settings.structured_output:
        typer.secho(f"Load balancer '{load_balancer_id}' deletion accepted.", fg=typer.colors.GREEN)


@app.command("status")
@handle_api_errors
def get_load_balancer_status(ctx: typer.Context, load_balancer_id: str = typer.Argument(..., help="Load-balancer ID")) -> None:
    """Show provisioning status and endpoint details."""
    print_json(_lbs(ctx)[1].get_load_balancer_status(load_balancer_id), id_field=ID_FIELD)
