"""Request-shape tests for Object Storage CLI commands."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ibee_cli.main import app

runner = CliRunner()
BASE_ARGS = [
    "--token",
    "test-token",
    "--workspace",
    "workspace-123",
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
    assert call["params"]["workspace_id"] == "workspace-123"
    assert call["headers"]["Authorization"] == "Bearer test-token"
    return call


def test_create_bucket_request(requests):
    call = invoke(
        requests,
        [
            "buckets",
            "create",
            "production-assets",
            "--site-id",
            "site-1",
            "--region",
            "in-south-1",
            "--public",
            "--bucket-lock",
            "--tag",
            "production",
            "--metadata",
            '{"owner":"platform"}',
        ],
    )
    assert call["method"] == "POST"
    assert call["url"].endswith("/object-storage/buckets")
    assert call["json"] == {
        "name": "production-assets",
        "site_id": "site-1",
        "region": "in-south-1",
        "plan": "Standard",
        "is_public": True,
        "bucket_lock_enabled": True,
        "tags": ["production"],
        "metadata": {"owner": "platform"},
    }


@pytest.mark.parametrize(
    ("args", "method", "path", "payload"),
    [
        (
            ["buckets", "get", "production-assets"],
            "GET",
            "/object-storage/buckets/production-assets",
            None,
        ),
        (
            ["buckets", "update", "production-assets", "--private"],
            "PATCH",
            "/object-storage/buckets/production-assets",
            {"is_public": False},
        ),
        (
            ["buckets", "delete", "production-assets", "--yes"],
            "DELETE",
            "/object-storage/buckets/production-assets",
            None,
        ),
    ],
)
def test_bucket_resource_requests(requests, args, method, path, payload):
    call = invoke(requests, args)
    assert call["method"] == method
    assert call["url"].endswith(path)
    assert call["json"] == payload


@pytest.mark.parametrize(
    ("args", "method", "path", "payload"),
    [
        (
            ["buckets", "credentials", "list"],
            "GET",
            "/object-storage/credentials",
            None,
        ),
        (
            [
                "buckets",
                "credentials",
                "create",
                "--name",
                "deploy",
                "--permission-type",
                "read_write",
                "--bucket-scope",
                "specific",
                "--allowed-bucket",
                "production-assets",
                "--allowed-bucket",
                "backups",
            ],
            "POST",
            "/object-storage/credentials",
            {
                "name": "deploy",
                "permission_type": "read_write",
                "bucket_scope": "specific",
                "allowed_buckets": ["production-assets", "backups"],
            },
        ),
        (
            ["buckets", "credentials", "get", "AKIA_TEST"],
            "GET",
            "/object-storage/credentials/AKIA_TEST",
            None,
        ),
        (
            ["buckets", "credentials", "revoke", "AKIA_TEST", "--yes"],
            "DELETE",
            "/object-storage/credentials/AKIA_TEST",
            None,
        ),
    ],
)
def test_s3_credential_requests(requests, args, method, path, payload):
    call = invoke(requests, args)
    assert call["method"] == method
    assert call["url"].endswith(path)
    assert call["json"] == payload


def test_bucket_update_requires_visibility_choice(requests):
    result = runner.invoke(
        app, [*BASE_ARGS, "buckets", "update", "production-assets"]
    )
    assert result.exit_code != 0
    assert "Provide --public or --private" in result.output
    assert requests == []


@pytest.mark.parametrize(
    "args",
    [
        [
            "buckets",
            "credentials",
            "create",
            "--bucket-scope",
            "specific",
        ],
        [
            "buckets",
            "credentials",
            "create",
            "--allowed-bucket",
            "production-assets",
        ],
    ],
)
def test_credential_scope_validation(requests, args):
    result = runner.invoke(app, [*BASE_ARGS, *args])
    assert result.exit_code != 0
    assert requests == []
