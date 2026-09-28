"""Shared CLI state: authentication, environment, and client construction."""

from __future__ import annotations

import functools
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import typer
from ibee import Ibee
from ibee.core.api_error import ApiError
from ibee.environment import IbeeEnvironment
from ibee.errors import error_from_response
from ibee.retry import (
    is_retry_safe,
    is_retryable_transport_error,
    retry_delay,
    should_retry_status,
)
from ibee.validation import (
    WORKSPACE_ID_ERROR,
    WORKSPACE_ID_PATTERN,
    IbeeValidationError,
    check_billable_body_size,
    resolve_base_url,
    validate_token_for_base_url,
)

PRODUCTION_API_BASE_URL = "https://api.ibee.ai/v1"
DEVELOPMENT_API_BASE_URL = "https://api.ibee.co.in/v1"
OUTPUT_MODES = ("table", "json", "yaml", "id")
TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes"})
#: Default retries for direct gateway requests (same default as the SDK).
DEFAULT_MAX_RETRIES = 2
REQUEST_TIMEOUT_SECONDS = 30

# Indirection so tests can skip real sleeps between retries.
_sleep = time.sleep

class CliApiError(ApiError):
    """Kept for import compatibility with 0.3.0.

    Direct gateway requests now raise the SDK's typed ``ApiError`` subclasses, so
    ``except CliApiError`` does not catch them; catch ``ApiError`` instead. The 0.3.0
    positional form ``CliApiError(status_code, body)`` still constructs an error.
    """

    def __init__(
        self,
        status_code: Optional[int] = None,
        body: Any = None,
        *,
        headers: Optional[dict] = None,
        **_ignored: Any,
    ) -> None:
        super().__init__(status_code=status_code, body=body, headers=headers)


@dataclass
class Settings:
    """Global CLI options.

    0.3.0 compatibility: the fifth positional argument was ``as_json`` (a bool); a bool
    passed there, or ``as_json=...`` as a keyword, maps to ``output='json'``.
    """

    token: Optional[str]
    workspace: Optional[str]
    dev: bool
    base_url: Optional[str]
    output: Optional[str] = None
    assume_yes: bool = False
    check_billing: bool = False
    env_name: Optional[str] = None
    endpoint: Optional[str] = None
    _resolved_base_url: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.output, bool):
            self.output = "json" if self.output else None

    @property
    def as_json(self) -> bool:
        """``True`` when JSON output was requested (``--json`` or ``-o json``)."""
        return self.output == "json"

    @as_json.setter
    def as_json(self, value: bool) -> None:
        if value:
            self.output = "json"
        elif self.output == "json":
            self.output = None

    @property
    def structured_output(self) -> bool:
        """``True`` for ``-o json|yaml|id``; commands then skip their tables."""
        return self.output in ("json", "yaml", "id")


_settings_dataclass_init = Settings.__init__


@functools.wraps(_settings_dataclass_init)
def _settings_init(self: Settings, *args: Any, as_json: Optional[bool] = None, **kwargs: Any) -> None:
    _settings_dataclass_init(self, *args, **kwargs)
    if as_json is not None:
        self.as_json = bool(as_json)


Settings.__init__ = _settings_init  # type: ignore[method-assign]


def get_settings(ctx: typer.Context) -> Settings:
    return ctx.obj


def is_truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUTHY_ENV_VALUES


def fail_usage(message: str) -> None:
    """Print a usage/validation error and exit 2."""
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=2)


