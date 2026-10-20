"""Container Registry (``ibee registries``): registries, storage plans, access tokens,
repositories, artifacts, security settings, vulnerabilities, SBOMs and metrics.

Each command makes one SDK call and prints the server's answer. The registry service
decides every rule (names, plans and Billing, visibility, states, permissions).
Access-token secrets are printed only by ``tokens create`` and ``tokens rotate``, and
only with ``--show-sensitive`` or ``-o json|yaml|id``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

import typer

from ..context import get_client, get_settings
from ..helpers import (
    compact_payload,
    confirm_destructive,
    idempotency_key_option,
    require_show_sensitive,
    response_items,
    show_sensitive_option,
)
from ..render import cell, emit, handle_api_errors, print_json

app = typer.Typer(help="Container Registry: registries, access tokens, images and security", no_args_is_help=True)
plan_app = typer.Typer(help="A registry's storage plan", no_args_is_help=True)
tokens_app = typer.Typer(help="Registry access tokens (docker login credentials)", no_args_is_help=True)
repositories_app = typer.Typer(help="Repositories in a registry", no_args_is_help=True)
artifacts_app = typer.Typer(help="Images (artifacts) in a repository", no_args_is_help=True)
security_app = typer.Typer(help="Security settings (vulnerability pull policy)", no_args_is_help=True)
vulnerabilities_app = typer.Typer(help="Vulnerability findings", no_args_is_help=True)
sboms_app = typer.Typer(help="Software bills of materials (SBOMs)", no_args_is_help=True)
app.add_typer(plan_app, name="plan")
app.add_typer(tokens_app, name="tokens")
app.add_typer(repositories_app, name="repositories")
app.add_typer(artifacts_app, name="artifacts")
app.add_typer(security_app, name="security")
app.add_typer(vulnerabilities_app, name="vulnerabilities")
app.add_typer(sboms_app, name="sboms")


def _api(ctx: typer.Context) -> tuple[Any, Any]:
    """``(settings, client.container_registry)`` for this invocation."""
    settings = get_settings(ctx)
    return settings, get_client(settings).container_registry


def _show(item: Any, name: str) -> Any:
    """A table cell: tags joined by name, timestamps to the second."""
    value = cell(item, name)
    if isinstance(value, list):
        return ", ".join(str(tag if isinstance(tag, str) else cell(tag, "name")) for tag in value)
    return str(value)[:19] if name.endswith(("_at", "_time")) and value else value


def _list(settings: Any, result: Any, title: str, columns: dict[str, str], id_field: str) -> None:
    """A table of the answer's items (``columns`` maps header to field); -o json|yaml|id prints the answer."""
    rows = [[_show(item, name) for name in columns.values()] for item in response_items(result, "items")]
    emit(result, settings=settings, default="table", headers=list(columns), rows=rows, title=title, id_field=id_field)


def _yes() -> Any:
    return typer.Option(False, "--yes", "-y", help="Skip confirmation")


def _page() -> Any:
    return typer.Option(None, "--page", help="Page number")


def _page_size() -> Any:
    return typer.Option(None, "--page-size", help="Items per page (the server allows up to 100)")


def _registry_id() -> Any:
    return typer.Argument(..., help="Registry ID")


def _repository(required: bool = True) -> Any:
    return typer.Option(... if required else None, "--repository", "-r", help="Repository name, for example api or team/api")


def _ack() -> Any:
    return typer.Option(
        None, "--ack-public-egress", help="Acknowledge that anonymous pulls of a PUBLIC registry are billed to the workspace"
    )


# ---------------------------------------------------------------------------
# Registries: `ibee registries <verb>`
# ---------------------------------------------------------------------------


@app.command("list")
@handle_api_errors
def list_registries(ctx: typer.Context, page: Optional[int] = _page(), page_size: Optional[int] = _page_size()) -> None:
    """List the workspace's registries."""
    settings, api = _api(ctx)
    result = api.list_container_registries(**compact_payload(page=page, page_size=page_size))
    columns = {
        "ID": "registry_id", "Name": "registry_name", "Visibility": "visibility", "State": "observed_state",
        "Created": "created_at",
    }
    _list(settings, result, "Container registries", columns, "registry_id")


@app.command("name-check")
@handle_api_errors
def check_name(ctx: typer.Context, name: str = typer.Argument(..., help="Registry name")) -> None:
    """Check whether a registry name is free (names are global and never reused)."""
    _settings, api = _api(ctx)
    print_json(api.check_container_registry_name_availability(name), id_field="registry_name")


