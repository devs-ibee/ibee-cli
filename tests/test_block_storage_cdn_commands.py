"""Request-contract tests for every public Block Storage and CDN CLI operation."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ibee_cli.main import app

runner = CliRunner()
BASE_ARGS = [
    "--token",
    "test-token",
    "--workspace",
    "973318",
    "--base-url",
    "https://gateway.example/v1",
]


class FakeResponse:
    status_code = 200
    content = b"{}"
    text = "{}"

    def json(self):
        return {}


@pytest.fixture
def requests(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr("ibee_cli.context.httpx.request", fake_request)
    return calls


def invoke(requests, args):
    result = runner.invoke(app, [*BASE_ARGS, *args])
    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    call = requests[0]
    assert call["params"]["workspace_id"] == "973318"
    assert call["headers"]["Authorization"] == "Bearer test-token"
    return call


@pytest.mark.parametrize(
    ("args", "method", "path"),
    [
        (["block-storage", "list"], "GET", "/block-storage/volumes"),
        (
            ["block-storage", "create", "data", "--size-gb", "100", "--site-id", "site-1"],
            "POST",
            "/block-storage/volumes",
        ),
        (["block-storage", "get", "vol/one"], "GET", "/block-storage/volumes/vol%2Fone"),
        (
            ["block-storage", "operations", "vol/one"],
            "GET",
            "/block-storage/volumes/vol%2Fone/operations",
        ),
        (
            ["block-storage", "attach", "vol/one", "--node-name", "worker-1", "--vm-id", "vm-1"],
            "POST",
            "/block-storage/volumes/vol%2Fone/attachments",
        ),
        (
            ["block-storage", "detach", "vol/one", "--node-name", "worker-1", "--confirm-unmounted", "--yes"],
            "POST",
            "/block-storage/volumes/vol%2Fone/detach",
        ),
        (
            ["block-storage", "resize", "vol/one", "--new-size-gb", "200"],
            "POST",
            "/block-storage/volumes/vol%2Fone/resize",
        ),
        (
            ["block-storage", "delete", "vol/one", "--force", "--yes"],
            "DELETE",
            "/block-storage/volumes/vol%2Fone",
        ),
    ],
)
def test_all_eight_block_storage_routes(requests, args, method, path):
    call = invoke(requests, args)
    assert call["method"] == method
    assert call["url"].endswith(path)


def test_block_storage_request_shapes(requests):
    create = invoke(
        requests,
        [
            "block-storage", "create", "data", "--size-gb", "100",
            "--site-id", "site-1", "--site-name", "Bengaluru",
            "--class", "performance", "--replicas", "3", "--no-backup",
        ],
    )
    assert create["json"]["name"] == "data"
    assert create["json"]["site_id"] == "site-1"
    assert create["json"]["site_name"] == "Bengaluru"
    assert create["json"]["volume_class"] == "performance"
    assert create["json"]["replica_count"] == 3
    assert create["json"]["backup_enabled"] is False
    assert create["json"]["idempotency_key"].startswith("cli-block-create-data-")


def test_block_create_requires_site_before_request(requests):
    result = runner.invoke(
        app,
        [*BASE_ARGS, "block-storage", "create", "data", "--size-gb", "100"],
    )
    assert result.exit_code == 2
    assert "--site-id" in result.output
    assert requests == []


@pytest.mark.parametrize(
    ("args", "method", "path"),
    [
        (["cdn", "generate-url", "assets", "images/logo.png"], "POST", "/cdn/generate-url"),
        (["cdn", "list"], "GET", "/cdn/distributions"),
        (["cdn", "create", "assets", "--origin-id", "bucket-1"], "POST", "/cdn/distributions"),
        (["cdn", "get", "dist/one"], "GET", "/cdn/distributions/dist%2Fone"),
        (["cdn", "update", "dist/one", "--cache-policy", "media"], "PATCH", "/cdn/distributions/dist%2Fone"),
        (["cdn", "delete", "dist/one", "--yes"], "DELETE", "/cdn/distributions/dist%2Fone"),
        (["cdn", "website", "get", "dist/one"], "GET", "/cdn/distributions/dist%2Fone/website-config"),
        (["cdn", "website", "set", "dist/one", "--index-document", "home.html"], "PUT", "/cdn/distributions/dist%2Fone/website-config"),
        (["cdn", "website", "delete", "dist/one", "--yes"], "DELETE", "/cdn/distributions/dist%2Fone/website-config"),
        (["cdn", "domains", "list", "dist/one"], "GET", "/cdn/distributions/dist%2Fone/custom-domains"),
        (["cdn", "domains", "create", "dist/one", "static.example.com"], "POST", "/cdn/distributions/dist%2Fone/custom-domains"),
        (["cdn", "domains", "get", "dist/one", "nested/domain.example"], "GET", "/cdn/distributions/dist%2Fone/custom-domains/nested%2Fdomain.example"),
        (["cdn", "domains", "verify", "dist/one", "nested/domain.example"], "POST", "/cdn/distributions/dist%2Fone/custom-domains/nested%2Fdomain.example/verify"),
        (["cdn", "domains", "delete", "dist/one", "nested/domain.example", "--yes"], "DELETE", "/cdn/distributions/dist%2Fone/custom-domains/nested%2Fdomain.example"),
        (["cdn", "purge", "dist/one", "--mode", "all"], "POST", "/cdn/distributions/dist%2Fone/purge"),
    ],
)
def test_all_fifteen_cdn_routes(requests, args, method, path):
    call = invoke(requests, args)
    assert call["method"] == method
    assert call["url"].endswith(path)


def test_cdn_purge_url_uses_openapi_paths_field(requests):
    call = invoke(
        requests,
        ["cdn", "purge", "dist-1", "--mode", "url", "--path", "/a.css", "--path", "/b.js"],
    )
    assert call["json"] == {"mode": "url", "paths": ["/a.css", "/b.js"]}


def test_cdn_purge_selector_validation_prevents_request(requests):
    result = runner.invoke(app, [*BASE_ARGS, "cdn", "purge", "dist-1", "--mode", "url"])
    assert result.exit_code != 0
    assert "requires at least one --path" in result.output
    assert requests == []


@pytest.mark.parametrize("group", ["block-storage", "cdn"])
def test_new_groups_preserve_workspace_validation(group):
    result = runner.invoke(
        app,
        ["--token", "test-token", "--workspace", "not-a-workspace", group, "list"],
    )
    assert result.exit_code == 2
    assert "workspace_id must be a positive numeric string" in result.output