def require_token(settings: Settings) -> str:
    if settings.token:
        return settings.token
    typer.secho(
        "No API token. Set IBEE_TOKEN (or pass --token). "
        "Create one in the portal under Settings > API Tokens.",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


def require_workspace(settings: Settings) -> str:
    if settings.workspace is not None:
        if WORKSPACE_ID_PATTERN.fullmatch(settings.workspace):
            return settings.workspace
        typer.secho(WORKSPACE_ID_ERROR, fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    typer.secho(
        "No workspace. Set IBEE_WORKSPACE_ID (or pass --workspace).",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


def api_base_url(settings: Settings) -> str:
    """Resolve and validate the public gateway base URL.

    Precedence: ``--base-url`` > ``IBEE_BASE_URL`` > ``IBEE_ENDPOINT`` > ``--dev`` >
    ``IBEE_ENV`` (``dev``/``development`` or ``prod``/``production``; default production).
    An invalid ``IBEE_ENV`` or an unsafe URL exits 2.
    """

    if settings._resolved_base_url is not None:
        return settings._resolved_base_url
    explicit = settings.base_url or settings.endpoint
    try:
        if explicit:
            resolved = resolve_base_url(explicit)
        elif settings.dev:
            resolved = resolve_base_url(environment=IbeeEnvironment.DEVELOPMENT)
        else:
            resolved = resolve_base_url(environment=IbeeEnvironment.from_name(settings.env_name))
    except IbeeValidationError as exc:
        fail_usage(exc.message)
    settings._resolved_base_url = resolved
    return resolved


def validated_token(settings: Settings) -> str:
    """The token, checked for CR/LF and against the endpoint's environment (exit 2 on mismatch)."""

    token = require_token(settings)
    base_url = api_base_url(settings)
    try:
        return validate_token_for_base_url(token, base_url)
    except IbeeValidationError as exc:
        fail_usage(exc.message)
    return token  # pragma: no cover - fail_usage always raises


def get_client(settings: Settings) -> Ibee:
    token = validated_token(settings)
    return Ibee(token=token, base_url=api_base_url(settings), timeout=REQUEST_TIMEOUT_SECONDS)


def _response_body(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        text = getattr(response, "text", "") or ""
        return text[:1000]


def api_request(
    settings: Settings,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Any:
    """Call a public API route using the CLI's shared auth and workspace state.

    Applies the SDK's client-side rules: workspace and token checks, the 64 KiB limit on
    billable create bodies, and the key-aware retry policy (reads, or writes carrying an
    idempotency key on a route that honours it, are retried on 429/502/503/504 and on
    network errors). Error responses raise the SDK's typed ``ApiError`` subclasses.
    """

    token = validated_token(settings)
    workspace = require_workspace(settings)
    query = {"workspace_id": workspace}
    query.update({key: value for key, value in (params or {}).items() if value is not None})
    relative_path = path.lstrip("/")
    check_billable_body_size(method, relative_path, json_body)
    headers = {
        "Authorization": f"Bearer {token}",
        "accept": "application/json",
        "content-type": "application/json",
    }
    retry_safe = is_retry_safe(method, relative_path, headers, json_body, query)
    idempotency_key = None
    for source in (json_body, query):
        if isinstance(source, dict) and source.get("idempotency_key"):
            idempotency_key = str(source["idempotency_key"])
            break
    url = f"{api_base_url(settings)}/{relative_path}"
    attempt = 0
    while True:
        try:
            response = httpx.request(
                method,
                url,
                params=query,
                json=json_body,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.TransportError as exc:
            if attempt < max_retries and is_retryable_transport_error(exc, retry_safe=retry_safe):
                _sleep(retry_delay(attempt))
                attempt += 1
                continue
            if idempotency_key:
                exc.idempotency_key = idempotency_key  # type: ignore[attr-defined]
            raise
        response_headers = getattr(response, "headers", None) or {}
        if retry_safe and should_retry_status(response.status_code) and attempt < max_retries:
            _sleep(retry_delay(attempt, response_headers))
            attempt += 1
            continue
        break
    if response.status_code >= 400:
        raise error_from_response(
            response.status_code,
            _response_body(response),
            dict(response_headers),
            idempotency_key,
            path=relative_path,
        )
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def normalize_output(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    mode = value.strip().lower()
    if mode not in OUTPUT_MODES:
        raise typer.BadParameter(f"must be one of: {', '.join(OUTPUT_MODES)}.")
    return mode


def settings_from_env(
    token: str | None,
    workspace: str | None,
    dev: bool,
    base_url: str | None,
    as_json: bool = False,
    *,
    output: str | None = None,
    assume_yes: bool = False,
    check_billing: bool = False,
) -> Settings:
    """Build settings from global options, falling back to environment variables."""

    mode = normalize_output(output)
    if as_json:
        if mode not in (None, "json"):
            raise typer.BadParameter(f"--json conflicts with -o {mode}.", param_hint="--json")
        mode = "json"
    return Settings(
        token=token or os.environ.get("IBEE_TOKEN") or os.environ.get("IBEE_API_TOKEN"),
        workspace=workspace if workspace is not None else os.environ.get("IBEE_WORKSPACE_ID"),
        dev=dev,
        base_url=base_url or os.environ.get("IBEE_BASE_URL"),
        output=mode,
        assume_yes=assume_yes or is_truthy_env("IBEE_ASSUME_YES"),
        check_billing=check_billing or is_truthy_env("IBEE_CHECK_BILLING"),
        env_name=os.environ.get("IBEE_ENV"),
        endpoint=os.environ.get("IBEE_ENDPOINT"),
    )
