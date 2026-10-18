"""Block Storage, Object Storage and CDN commands: one SDK call each, sent as typed."""

from __future__ import annotations

import json

import pytest
from _net_fixtures import TS, WS, plain, run

from ibee_cli.render import EXIT_CODES

REQ = {"x-request-id": "req-9"}


def volume(**overrides):
    record = {
        "id": "vol-1",
        "workspace_id": WS,
        "site_id": "site-1",
        "site_name": "Bengaluru",
        "name": "data",
        "size_gb": 50,
        "state": "in-use",
        "volume_class": "balanced",
        "replica_count": 2,
        "attachments": [
            {"node_name": "node-a", "mode": "single-writer", "device_path": "/dev/vdb", "vm_id": "vm-1",
             "vm_name": "web", "attached_at": TS},
        ],
        "created_at": TS,
        "updated_at": TS,
    }
    record.update(overrides)
    return record


def operation(**overrides):
    record = {"id": "op-1", "volume_id": "vol-1", "operation": "create", "status": "succeeded",
              "created_at": TS, "started_at": TS}
    record.update(overrides)
    return record


def bucket(**overrides):
    record = {"name": "logs", "is_public": False, "region": "in-south-1", "bucket_lock_enabled": True,
              "object_count": 3, "total_size": 2048}
    record.update(overrides)
    return record


def credential(**overrides):
    record = {"access_key_id": "AK1", "name": "ci", "status": "active", "created_at": TS,
              "permission_type": "object_ro", "bucket_scope": "specific", "allowed_buckets": ["logs"]}
    record.update(overrides)
    return record


def distribution(**overrides):
    record = {"id": "dist-1", "name": "site", "origin_type": "bucket", "origin_id": "assets",
              "cache_policy": "static-assets", "enabled": True, "status": "active",
              "default_domain": "d1.cdn.example", "default_url": "https://d1.cdn.example",
              "custom_domains": [], "created_at": TS}
    record.update(overrides)
    return record


# -- Block Storage -----------------------------------------------------------


def test_volume_list_table_and_json(gw) -> None:
    gw.on("GET", "block-storage/volumes", (200, [volume()]))
    result = run(["-o", "table", "block-storage", "list"])
    assert result.exit_code == 0, result.output
    text = plain(result)
    for part in ("Block Storage volumes", "vol-1", "data", "50", "in-use", "Bengaluru", "web"):
        assert part in text
    result = run(["-o", "json", "block-storage", "list"])
    assert result.exit_code == 0, result.output
    [item] = json.loads(result.stdout)
    assert item["id"] == "vol-1" and item["attachments"][0]["node_name"] == "node-a"
    assert [(c.method, c.path) for c in gw.calls] == [("GET", "block-storage/volumes")] * 2


def test_volume_create_is_sent_as_typed_without_client_bounds(gw) -> None:
    gw.on("POST", "block-storage/volumes", (200, {"volume": volume(name="ab", size_gb=1), "operation": operation()}))
    result = run(["block-storage", "create", "ab", "--size-gb", "1", "--site-id", "site-9",
                  "--idempotency-key", "k-1"])
    assert result.exit_code == 0, result.output
    assert len(gw.calls) == 1  # no site read, no billing check
    assert gw.last("POST", "block-storage/volumes").json == {
        "name": "ab", "size_gb": 1, "site_id": "site-9", "idempotency_key": "k-1",
    }
    assert json.loads(result.stdout)["volume"]["size_gb"] == 1
    result = run(["-o", "id", "block-storage", "create", "ab", "--size-gb", "1", "--site-id", "site-9"])
    assert result.stdout.strip() == "vol-1"


def test_volume_server_rule_errors_are_printed_as_received(gw) -> None:
    body = {"detail": {"code": "INVALID_VOLUME_SIZE", "message": "Size must be at least 10 GB."}}
    gw.on("POST", "block-storage/volumes", (422, body, REQ))
    result = run(["block-storage", "create", "ab", "--size-gb", "1", "--site-id", "site-9"])
    assert result.exit_code == EXIT_CODES[422] == 16
    text = plain(result)
    for part in ("Error: HTTP 422", "code: INVALID_VOLUME_SIZE", "message: Size must be at least 10 GB.",
                 "request_id: req-9"):
        assert part in text


