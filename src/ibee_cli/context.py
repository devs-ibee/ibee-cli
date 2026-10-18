"""Global CLI options and the SDK client they configure."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import typer
from ibee import Ibee, IbeeEnvironment
from ibee.client import IbeeValidationError

OUTPUT_MODES = ("table", "json", "yaml", "id")
TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes"})
REQUEST_TIMEOUT_SECONDS = 30


@dataclass
class Settings:
    """Global options (each with an environment variable fallback)."""

    token: Optional[str]
    workspace: Optional[str]
    dev: bool
    base_url: Optional[str]
    output: Optional[str] = None
    assume_yes: bool = False
    endpoint: Optional[str] = None

    @property
    def as_json(self) -> bool:
        return self.output == "json"

    @property
    def structured_output(self) -> bool:
        """``True`` for ``-o json|yaml|id``; commands then skip their tables."""
        return self.output in ("json", "yaml", "id")


def get_settings(ctx: typer.Context) -> Settings:
    return ctx.find_root().obj


def is_truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUTHY_ENV_VALUES


def fail_usage(message: str) -> None:
    """Print a usage or configuration error and exit 2 (nothing was sent)."""
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=2)


def require_workspace(settings: Settings) -> str:
    if settings.workspace:
        return settings.workspace
    fail_usage("No workspace. Set IBEE_WORKSPACE_ID (or pass --workspace).")
    return ""  # pragma: no cover - fail_usage always raises


def get_client(settings: Settings) -> Ibee:
    """The SDK client for these options (every call is scoped to the workspace).

    Endpoint precedence: ``--base-url``, ``IBEE_BASE_URL``, ``IBEE_ENDPOINT``, ``--dev``, then
    ``IBEE_ENV`` (default production). The SDK checks the URL and the token.
    """
    if not settings.token:
        fail_usage(
            "No API token. Set IBEE_TOKEN (or pass --token). Create one in the portal under Settings > API Tokens."
        )
    require_workspace(settings)
    explicit = settings.base_url or settings.endpoint
    try:
        return Ibee(
            token=settings.token,
            workspace_id=settings.workspace,
            base_url=explicit,
            environment=IbeeEnvironment.DEVELOPMENT if settings.dev and not explicit else None,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except IbeeValidationError as exc:
        fail_usage(exc.message)
    raise AssertionError("unreachable")  # pragma: no cover


def normalize_output(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    mode = value.strip().lower()
    if mode not in OUTPUT_MODES:
        raise typer.BadParameter(f"must be one of: {', '.join(OUTPUT_MODES)}.")
    return mode


def settings_from_env(
    token: Optional[str],
    workspace: Optional[str],
    dev: bool,
    base_url: Optional[str],
    as_json: bool = False,
    *,
    output: Optional[str] = None,
    assume_yes: bool = False,
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
        endpoint=os.environ.get("IBEE_ENDPOINT"),
    )