@app.command("create")
@handle_api_errors
def create_registry(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Registry name (global; it cannot be changed or reused)"),
    sku: str = typer.Option(..., "--sku", help="Storage plan SKU, for example REGISTRY-STORAGE-250GB"),
    visibility: Optional[str] = typer.Option(None, "--visibility", help="PRIVATE or PUBLIC"),
    ack_public_egress: Optional[bool] = _ack(),
    pull_policy: Optional[str] = typer.Option(
        None, "--pull-policy", help="ALLOW, BLOCK_CRITICAL, BLOCK_HIGH, BLOCK_MEDIUM or BLOCK_LOW"
    ),
) -> None:
    """Create a registry. The registry service asks Billing about the plan and answers.

    The answer is 202 while provisioning continues: check `ibee registries get` until
    observed_state is ACTIVE.
    """
    _settings, api = _api(ctx)
    result = api.create_container_registry(
        registry_name=name,
        confirm_registry_name=name,
        billing_catalog={"sku_code": sku},
        **compact_payload(
            visibility=visibility,
            acknowledge_public_egress_charges=ack_public_egress,
            security={"vulnerability_pull_policy": pull_policy} if pull_policy is not None else None,
        ),
    )
    print_json(result, id_field="registry_id")


@app.command("get")
@handle_api_errors
def get_registry(ctx: typer.Context, registry_id: str = _registry_id()) -> None:
    """Show a registry (a deleted one is still shown, with deleted_at set)."""
    _settings, api = _api(ctx)
    print_json(api.get_container_registry(registry_id), id_field="registry_id")


@app.command("delete")
@handle_api_errors
def delete_registry(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    confirm_name: str = typer.Option(..., "--confirm-name", help="The registry's name, to confirm"),
    yes: bool = _yes(),
) -> None:
    """Delete a registry (it must be empty; see `empty`). The name is never reused."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Delete registry '{confirm_name}' ({registry_id})?", yes)
    print_json(api.delete_container_registry(registry_id, confirm_registry_name=confirm_name), id_field="registry_id")


@app.command("visibility")
@handle_api_errors
def set_visibility(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    visibility: str = typer.Argument(..., help="PRIVATE or PUBLIC"),
    ack_public_egress: Optional[bool] = _ack(),
) -> None:
    """Make a registry private or public."""
    _settings, api = _api(ctx)
    result = api.update_container_registry_visibility(
        registry_id, visibility=visibility, **compact_payload(acknowledge_public_egress_charges=ack_public_egress)
    )
    print_json(result, id_field="registry_id")


@app.command("empty")
@handle_api_errors
def empty_registry(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    confirm_name: str = typer.Option(..., "--confirm-name", help="The registry's name, to confirm"),
    yes: bool = _yes(),
) -> None:
    """Permanently delete every repository and image in a registry."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Delete every repository and image in registry '{confirm_name}'?", yes)
    print_json(api.empty_container_registry(registry_id, confirm_registry_name=confirm_name), id_field="registry_id")


@app.command("retry")
@handle_api_errors
def retry_registry(ctx: typer.Context, registry_id: str = _registry_id()) -> None:
    """Run the registry's pending change again (for example after observed_state ERROR)."""
    _settings, api = _api(ctx)
    print_json(api.retry_container_registry_provisioning(registry_id), id_field="registry_id")


@app.command("metrics")
@handle_api_errors
def registry_metrics(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    time_range: Optional[str] = typer.Option(None, "--range", help="24h, 7d or 30d"),
) -> None:
    """Show storage use against the plan quota and outbound bandwidth."""
    _settings, api = _api(ctx)
    print_json(api.get_container_registry_metrics(registry_id, **compact_payload(range=time_range)), id_field="registry_id")


# ---------------------------------------------------------------------------
# Storage plan: `ibee registries plan <verb>`
# ---------------------------------------------------------------------------


@plan_app.command("get")
@handle_api_errors
def get_plan(ctx: typer.Context, registry_id: str = _registry_id()) -> None:
    """Show the current storage plan and any pending upgrade."""
    _settings, api = _api(ctx)
    print_json(api.get_container_registry_package(registry_id), id_field="registry_id")


@plan_app.command("upgrade")
@handle_api_errors
def upgrade_plan(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    sku: str = typer.Option(..., "--sku", help="Storage plan SKU to move to"),
    idempotency_key: Optional[str] = idempotency_key_option(),
) -> None:
    """Move a registry to a larger storage plan. The registry service asks Billing and answers."""
    _settings, api = _api(ctx)
    result = api.upgrade_container_registry_package(
        registry_id, billing_catalog={"sku_code": sku}, **compact_payload(idempotency_key=idempotency_key)
    )
    print_json(result, id_field="operation_id")


# ---------------------------------------------------------------------------
# Access tokens: `ibee registries tokens <verb>`
# ---------------------------------------------------------------------------