def test_volume_attach_detach_resize_send_only_given_fields(gw) -> None:
    action = {"volume": volume(), "operation": operation()}
    gw.on("POST", "block-storage/volumes/vol-1/attachments", (200, action))
    gw.on("POST", "block-storage/volumes/vol-1/detach", (200, action))
    gw.on("POST", "block-storage/volumes/vol-1/resize", (200, action))
    assert run(["block-storage", "attach", "vol-1", "--node-name", "node-a"]).exit_code == 0
    assert gw.last("POST", "block-storage/volumes/vol-1/attachments").json == {"node_name": "node-a"}
    assert run(["block-storage", "detach", "vol-1", "--node-name", "node-a", "--yes"]).exit_code == 0
    assert gw.last("POST", "block-storage/volumes/vol-1/detach").json == {"node_name": "node-a"}
    result = run(["block-storage", "resize", "vol-1", "--new-size-gb", "5", "--idempotency-key", "k-2"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "block-storage/volumes/vol-1/resize").json == {"new_size_gb": 5, "idempotency_key": "k-2"}
    assert len(gw.calls) == 3  # no volume pre-read


def test_volume_delete_asks_then_sends_one_call(gw) -> None:
    gw.on("DELETE", "block-storage/volumes/vol-1", (200, {"status": "deleted", "id": "vol-1", "operation_id": "op-2"}))
    result = run(["block-storage", "delete", "vol-1"], input="n\n")
    assert result.exit_code == 1 and gw.calls == []
    result = run(["block-storage", "delete", "vol-1", "--force", "--yes"])
    assert result.exit_code == 0, result.output
    call = gw.last("DELETE", "block-storage/volumes/vol-1")
    assert call.params.get("force") == "true" and len(gw.calls) == 1
    assert json.loads(result.stdout)["operation_id"] == "op-2"


# -- Object Storage ----------------------------------------------------------


def test_bucket_list_table_and_json(gw) -> None:
    gw.on("GET", "object-storage/buckets", (200, {"buckets": [bucket()], "is_truncated": True,
                                                   "next_continuation_token": "tok-2"}))
    result = run(["buckets", "list", "--limit", "1"])
    assert result.exit_code == 0, result.output
    text = plain(result)
    for part in ("Buckets", "logs", "in-south-1", "yes", "2.0 KB", "--continuation-token tok-2"):
        assert part in text
    assert gw.last("GET", "object-storage/buckets").params.get("limit") == "1"
    result = run(["-o", "json", "buckets", "list"])
    assert json.loads(result.stdout)["buckets"][0]["name"] == "logs"


def test_bucket_create_sends_the_given_region(gw, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IBEE_REGION", raising=False)
    gw.on("POST", "object-storage/buckets", (200, bucket(region="eu-test-9")))
    result = run(["buckets", "create", "logs", "--region", "eu-test-9", "--tag", "a"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "object-storage/buckets").json == {"name": "logs", "region": "eu-test-9", "tags": ["a"]}
    result = run(["buckets", "create", "logs"])  # no host default: the region is required
    assert result.exit_code == 2 and len(gw.calls) == 1


def test_bucket_delete_with_and_without_yes(gw) -> None:
    gw.on("DELETE", "object-storage/buckets/logs", (200, {"detail": "Bucket deleted"}))
    result = run(["buckets", "delete", "logs"], input="n\n")
    assert result.exit_code == 1 and gw.calls == []
    result = run(["buckets", "delete", "logs", "--yes"])
    assert result.exit_code == 0, result.output
    assert [(c.method, c.path) for c in gw.calls] == [("DELETE", "object-storage/buckets/logs")]
    assert json.loads(result.stdout) == {"detail": "Bucket deleted"}


def test_bucket_delete_conflict_exit_code(gw) -> None:
    body = {"error": "bucket_not_empty", "message": "Bucket is not empty"}
    gw.on("DELETE", "object-storage/buckets/logs", (409, body, REQ))
    result = run(["buckets", "delete", "logs", "--yes"])
    assert result.exit_code == EXIT_CODES[409] == 15
    assert "code: bucket_not_empty" in plain(result) and "request_id: req-9" in plain(result)


def test_s3_credential_secret_is_never_reduced_to_an_id(gw) -> None:
    gw.on("POST", "object-storage/credentials", (200, {**credential(), "secret_access_key": "SECRET-1"}))
    for args in (["buckets", "credentials", "create", "--name", "ci"],
                 ["-o", "id", "buckets", "credentials", "create", "--name", "ci"]):
        result = run(args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["secret_access_key"] == "SECRET-1"
        assert "cannot be retrieved again" in result.stderr
    assert gw.last("POST", "object-storage/credentials").json == {"name": "ci"}


def test_s3_credentials_list_table_and_revoke(gw) -> None:
    gw.on("GET", "object-storage/credentials", (200, {"credentials": [credential()]}))
    gw.on("DELETE", "object-storage/credentials/AK1", (200, {"success": True, "message": "Revoked"}))
    result = run(["-o", "table", "buckets", "credentials", "list"])
    assert result.exit_code == 0, result.output
    for part in ("S3 credentials", "ci", "AK1", "object_ro", "logs", "active"):
        assert part in plain(result)
    assert run(["buckets", "credentials", "revoke", "AK1"], input="n\n").exit_code == 1
    result = run(["buckets", "credentials", "revoke", "AK1", "--yes"])
    assert result.exit_code == 0 and json.loads(result.stdout)["message"] == "Revoked"
    assert len(gw.writes()) == 1


# -- CDN ---------------------------------------------------------------------


def test_cdn_list_table_and_json(gw) -> None:
    gw.on("GET", "cdn/distributions", (200, {"distributions": [distribution()], "count": 1}))
    result = run(["-o", "table", "cdn", "list"])
    assert result.exit_code == 0, result.output
    for part in ("CDN distributions", "dist-1", "assets", "static-assets", "https://d1.cdn.example"):
        assert part in plain(result)
    result = run(["-o", "json", "cdn", "list"])
    assert json.loads(result.stdout)["count"] == 1


def test_cdn_create_sends_only_given_fields(gw) -> None:
    gw.on("POST", "cdn/distributions", (200, distribution()))
    result = run(["cdn", "create", "site", "--origin-id", "assets"])
    assert result.exit_code == 0, result.output
    assert gw.calls[-1].json == {"name": "site", "origin_id": "assets"} and len(gw.calls) == 1  # no origin read


def test_cdn_purge_is_sent_as_typed(gw) -> None:
    gw.on("POST", "cdn/distributions/dist-1/purge", (200, {"success": True, "mode": "url", "purged": ["/a,b", "/c"]}))
    result = run(["cdn", "purge", "dist-1", "--mode", "url", "--path", "/a,b", "--path", "/c"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "cdn/distributions/dist-1/purge").json == {"mode": "url", "paths": ["/a,b", "/c"]}
    result = run(["cdn", "purge", "dist-1", "--mode", "all"], input="n\n")
    assert result.exit_code == 1 and len(gw.calls) == 1


def test_cdn_purge_reported_failure_exits_1(gw) -> None:
    gw.on("POST", "cdn/distributions/dist-1/purge", (200, {"success": False, "mode": "tag", "message": "Purge failed"}))
    result = run(["cdn", "purge", "dist-1", "--mode", "tag", "--tag", "v1"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["message"] == "Purge failed"


def test_removed_commands_and_flags_are_usage_errors(gw) -> None:
    removed = (
        ["block-storage", "attach-vm", "vol-1", "vm-1"],
        ["block-storage", "detach-vm", "vol-1"],
        ["block-storage", "delete", "vol-1", "--check-state"],
        ["buckets", "delete", "logs", "--skip-preflight"],
        ["buckets", "credentials", "delete", "AK1"],
        ["cdn", "cache-policies"],
        ["cdn", "metrics", "dist-1"],
        ["cdn", "domains", "verify", "dist-1", "cdn.example.com", "--wait"],
    )
    for args in removed:
        assert run(args).exit_code == 2, args
    assert gw.calls == []
