"""L4 and L7 load-balancer commands.

The Python SDK applies the portal's rules (names, backends, routing, policy, health
checks, managed TLS only, L7 path rules, https-only custom domains) and supplies the
portal's managed TLS for tls_passthrough and https.
"""

from __future__ import annotations

from typing import Any, List, Optional

import typer
from ibee.validation import DEFAULT_RETRY_ON, PORTAL_HEALTH_CHECK_DEFAULT

from ..context import get_client, get_settings, require_workspace
from ..helpers import confirm_destructive, parse_json_array, parse_json_object
from ..render import emit, handle_api_errors, print_json

UNCONTRACTED = "Not yet part of the published API contract; behaviour may change."
ID_FIELD = "lb_id"
COLUMNS = ("lb_id", "name", "layer", "protocol", "status", "endpoint_url")
PORTAL_POLICY_TIMEOUT_MS = 30000
PORTAL_RETRY_ATTEMPTS = 3
PORTAL_PER_RETRY_TIMEOUT_MS = 5000

app = typer.Typer(help="L4 and L7 load balancers", no_args_is_help=True)


def _session(ctx: typer.Context) -> tuple[Any, str, Any]:
    settings = get_settings(ctx)
    workspace = require_workspace(settings)
    return settings, workspace, get_client(settings)


# ---------------------------------------------------------------------------
# Option parsing
# ---------------------------------------------------------------------------


