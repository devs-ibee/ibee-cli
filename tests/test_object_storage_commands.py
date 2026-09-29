"""Object Storage commands against the real Python SDK (scripted gateway)."""

from __future__ import annotations

import pytest

import _net_fixtures as net

WS = net.WS
run, plain = net.run, net.plain
TS = "2026-09-01T10:00:00Z"
B = "object-storage/buckets"


def bucket(name="production-assets", **overrides):
    record = {"name": name, "region": "in-south-1", "is_public": False, "bucket_lock_enabled": False,
              "object_count": 0, "total_size": 0, "created_at": TS}
    record.update(overrides)
    return record


def credential(**overrides):
    record = {"access_key_id": "AKIA_TEST", "name": "deploy", "status": "active", "created_at": TS,
              "permission_type": "object_rw", "bucket_scope": "specific", "allowed_buckets": ["a", "b"]}
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------


def test_create_bucket_request(gw):
    gw.on("POST", B, bucket(is_public=True, bucket_lock_enabled=True))
    result = run(["buckets", "create", "production-assets", "--region", "in-south-1", "--public", "--bucket-lock",
                  "--tag", "production", "--default-retention", '{"mode":"GOVERNANCE","days":30}'])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", B).json == {
        "name": "production-assets",
        "region": "in-south-1",
        "is_public": True,
        "object_lock_enabled": True,
        "default_retention": {"mode": "GOVERNANCE", "days": 30},
        "tags": ["production"],
    }


def test_retention_options_enable_object_lock(gw):
    gw.on("POST", B, bucket())
    result = run(["buckets", "create", "locked-assets", "--region", "r1", "--retention-mode", "compliance",
                  "--retention-years", "2"])
    assert result.exit_code == 0, result.output
    body = gw.last("POST", B).json
    assert body["object_lock_enabled"] is True
    assert body["default_retention"] == {"mode": "COMPLIANCE", "years": 2}


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["Assets"], "lowercase letters, numbers, and hyphens"),
        (["ab"], "at least 3 characters"),
        (["a" * 64], "less than 63 characters"),
        (["assets-"], "start and end with a letter or number"),
        (["assets", "--no-bucket-lock", "--retention-mode", "GOVERNANCE", "--retention-days", "1"],
         "object_lock_enabled must be true"),
        (["assets", "--retention-mode", "GOVERNANCE"], "--retention-days or --retention-years"),
        (["assets", "--retention-mode", "GOVERNANCE", "--retention-days", "1", "--retention-years", "1"],
         "only one of"),
        (["assets", "--retention-days", "3"], "require --retention-mode"),
        (["assets", "--retention-mode", "LEGAL", "--retention-days", "3"], "GOVERNANCE or COMPLIANCE"),
        (["assets", "--retention-mode", "GOVERNANCE", "--retention-days", "36501"], "1 and 36500"),
        (["assets", "--default-retention", '{"mode":"GOVERNANCE","days":1,"years":1}'], "exactly one of"),
    ],
)
def test_create_bucket_validation_runs_before_any_request(gw, args, expected):
    result = run(["buckets", "create", *args, "--region", "r1"])
    assert result.exit_code == 2, result.output
    assert expected in plain(result)
    assert gw.calls == []