@tokens_app.command("list")
@handle_api_errors
def list_tokens(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    include_revoked: Optional[bool] = typer.Option(None, "--include-revoked", help="Include revoked tokens"),
) -> None:
    """List the tokens that can use a registry (secrets are never returned)."""
    settings, api = _api(ctx)
    result = api.list_container_registry_access_tokens(registry_id, **compact_payload(include_revoked=include_revoked))
    columns = {
        "ID": "token_id", "Name": "token_name", "Username": "harbor_username", "Permission": "permission",
        "Expires": "expires_at", "Revoked": "revoked_at",
    }
    _list(settings, result, "Registry access tokens", columns, "token_id")


@tokens_app.command("create")
@handle_api_errors
def create_token(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    name: str = typer.Argument(..., help="Token name"),
    permission: Optional[str] = typer.Option(None, "--permission", help="PULL or PULL_PUSH"),
    expires_in_days: Optional[int] = typer.Option(None, "--expires-in-days", help="1-365, or -1 for no expiry"),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Description"),
    also_registry: Optional[List[str]] = typer.Option(
        None, "--registry-id", help="Another registry the token may use (repeatable)"
    ),
    show_sensitive: bool = show_sensitive_option(),
) -> None:
    """Create a docker login credential; its username and secret are printed once."""
    settings, api = _api(ctx)
    require_show_sensitive(settings, show_sensitive)
    result = api.create_container_registry_access_token(
        registry_id,
        token_name=name,
        **compact_payload(
            permission=permission,
            expires_in_days=expires_in_days,
            description=description,
            registry_ids=also_registry or None,
        ),
    )
    print_json(result, id_field="token_id")


@tokens_app.command("rotate")
@handle_api_errors
def rotate_token(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    token_id: str = typer.Argument(..., help="Token ID"),
    show_sensitive: bool = show_sensitive_option(),
) -> None:
    """Issue a new secret (the old one stops working) and print it once."""
    settings, api = _api(ctx)
    require_show_sensitive(settings, show_sensitive)
    print_json(api.rotate_container_registry_access_token(registry_id, token_id), id_field="token_id")


@tokens_app.command("revoke")
@handle_api_errors
def revoke_token(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    token_id: str = typer.Argument(..., help="Token ID"),
    yes: bool = _yes(),
) -> None:
    """Revoke a token immediately."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Revoke registry access token '{token_id}'?", yes)
    print_json(api.revoke_container_registry_access_token(registry_id, token_id))


# ---------------------------------------------------------------------------
# Repositories and artifacts
# ---------------------------------------------------------------------------


@repositories_app.command("list")
@handle_api_errors
def list_repositories(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    search: Optional[str] = typer.Option(None, "--search", help="Fuzzy match on the repository name"),
    sort: Optional[str] = typer.Option(None, "--sort", help="name, pull_count, creation_time or update_time (- for descending)"),
    page: Optional[int] = _page(),
    page_size: Optional[int] = _page_size(),
) -> None:
    """List a registry's repositories."""
    settings, api = _api(ctx)
    result = api.list_container_registry_repositories(
        registry_id, **compact_payload(search=search, sort=sort, page=page, page_size=page_size)
    )
    columns = {"Name": "name", "Artifacts": "artifact_count", "Pulls": "pull_count", "Updated": "update_time"}
    _list(settings, result, "Repositories", columns, "name")


