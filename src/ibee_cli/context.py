"""Shared CLI state: authentication, environment, and client construction."""

from __future__ import annotations

import os
from dataclasses import dataclass

import typer
from ibee import Ibee
from ibee.environment import IbeeEnvironment


@dataclass
class Settings:
    token: str | None
    workspace: str | None
    dev: bool
    base_url: str | None
    as_json: bool


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
    env = IbeeEnvironment.DEVELOPMENT if settings.dev else IbeeEnvironment.DEFAULT
    return Ibee(token=token, environment=env, timeout=30)


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