def test_region_defaults_by_api_host(gw):
    gw.base_url = "https://api.ibee.co.in/v1"
    gw.on("POST", B, bucket())
    result = run(["buckets", "create", "assets"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", B).json["region"] == "in-south-2"


def test_region_is_required_for_other_endpoints(gw, monkeypatch):
    monkeypatch.delenv("IBEE_REGION", raising=False)
    result = run(["buckets", "create", "assets"])
    assert result.exit_code == 2
    assert "region is required for this base URL" in plain(result)
    assert gw.calls == []


def test_create_bucket_preflight_uses_object_storage_sku(gw):
    gw.on("POST", "billing/resource-eligibility",
          {"allowed": True, "reason": "ok", "organization_id": "org-1", "sku_code": "OBJECTST-STD"})
    gw.on("POST", B, bucket())
    result = run(["buckets", "create", "assets", "--region", "r1", "--preflight-billing"])
    assert result.exit_code == 0, result.output
    assert not any(c.path == "billing/resource-eligibility" for c in gw.calls)


def test_list_buckets_table_and_next_page_hint(gw):
    gw.on("GET", B, {"buckets": [bucket(is_public=True, object_count=3, total_size=2048)], "is_truncated": True,
                     "next_continuation_token": "tok-2"})
    result = run(["buckets", "list"])
    assert result.exit_code == 0, result.output
    assert gw.last("GET", B).params == {"workspace_id": WS, "limit": "100"}
    output = plain(result)
    assert "production-assets" in output and "2.0 KB" in output and "yes" in output
    assert "ibee buckets list --continuation-token tok-2" in output


def test_list_buckets_all_follows_tokens(gw):
    gw.on("GET", B,
          {"buckets": [bucket("a1b")], "is_truncated": True, "next_continuation_token": "t2"},
          {"buckets": [bucket("a2b")], "is_truncated": False, "next_continuation_token": None})
    result = run(["-o", "id", "buckets", "list", "--all"])
    assert result.exit_code == 0, result.output
    assert result.output.split() == ["a1b", "a2b"]
    assert gw.calls[1].params["continuation_token"] == "t2"


@pytest.mark.parametrize("args", [["--limit", "0"], ["--limit", "1001"], ["--all", "--continuation-token", "t"]])
def test_list_buckets_validation(gw, args):
    result = run(["buckets", "list", *args])
    assert result.exit_code == 2
    assert gw.calls == []


def test_get_bucket_url_encodes_the_name(gw):
    gw.on("GET", f"{B}/legacy name", bucket("legacy name"))
    result = run(["buckets", "get", "legacy name"])
    assert result.exit_code == 0, result.output
    assert "/legacy%20name" in gw.calls[0].raw_path


def test_update_private_confirms_the_cdn_side_effect(gw):
    result = run(["buckets", "update", "production-assets", "--private"], input="n\n")
    assert result.exit_code == 1
    assert "deletes any CDN distribution" in plain(result)
    assert gw.calls == []
    gw.on("PATCH", f"{B}/production-assets", bucket())
    result = run(["buckets", "update", "production-assets", "--private", "--yes"])
    assert result.exit_code == 0, result.output
    assert gw.last("PATCH", f"{B}/production-assets").json == {"is_public": False}


def test_update_public_needs_no_confirmation(gw):
    gw.on("PATCH", f"{B}/production-assets", bucket(is_public=True))
    result = run(["buckets", "update", "production-assets", "--public"])
    assert result.exit_code == 0, result.output


def test_bucket_update_requires_visibility_choice(gw):
    result = run(["buckets", "update", "production-assets"])
    assert result.exit_code != 0
    assert "Provide --public or --private" in plain(result)
    assert gw.calls == []


@pytest.mark.parametrize(
    ("record", "args", "expected"),
    [
        (bucket(bucket_lock_enabled=True), [], "Object Lock is enabled"),
        (bucket(object_count=4), [], "Bucket is not empty (4 objects)"),
        (bucket(bucket_lock_enabled=True), ["--skip-preflight"], "Object Lock is enabled"),
    ],
)
def test_delete_bucket_preflight(gw, record, args, expected):
    gw.on("GET", f"{B}/production-assets", record)
    result = run(["buckets", "delete", "production-assets", "--yes", *args])
    assert result.exit_code == 2
    assert expected in plain(result)
    assert gw.writes() == []


def test_delete_bucket_skip_preflight_ignores_object_count(gw):
    gw.on("GET", f"{B}/production-assets", bucket(object_count=4))
    gw.on("DELETE", f"{B}/production-assets", {"detail": "Bucket deleted"})
    result = run(["buckets", "delete", "production-assets", "--yes", "--skip-preflight"])
    assert result.exit_code == 0, result.output
    assert len(gw.requests("GET")) == 1


def test_delete_bucket_prompt_says_empty(gw):
    gw.on("GET", f"{B}/production-assets", bucket())
    result = run(["buckets", "delete", "production-assets"], input="n\n")
    assert result.exit_code == 1
    assert "Delete empty bucket 'production-assets'?" in plain(result)
    assert gw.writes() == []


def test_delete_bucket_409_not_empty_hint(gw):
    gw.on("DELETE", f"{B}/production-assets", (409, {"detail": "Bucket is not empty"}))
    result = run(["buckets", "delete", "production-assets", "--yes", "--no-check-state"])
    assert result.exit_code == 1
    output = plain(result)
    assert "Conflict (409): Bucket is not empty" in output
    assert "Empty the bucket first" in output
    assert gw.requests("GET") == []


def test_delete_bucket_403_retention_hint(gw):
    gw.on("DELETE", f"{B}/production-assets", (403, {"detail": "Object is under retention"}))
    result = run(["buckets", "delete", "production-assets", "--yes", "--no-check-state"])
    assert result.exit_code == 1
    assert "retention or legal hold is active" in plain(result)


# ---------------------------------------------------------------------------
# S3 credentials
# ---------------------------------------------------------------------------


def test_credential_create_defaults_to_admin_rw_and_is_not_retried(gw):
    gw.on("POST", "object-storage/credentials", (503, {"detail": "busy"}))
    result = run(["buckets", "credentials", "create"])
    assert result.exit_code == 1
    assert len(gw.calls) == 1
    assert gw.calls[0].json == {"name": "Default Key", "permission_type": "admin_rw", "bucket_scope": "all",
                                "allowed_buckets": []}


def test_credential_create_specific_scope_and_endpoint_hint(gw):
    gw.on("POST", "object-storage/credentials", {**credential(), "secret_access_key": "s3cr3t",
                                                 "organization_id": "org", "workspace_id": WS})
    result = run(["buckets", "credentials", "create", "--name", " deploy ", "--permission-type", "object_rw",
                  "--bucket-scope", "specific", "--allowed-bucket", "a", "--allowed-bucket", " b ",
                  "--allowed-bucket", "a"])
    assert result.exit_code == 0, result.output
    assert gw.calls[0].json == {"name": "deploy", "permission_type": "object_rw", "bucket_scope": "specific",
                                "allowed_buckets": ["a", "b"]}
    output = plain(result)
    assert "it cannot be retrieved again" in output
    assert f"https://{WS}.blob.ibeestorage.com" in output


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--bucket-scope", "specific"], "apply only to object_rw/object_ro"),
        (["--allowed-bucket", "production-assets"], "apply only to object_rw/object_ro"),
        (["--permission-type", "object_ro", "--bucket-scope", "specific"], "Please select at least one bucket"),
        (["--permission-type", "object_ro", "--allowed-bucket", "a"], "must be empty"),
        (["--permission-type", "read_write"], "permission_type"),
        (["--name", "x" * 101], "at most 100 characters"),
        (["--name", "  "], "Please enter a credential name"),
    ],
)
def test_credential_scope_validation(gw, args, expected):
    result = run(["buckets", "credentials", "create", *args])
    assert result.exit_code == 2, result.output
    assert expected in plain(result)
    assert gw.calls == []


