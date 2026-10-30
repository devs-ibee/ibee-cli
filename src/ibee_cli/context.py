"""Global CLI options and the SDK client they configure."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import typer
from ibee import Ibee, IbeeEnvironment

from . import __version__

try:  # the SDK's configuration error (bad base URL), raised before anything is sent
    from ibee.client import IbeeValidationError
except ImportError:  # pragma: no cover - an SDK without it

    class IbeeValidationError(ValueError):  # type: ignore[no-redef]
        """Never raised: this SDK has no configuration error class."""

OUTPUT_MODES = ("table", "json", "yaml", "id")
TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes"})
REQUEST_TIMEOUT_SECONDS = 30
# Replaces the SDK's own ``ibee/<version>`` so IBEE can tell CLI calls from SDK calls
# (Activity Logs show the tool that made each change); the SDK's other headers stay.
USER_AGENT = f"ibee-cli/{__version__}"


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


def get_settings(ctx: typer.Context) -> Settings:
    return ctx.find_root().obj


def is_truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUTHY_ENV_VALUES


def fail_usage(message: str) -> None:
    """Print a usage or configuration error and exit 2 (nothing was sent)."""
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=2)


def get_client(settings: Settings) -> Ibee:
    """The SDK client for these options.

    Every operation of the contract takes ``workspace_id``; the SDK sends the client's.
    Endpoint precedence: ``--base-url``, ``IBEE_BASE_URL``, ``IBEE_ENDPOINT``, ``--dev``, then
    ``IBEE_ENV`` (default production).
    """
    if not settings.token:
        fail_usage("No API token. Set IBEE_TOKEN (or pass --token). Create one in the portal under Settings > API Tokens.")
    if not settings.workspace:
        fail_usage("No workspace. Set IBEE_WORKSPACE_ID (or pass --workspace).")
    explicit = settings.base_url or settings.endpoint
    try:
        return Ibee(
            token=settings.token,
            workspace_id=settings.workspace,
            base_url=explicit,
            environment=IbeeEnvironment.DEVELOPMENT if settings.dev and not explicit else None,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
    except IbeeValidationError as exc:
        fail_usage(getattr(exc, "message", None) or str(exc))
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