@repositories_app.command("delete")
@handle_api_errors
def delete_repository(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    repository: str = typer.Argument(..., help="Repository name"),
    yes: bool = _yes(),
) -> None:
    """Permanently delete a repository and all of its images."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Permanently delete repository '{repository}' and all of its images?", yes)
    print_json(api.delete_container_registry_repository(registry_id, repository, confirm=True))


@artifacts_app.command("list")
@handle_api_errors
def list_artifacts(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    repository: str = _repository(),
    page: Optional[int] = _page(),
    page_size: Optional[int] = _page_size(),
) -> None:
    """List the images in a repository with their tags."""
    settings, api = _api(ctx)
    result = api.list_container_registry_artifacts(
        registry_id, repository_name=repository, **compact_payload(page=page, page_size=page_size)
    )
    columns = {"Digest": "digest", "Tags": "tags", "Size": "size", "Pushed": "push_time"}
    _list(settings, result, "Artifacts", columns, "digest")


@artifacts_app.command("delete")
@handle_api_errors
def delete_artifact(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    reference: str = typer.Argument(..., help="Image digest (sha256:...) or tag"),
    repository: str = _repository(),
    yes: bool = _yes(),
) -> None:
    """Permanently delete an image and every tag that points to it."""
    settings, api = _api(ctx)
    confirm_destructive(settings, f"Permanently delete image '{repository}@{reference}'?", yes)
    print_json(api.delete_container_registry_artifact(registry_id, reference, repository_name=repository, confirm=True))


@artifacts_app.command("rescan")
@handle_api_errors
def rescan_artifact(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    reference: str = typer.Argument(..., help="Image digest (sha256:...) or tag"),
    repository: str = _repository(),
) -> None:
    """Queue a new vulnerability scan of an image."""
    _settings, api = _api(ctx)
    print_json(api.scan_container_registry_artifact(registry_id, reference, repository_name=repository))


# ---------------------------------------------------------------------------
# Security: settings, vulnerabilities and SBOMs
# ---------------------------------------------------------------------------


@security_app.command("get")
@handle_api_errors
def get_security(ctx: typer.Context, registry_id: str = _registry_id()) -> None:
    """Show the vulnerability pull policy."""
    _settings, api = _api(ctx)
    print_json(api.get_container_registry_security_settings(registry_id))


@security_app.command("update")
@handle_api_errors
def update_security(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    pull_policy: str = typer.Option(
        ..., "--pull-policy", help="ALLOW, BLOCK_CRITICAL, BLOCK_HIGH, BLOCK_MEDIUM or BLOCK_LOW"
    ),
) -> None:
    """Set the vulnerability pull policy."""
    _settings, api = _api(ctx)
    print_json(api.update_container_registry_security_settings(registry_id, vulnerability_pull_policy=pull_policy))


@vulnerabilities_app.command("summary")
@handle_api_errors
def vulnerability_summary(ctx: typer.Context, registry_id: str = _registry_id()) -> None:
    """Show the number of findings by severity."""
    _settings, api = _api(ctx)
    print_json(api.get_container_registry_vulnerability_summary(registry_id))


@vulnerabilities_app.command("list")
@handle_api_errors
def list_vulnerabilities(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    severity: Optional[str] = typer.Option(None, "--severity", help="Critical, High, Medium, Low, Unknown or None"),
    cve_id: Optional[str] = typer.Option(None, "--cve-id", help="CVE ID"),
    repository: Optional[str] = _repository(required=False),
    digest: Optional[str] = typer.Option(None, "--digest", help="Image digest"),
    tag: Optional[str] = typer.Option(None, "--tag", help="Image tag"),
    package: Optional[str] = typer.Option(None, "--package", help="Package name"),
    page: Optional[int] = _page(),
    page_size: Optional[int] = _page_size(),
) -> None:
    """List vulnerability findings."""
    settings, api = _api(ctx)
    result = api.list_container_registry_vulnerabilities(
        registry_id,
        **compact_payload(
            severity=severity, cve_id=cve_id, repository_name=repository, digest=digest, tag=tag, package=package,
            page=page, page_size=page_size,
        ),
    )
    columns = {
        "CVE": "cve_id", "Severity": "severity", "Package": "package", "Version": "version",
        "Fixed in": "fixed_version", "Repository": "repository_name",
    }
    _list(settings, result, "Vulnerabilities", columns, "cve_id")


@sboms_app.command("list")
@handle_api_errors
def list_sboms(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    repository: str = _repository(),
    page: Optional[int] = _page(),
    page_size: Optional[int] = _page_size(),
) -> None:
    """List a repository's SBOMs, one per image digest."""
    settings, api = _api(ctx)
    result = api.list_container_registry_sboms(
        registry_id, repository_name=repository, **compact_payload(page=page, page_size=page_size)
    )
    columns = {"Digest": "artifact_digest", "Tags": "tags", "Status": "status", "Format": "format"}
    _list(settings, result, "SBOMs", columns, "artifact_digest")


@sboms_app.command("get")
@handle_api_errors
def get_sbom(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    digest: str = typer.Argument(..., help="Image digest (sha256:...)"),
    repository: str = _repository(),
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Filter components"),
    page: Optional[int] = _page(),
    page_size: Optional[int] = _page_size(),
) -> None:
    """Show an image's SBOM components and relationships."""
    _settings, api = _api(ctx)
    print_json(api.get_container_registry_sbom(
        registry_id, digest, repository_name=repository, **compact_payload(q=query, page=page, page_size=page_size)
    ))


@sboms_app.command("download")
@handle_api_errors
def download_sbom(
    ctx: typer.Context,
    registry_id: str = _registry_id(),
    digest: str = typer.Argument(..., help="Image digest (sha256:...)"),
    repository: str = _repository(),
    out: Optional[Path] = typer.Option(None, "--file", "-f", help="Write the document here (default: stdout)"),
) -> None:
    """Download an image's full SBOM document (SPDX or CycloneDX JSON)."""
    _settings, api = _api(ctx)
    chunks = api.download_container_registry_sbom(registry_id, digest, repository_name=repository)
    if out is None:
        for chunk in chunks:
            typer.echo(chunk, nl=False)
        return
    with out.open("wb") as handle:
        for chunk in chunks:
            handle.write(chunk)
    typer.secho(f"Wrote {out}", fg=typer.colors.GREEN, err=True)
