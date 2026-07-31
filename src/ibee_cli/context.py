"""Shared CLI state: authentication, environment, and client construction."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
import typer
from ibee import Ibee
from ibee.environment import IbeeEnvironment

PRODUCTION_API_BASE_URL = "https://api.ibee.ai/v1"
DEVELOPMENT_API_BASE_URL = "https://api.ibee.co.in/v1"


@dataclass
class Settings:
    token: str | None
    workspace: str | None
    dev: bool
    base_url: str | None
    as_json: bool


class CliApiError(Exception):
    """An HTTP error returned by a direct gateway request."""

    def __init__(self, status_code: int, body: Any) -> None:
        super().__init__(f"API error {status_code}")
        self.status_code = status_code
        self.body = body


def get_settings(ctx: typer.Context) -> Settings:
    return ctx.obj


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
    if settings.workspace:
        return settings.workspace
    typer.secho(
        "No workspace. Set IBEE_WORKSPACE_ID (or pass --workspace).",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


def get_client(settings: Settings) -> Ibee:
    token = require_token(settings)
    if settings.base_url:
        return Ibee(token=token, base_url=settings.base_url, timeout=30)
    if settings.dev:
        development = getattr(IbeeEnvironment, "DEVELOPMENT", None)
        if development is None:
            return Ibee(token=token, base_url=DEVELOPMENT_API_BASE_URL, timeout=30)
        return Ibee(token=token, environment=development, timeout=30)
    return Ibee(token=token, environment=IbeeEnvironment.DEFAULT, timeout=30)


def api_base_url(settings: Settings) -> str:
    """Resolve the public gateway base URL without requiring a generated SDK."""

    if settings.base_url:
        return settings.base_url.rstrip("/")
    return DEVELOPMENT_API_BASE_URL if settings.dev else PRODUCTION_API_BASE_URL


def api_request(
    settings: Settings,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    """Call a public API route using the CLI's shared auth and workspace state."""

    token = require_token(settings)
    workspace = require_workspace(settings)
    query = {"workspace_id": workspace}
    query.update({key: value for key, value in (params or {}).items() if value is not None})
    response = httpx.request(
        method,
        f"{api_base_url(settings)}/{path.lstrip('/')}",
        params=query,
        json=json_body,
        headers={
            "Authorization": f"Bearer {token}",
            "accept": "application/json",
            "content-type": "application/json",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = response.text[:1000]
        raise CliApiError(response.status_code, body)
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def settings_from_env(
    token: str | None,
    workspace: str | None,
    dev: bool,
    base_url: str | None,
    as_json: bool,
) -> Settings:
    return Settings(
        token=token or os.environ.get("IBEE_TOKEN") or os.environ.get("IBEE_API_TOKEN"),
        workspace=workspace or os.environ.get("IBEE_WORKSPACE_ID"),
        dev=dev or os.environ.get("IBEE_ENV", "").lower() in ("dev", "development"),
        base_url=base_url or os.environ.get("IBEE_BASE_URL"),
        as_json=as_json,
    )