def test_credential_list_table(gw):
    gw.on("GET", "object-storage/credentials", {"credentials": [credential(), credential(
        access_key_id="AK2", name="admin", permission_type="admin_rw", bucket_scope="all", allowed_buckets=[])]})
    result = run(["-o", "table", "buckets", "credentials", "list"])
    assert result.exit_code == 0, result.output
    output = plain(result)
    assert "AKIA_TEST" in output and "a, b" in output and "admin_rw" in output


def test_credential_get(gw):
    gw.on("GET", "object-storage/credentials/AKIA_TEST", credential())
    assert run(["buckets", "credentials", "get", "AKIA_TEST"]).exit_code == 0


@pytest.mark.parametrize("verb", ["revoke", "delete"])
def test_credential_delete_is_permanent(gw, verb):
    result = run(["buckets", "credentials", verb, "AKIA_TEST"], input="n\n")
    assert result.exit_code == 1
    assert "Permanently delete S3 credential 'AKIA_TEST'? It cannot be restored or re-enabled." in plain(result)
    gw.on("DELETE", "object-storage/credentials/AKIA_TEST", {"success": True, "message": "has been deleted"})
    result = run(["buckets", "credentials", verb, "AKIA_TEST", "--yes"])
    assert result.exit_code == 0, result.output
    assert gw.last("DELETE", "object-storage/credentials/AKIA_TEST")


def test_credential_402_is_worded_for_s3_credentials(gw):
    gw.on("POST", "object-storage/credentials", (402, {"error": "billing_denied", "billing_reason": "insufficient_balance"}))
    result = run(["buckets", "credentials", "create"])
    assert result.exit_code == 1
    assert "S3 credential" in plain(result)


def test_storage_lifecycle_restriction_is_explained(gw):
    gw.on("POST", B, (403, {"detail": "Storage namespace changes are restricted"}))
    result = run(["buckets", "create", "assets", "--region", "r1"])
    assert result.exit_code == 1
    assert "restricted" in plain(result)