def _int(text: str, what: str, raw: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        raise typer.BadParameter(f"{what} must be a whole number in {raw!r}.") from None


def parse_backend(raw: str) -> dict:
    """``TYPE:TARGET:PORT[:WEIGHT][:tls]``; IPv6 targets go in brackets (``ip:[2001:db8::1]:443``)."""

    text = raw.strip()
    tls = False
    if text.lower().endswith(":tls"):
        tls = True
        text = text[: -len(":tls")]
    kind, sep, rest = text.partition(":")
    if not sep or not kind.strip():
        raise typer.BadParameter(f"--backend expects TYPE:TARGET:PORT[:WEIGHT][:tls], got {raw!r}.")
    if rest.startswith("["):
        target, close, tail = rest[1:].partition("]")
        if not close or not tail.startswith(":"):
            raise typer.BadParameter(f"--backend expects [IPv6]:PORT, got {raw!r}.")
        numbers = tail[1:].split(":")
    else:
        pieces = rest.rsplit(":", 2)
        if len(pieces) == 3 and pieces[1].strip().isdigit() and pieces[2].strip().isdigit():
            target, numbers = pieces[0], pieces[1:]
        else:
            pieces = rest.rsplit(":", 1)
            if len(pieces) != 2:
                raise typer.BadParameter(f"--backend expects TYPE:TARGET:PORT[:WEIGHT][:tls], got {raw!r}.")
            target, numbers = pieces[0], pieces[1:]
    if len(numbers) not in (1, 2):
        raise typer.BadParameter(f"--backend expects TYPE:TARGET:PORT[:WEIGHT][:tls], got {raw!r}.")
    backend: dict[str, Any] = {
        "type": kind.strip().lower(),
        "target": target.strip(),
        "port": _int(numbers[0], "PORT", raw),
    }
    if len(numbers) == 2:
        backend["weight"] = _int(numbers[1], "WEIGHT", raw)
    if tls:
        backend["tls"] = True
    return backend


def parse_rule(raw: str) -> dict:
    """``PRIORITY:PATH_PREFIX[:HEADER=VALUE]``."""

    parts = raw.split(":", 2)
    if len(parts) < 2:
        raise typer.BadParameter(f"--rule expects PRIORITY:PATH_PREFIX[:HEADER=VALUE], got {raw!r}.")
    rule: dict[str, Any] = {"priority": _int(parts[0], "PRIORITY", raw), "path_prefix": parts[1].strip()}
    if len(parts) == 3 and parts[2].strip():
        name, eq, value = parts[2].partition("=")
        if not eq:
            raise typer.BadParameter(f"--rule header expects HEADER=VALUE, got {raw!r}.")
        rule["headers"] = {name.strip(): value.strip()}
    return rule


def _either(json_value: Optional[str], json_option: str, flag_value: Any, flag_option: str) -> None:
    if json_value is not None and flag_value:
        raise typer.BadParameter(f"Use {json_option} or {flag_option}, not both.")


def _backends(backends_json: Optional[str], backend: Optional[List[str]]) -> Optional[list]:
    _either(backends_json, "--backends", backend, "--backend")
    if backends_json is not None:
        return parse_json_array(backends_json, "--backends")
    if backend:
        return [parse_backend(item) for item in backend]
    return None


def _rules(rules_json: Optional[str], rule: Optional[List[str]]) -> Optional[list]:
    _either(rules_json, "--rules", rule, "--rule")
    if rules_json is not None:
        return parse_json_array(rules_json, "--rules")
    if rule:
        return [parse_rule(item) for item in rule]
    return None


def _routing(routing_json: Optional[str], algorithm: Optional[str], sticky_header: Optional[str]) -> Optional[dict]:
    _either(routing_json, "--routing", algorithm is not None or sticky_header is not None,
            "--algorithm/--sticky-header")
    if routing_json is not None:
        return parse_json_object(routing_json, "--routing")
    routing: dict[str, Any] = {}
    if algorithm is not None:
        routing["algorithm"] = algorithm
    if sticky_header is not None:
        routing["sticky_header"] = sticky_header
    return routing or None


def _policy(
    policy_json: Optional[str],
    timeout_ms: Optional[int],
    retries: Optional[int],
    per_retry_timeout_ms: Optional[int],
    retry_on: Optional[List[str]],
    proxy_protocol: Optional[bool],
) -> Optional[dict]:
    flags = any(v is not None for v in (timeout_ms, retries, per_retry_timeout_ms, proxy_protocol)) or bool(retry_on)
    _either(policy_json, "--policy", flags, "the policy options")
    if policy_json is not None:
        return parse_json_object(policy_json, "--policy")
    if not flags:
        return None
    policy: dict[str, Any] = {
        "timeout_ms": PORTAL_POLICY_TIMEOUT_MS if timeout_ms is None else timeout_ms,
        "proxy_protocol_enabled": bool(proxy_protocol),
    }
    if retries is not None or per_retry_timeout_ms is not None or retry_on:
        policy["retries"] = {
            "attempts": PORTAL_RETRY_ATTEMPTS if retries is None else retries,
            "per_retry_timeout_ms": PORTAL_PER_RETRY_TIMEOUT_MS if per_retry_timeout_ms is None else per_retry_timeout_ms,
            "on": list(retry_on) if retry_on else list(DEFAULT_RETRY_ON),
        }
    return policy


def _health_check(
    health_json: Optional[str],
    kind: Optional[str],
    path: Optional[str],
    interval_ms: Optional[int],
    timeout_ms: Optional[int],
    healthy: Optional[int],
    unhealthy: Optional[int],
) -> Optional[dict]:
    flags = any(v is not None for v in (kind, path, interval_ms, timeout_ms, healthy, unhealthy))
    _either(health_json, "--health-check", flags, "the --health-check-* options")
    if health_json is not None:
        return parse_json_object(health_json, "--health-check")
    if not flags:
        return None
    active = dict(PORTAL_HEALTH_CHECK_DEFAULT["active"])
    active["type"] = (kind or "http").strip().lower()
    if active["type"] == "tcp":
        active.pop("path", None)
    for key, value in (
        ("path", path),
        ("interval_ms", interval_ms),
        ("timeout_ms", timeout_ms),
        ("healthy_threshold", healthy),
        ("unhealthy_threshold", unhealthy),
    ):
        if value is not None:
            active[key] = value
    return {"active": active}


def _observability(logs: Optional[bool]) -> Optional[dict]:
    return None if logs is None else {"logs_enabled": logs}


def _optional_object(raw: Optional[str], option: str) -> Optional[dict]:
    return parse_json_object(raw, option) if raw is not None else None


# Shared option declarations -------------------------------------------------

BACKEND_HELP = (
    "Backend TYPE:TARGET:PORT[:WEIGHT][:tls] (repeatable). TYPE is service, ip or hostname; "
    "IPv6 targets go in brackets; WEIGHT 1-1000 (default 100)"
)
BACKENDS_JSON_HELP = 'Backends JSON array, e.g. \'[{"type":"ip","target":"10.0.0.5","port":80}]\''


def _backend_opt() -> Any:
    return typer.Option(None, "--backend", help=BACKEND_HELP)


def _backends_json_opt() -> Any:
    return typer.Option(None, "--backends", help=BACKENDS_JSON_HELP)


def _algorithm_opt() -> Any:
    return typer.Option(
        None, "--algorithm", help="round_robin, least_request, random or consistent_hash"
    )


def _routing_opt() -> Any:
    return typer.Option(None, "--routing", help="Routing JSON object (instead of --algorithm/--sticky-header)")


POLICY_NOTE = f"{UNCONTRACTED}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("list")
@handle_api_errors
def list_load_balancers(
    ctx: typer.Context,
    status: Optional[str] = typer.Option(
        None, "--status", help="provisioning, active, failed, deleting or deleted"
    ),
    layer: Optional[str] = typer.Option(None, "--layer", help="l4 or l7"),
    protocol: Optional[str] = typer.Option(
        None, "--protocol", help="tcp or tls_passthrough (l4); http or https (l7)"
    ),
    limit: Optional[int] = typer.Option(None, "--limit", help="1-500 (default 100)"),
    skip: Optional[int] = typer.Option(None, "--skip", help="Skip N load balancers"),
    include_deleted: bool = typer.Option(
        False, "--include-deleted", help=f"Include deleted load balancers (implied by --status deleted). {UNCONTRACTED}"
    ),
) -> None:
    """List load balancers in the workspace."""

    settings, workspace, client = _session(ctx)
    items = client.load_balancers.list_load_balancers(
        workspace_id=workspace,
        status=status,
        layer=layer,
        protocol=protocol,
        limit=limit,
        skip=skip,
        include_deleted=True if include_deleted else None,
    )
    emit(items, settings=settings, default="json", columns=COLUMNS, title="Load balancers", id_field=ID_FIELD)


def _create(
    ctx: typer.Context,
    layer: str,
    check_billing: bool,
    **fields: Any,
) -> None:
    settings, workspace, client = _session(ctx)
    method = getattr(client.load_balancers, f"create_{layer}load_balancer")
    lb = method(workspace_id=workspace, check_billing=check_billing or settings.check_billing,
                **{key: value for key, value in fields.items() if value is not None})
    print_json(lb, id_field=ID_FIELD)


def _update(ctx: typer.Context, layer: str, load_balancer_id: str, **fields: Any) -> None:
    _settings, workspace, client = _session(ctx)
    method = getattr(client.load_balancers, f"update_{layer}load_balancer")
    lb = method(load_balancer_id, workspace_id=workspace, **fields)
    print_json(lb, id_field=ID_FIELD)


def _require_backends(backends: Optional[list]) -> list:
    if backends is None:
        raise typer.BadParameter("Provide --backend (repeatable) or --backends JSON.")
    return backends


@app.command("create-l4")
@handle_api_errors
def create_l4_load_balancer(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Load-balancer name (1-128 characters)"),
    protocol: str = typer.Option("tcp", "--protocol", help="tcp or tls_passthrough (managed TLS passthrough)"),
    backend: Optional[List[str]] = _backend_opt(),
    backends: Optional[str] = _backends_json_opt(),
    algorithm: Optional[str] = _algorithm_opt(),
    routing: Optional[str] = _routing_opt(),
    timeout_ms: Optional[int] = typer.Option(None, "--timeout-ms", help="Request timeout 100-300000 ms (portal 30000)"),
    retries: Optional[int] = typer.Option(None, "--retries", help="Retry attempts 1-10 (portal 3)"),
    per_retry_timeout_ms: Optional[int] = typer.Option(
        None, "--per-retry-timeout-ms", help="100-120000 ms (portal 5000)"
    ),
    retry_on: Optional[List[str]] = typer.Option(
        None, "--retry-on", help="Retry condition (repeatable; default 5xx, reset, connect-failure)"
    ),
    proxy_protocol: Optional[bool] = typer.Option(
        None, "--proxy-protocol/--no-proxy-protocol", help="Send the PROXY protocol header to backends"
    ),
    policy: Optional[str] = typer.Option(None, "--policy", help=f"Policy JSON object. {POLICY_NOTE}"),
    health_check_type: Optional[str] = typer.Option(
        None, "--health-check-type", help=f"Active health check: http, https or tcp. {POLICY_NOTE}"
    ),
    health_check_path: Optional[str] = typer.Option(None, "--health-check-path", help="Path (http/https; default /health)"),
    health_check_interval_ms: Optional[int] = typer.Option(
        None, "--health-check-interval-ms", help="100-120000 (default 10000)"
    ),
    health_check_timeout_ms: Optional[int] = typer.Option(
        None, "--health-check-timeout-ms", help="100-120000 (default 2000)"
    ),
    healthy_threshold: Optional[int] = typer.Option(None, "--healthy-threshold", help="1-20 (default 2)"),
    unhealthy_threshold: Optional[int] = typer.Option(None, "--unhealthy-threshold", help="1-20 (default 3)"),
    health_check: Optional[str] = typer.Option(None, "--health-check", help="Health-check JSON object"),
    logs: Optional[bool] = typer.Option(None, "--logs/--no-logs", help=f"Access logs. {POLICY_NOTE}"),
    tls: Optional[str] = typer.Option(
        None, "--tls", help="TLS JSON object (managed certificates only; default for tls_passthrough)"
    ),
    check_billing: bool = typer.Option(
        False, "--check-billing", help="Check LOADBALA-STD billing eligibility first (same as the global option)"
    ),
) -> None:
    """Create an L4 load balancer (TCP or TLS passthrough)."""

    _create(
        ctx,
        "l4",
        check_billing,
        name=name,
        protocol=protocol,
        backends=_require_backends(_backends(backends, backend)),
        routing=_routing(routing, algorithm, None),
        tls=_optional_object(tls, "--tls"),
        policy=_policy(policy, timeout_ms, retries, per_retry_timeout_ms, retry_on, proxy_protocol),
        health_check=_health_check(
            health_check, health_check_type, health_check_path, health_check_interval_ms,
            health_check_timeout_ms, healthy_threshold, unhealthy_threshold,
        ),
        observability=_observability(logs),
    )


@app.command("create-l7")
@handle_api_errors
def create_l7_load_balancer(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Load-balancer name (1-128 characters)"),
    protocol: str = typer.Option(
        "http",
        "--protocol",
        help="http (default, kept from 0.3.0) or https (the portal's default; managed certificate)",
    ),
    backend: Optional[List[str]] = _backend_opt(),
    backends: Optional[str] = _backends_json_opt(),
    algorithm: Optional[str] = _algorithm_opt(),
    sticky_header: Optional[str] = typer.Option(
        None, "--sticky-header", help="Sticky sessions keyed on this request header (portal example X-User-ID)"
    ),
    routing: Optional[str] = _routing_opt(),
    timeout_ms: Optional[int] = typer.Option(None, "--timeout-ms", help="Request timeout 100-300000 ms (portal 30000)"),
    retries: Optional[int] = typer.Option(None, "--retries", help="Retry attempts 1-10 (portal 3)"),
    per_retry_timeout_ms: Optional[int] = typer.Option(
        None, "--per-retry-timeout-ms", help="100-120000 ms (portal 5000)"
    ),
    retry_on: Optional[List[str]] = typer.Option(
        None, "--retry-on", help="Retry condition (repeatable; default 5xx, reset, connect-failure)"
    ),
    proxy_protocol: Optional[bool] = typer.Option(None, "--proxy-protocol/--no-proxy-protocol"),
    policy: Optional[str] = typer.Option(None, "--policy", help=f"Policy JSON object. {POLICY_NOTE}"),
    health_check_type: Optional[str] = typer.Option(
        None, "--health-check-type", help=f"Active health check: http, https or tcp. {POLICY_NOTE}"
    ),
    health_check_path: Optional[str] = typer.Option(None, "--health-check-path", help="Path (default /health)"),
    health_check_interval_ms: Optional[int] = typer.Option(None, "--health-check-interval-ms"),
    health_check_timeout_ms: Optional[int] = typer.Option(None, "--health-check-timeout-ms"),
    healthy_threshold: Optional[int] = typer.Option(None, "--healthy-threshold"),
    unhealthy_threshold: Optional[int] = typer.Option(None, "--unhealthy-threshold"),
    health_check: Optional[str] = typer.Option(None, "--health-check", help="Health-check JSON object"),
    logs: Optional[bool] = typer.Option(None, "--logs/--no-logs", help=f"Access logs. {POLICY_NOTE}"),
    tls: Optional[str] = typer.Option(None, "--tls", help="TLS JSON object (managed only; default for https)"),
    custom_domain: Optional[str] = typer.Option(
        None, "--custom-domain", help="Hostname (https only); its CNAME must already point at IBEE"
    ),
    rule: Optional[List[str]] = typer.Option(
        None, "--rule", help="Path rule PRIORITY:PATH_PREFIX[:HEADER=VALUE] (repeatable)"
    ),
    rules: Optional[str] = typer.Option(None, "--rules", help="Rules JSON array"),
    check_billing: bool = typer.Option(
        False, "--check-billing", help="Check LOADBALA-STD billing eligibility first (same as the global option)"
    ),
) -> None:
    """Create an L7 load balancer (HTTP or HTTPS)."""

    _create(
        ctx,
        "l7",
        check_billing,
        name=name,
        protocol=protocol,
        backends=_require_backends(_backends(backends, backend)),
        routing=_routing(routing, algorithm, sticky_header),
        tls=_optional_object(tls, "--tls"),
        custom_domain={"hostname": custom_domain} if custom_domain is not None else None,
        rules=_rules(rules, rule),
        policy=_policy(policy, timeout_ms, retries, per_retry_timeout_ms, retry_on, proxy_protocol),
        health_check=_health_check(
            health_check, health_check_type, health_check_path, health_check_interval_ms,
            health_check_timeout_ms, healthy_threshold, unhealthy_threshold,
        ),
        observability=_observability(logs),
    )


@app.command("get")
@handle_api_errors
def get_load_balancer(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
    include_deleted: bool = typer.Option(
        False, "--include-deleted", help=f"Also find a deleted load balancer. {UNCONTRACTED}"
    ),
) -> None:
    """Show a load balancer (routing, policy, health check and TLS are not returned by the API)."""

    _settings, workspace, client = _session(ctx)
    lb = client.load_balancers.get_load_balancer(
        load_balancer_id, workspace_id=workspace, include_deleted=True if include_deleted else None
    )
    print_json(lb, id_field=ID_FIELD)


def _update_fields(
    *,
    name: Optional[str],
    backend: Optional[List[str]],
    backends: Optional[str],
    algorithm: Optional[str],
    sticky_header: Optional[str],
    routing: Optional[str],
    timeout_ms: Optional[int],
    retries: Optional[int],
    per_retry_timeout_ms: Optional[int],
    retry_on: Optional[List[str]],
    proxy_protocol: Optional[bool],
    policy: Optional[str],
    health: tuple,
    logs: Optional[bool],
    tls: Optional[str],
) -> dict[str, Any]:
    fields = {
        "name": name,
        "backends": _backends(backends, backend),
        "routing": _routing(routing, algorithm, sticky_header),
        "tls": _optional_object(tls, "--tls"),
        "policy": _policy(policy, timeout_ms, retries, per_retry_timeout_ms, retry_on, proxy_protocol),
        "health_check": _health_check(*health),
        "observability": _observability(logs),
    }
    return {key: value for key, value in fields.items() if value is not None}


@app.command("update-l4")
@handle_api_errors
def update_l4_load_balancer(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    backend: Optional[List[str]] = _backend_opt(),
    backends: Optional[str] = typer.Option(None, "--backends", help="Backends JSON array"),
    algorithm: Optional[str] = _algorithm_opt(),
    routing: Optional[str] = _routing_opt(),
    timeout_ms: Optional[int] = typer.Option(None, "--timeout-ms"),
    retries: Optional[int] = typer.Option(None, "--retries"),
    per_retry_timeout_ms: Optional[int] = typer.Option(None, "--per-retry-timeout-ms"),
    retry_on: Optional[List[str]] = typer.Option(None, "--retry-on"),
    proxy_protocol: Optional[bool] = typer.Option(None, "--proxy-protocol/--no-proxy-protocol"),
    policy: Optional[str] = typer.Option(None, "--policy", help="Policy JSON object (replaces the policy)"),
    health_check_type: Optional[str] = typer.Option(None, "--health-check-type"),
    health_check_path: Optional[str] = typer.Option(None, "--health-check-path"),
    health_check_interval_ms: Optional[int] = typer.Option(None, "--health-check-interval-ms"),
    health_check_timeout_ms: Optional[int] = typer.Option(None, "--health-check-timeout-ms"),
    healthy_threshold: Optional[int] = typer.Option(None, "--healthy-threshold"),
    unhealthy_threshold: Optional[int] = typer.Option(None, "--unhealthy-threshold"),
    health_check: Optional[str] = typer.Option(None, "--health-check", help="Health-check JSON object"),
    logs: Optional[bool] = typer.Option(None, "--logs/--no-logs"),
    tls: Optional[str] = typer.Option(None, "--tls", help="TLS JSON object (managed passthrough only)"),
) -> None:
    """Update an L4 load balancer (at least one option).

    Policy and health-check options replace the whole policy or health check; values you
    leave out use the portal defaults.
    """

    _update(
        ctx,
        "l4",
        load_balancer_id,
        **_update_fields(
            name=name, backend=backend, backends=backends, algorithm=algorithm, sticky_header=None,
            routing=routing, timeout_ms=timeout_ms, retries=retries, per_retry_timeout_ms=per_retry_timeout_ms,
            retry_on=retry_on, proxy_protocol=proxy_protocol, policy=policy,
            health=(health_check, health_check_type, health_check_path, health_check_interval_ms,
                    health_check_timeout_ms, healthy_threshold, unhealthy_threshold),
            logs=logs, tls=tls,
        ),
    )


@app.command("update-l7")
@handle_api_errors
def update_l7_load_balancer(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
    name: Optional[str] = typer.Option(None, "--name"),
    backend: Optional[List[str]] = _backend_opt(),
    backends: Optional[str] = typer.Option(None, "--backends", help="Backends JSON array"),
    algorithm: Optional[str] = _algorithm_opt(),
    sticky_header: Optional[str] = typer.Option(None, "--sticky-header"),
    routing: Optional[str] = _routing_opt(),
    timeout_ms: Optional[int] = typer.Option(None, "--timeout-ms"),
    retries: Optional[int] = typer.Option(None, "--retries"),
    per_retry_timeout_ms: Optional[int] = typer.Option(None, "--per-retry-timeout-ms"),
    retry_on: Optional[List[str]] = typer.Option(None, "--retry-on"),
    proxy_protocol: Optional[bool] = typer.Option(None, "--proxy-protocol/--no-proxy-protocol"),
    policy: Optional[str] = typer.Option(None, "--policy", help="Policy JSON object (replaces the policy)"),
    health_check_type: Optional[str] = typer.Option(None, "--health-check-type"),
    health_check_path: Optional[str] = typer.Option(None, "--health-check-path"),
    health_check_interval_ms: Optional[int] = typer.Option(None, "--health-check-interval-ms"),
    health_check_timeout_ms: Optional[int] = typer.Option(None, "--health-check-timeout-ms"),
    healthy_threshold: Optional[int] = typer.Option(None, "--healthy-threshold"),
    unhealthy_threshold: Optional[int] = typer.Option(None, "--unhealthy-threshold"),
    health_check: Optional[str] = typer.Option(None, "--health-check", help="Health-check JSON object"),
    logs: Optional[bool] = typer.Option(None, "--logs/--no-logs"),
    tls: Optional[str] = typer.Option(None, "--tls", help="TLS JSON object (managed terminate only)"),
    custom_domain: Optional[str] = typer.Option(
        None, "--custom-domain", help="Hostname (https only); its CNAME must already point at IBEE"
    ),
    clear_custom_domain: bool = typer.Option(False, "--clear-custom-domain", help="Remove the custom domain"),
    rule: Optional[List[str]] = typer.Option(None, "--rule", help="PRIORITY:PATH_PREFIX[:HEADER=VALUE] (repeatable)"),
    rules: Optional[str] = typer.Option(None, "--rules", help="Rules JSON array"),
) -> None:
    """Update an L7 load balancer (at least one option).

    Policy and health-check options replace the whole policy or health check; values you
    leave out use the portal defaults.
    """

    if clear_custom_domain and custom_domain is not None:
        raise typer.BadParameter("Use --custom-domain or --clear-custom-domain, not both.")
    fields = _update_fields(
        name=name, backend=backend, backends=backends, algorithm=algorithm, sticky_header=sticky_header,
        routing=routing, timeout_ms=timeout_ms, retries=retries, per_retry_timeout_ms=per_retry_timeout_ms,
        retry_on=retry_on, proxy_protocol=proxy_protocol, policy=policy,
        health=(health_check, health_check_type, health_check_path, health_check_interval_ms,
                health_check_timeout_ms, healthy_threshold, unhealthy_threshold),
        logs=logs, tls=tls,
    )
    parsed_rules = _rules(rules, rule)
    if parsed_rules is not None:
        fields["rules"] = parsed_rules
    if clear_custom_domain:
        fields["custom_domain"] = None
    elif custom_domain is not None:
        fields["custom_domain"] = {"hostname": custom_domain}
    _update(ctx, "l7", load_balancer_id, **fields)


@app.command("delete")
@handle_api_errors
def delete_load_balancer(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a load balancer."""

    settings, workspace, client = _session(ctx)
    confirm_destructive(settings, f"Delete load balancer '{load_balancer_id}'?", yes)
    client.load_balancers.delete_load_balancer(load_balancer_id, workspace_id=workspace)
    if not settings.structured_output:
        typer.secho(f"Load balancer '{load_balancer_id}' deletion accepted.", fg=typer.colors.GREEN)


@app.command("status")
@handle_api_errors
def get_load_balancer_status(
    ctx: typer.Context,
    load_balancer_id: str = typer.Argument(..., help="Load-balancer ID"),
) -> None:
    """Show provisioning status and endpoint details."""

    _settings, workspace, client = _session(ctx)
    print_json(client.load_balancers.get_load_balancer_status(load_balancer_id, workspace_id=workspace),
               id_field="lb_id")
