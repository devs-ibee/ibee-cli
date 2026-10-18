"""Secret Store commands: one SDK call each, sent as typed, the server's answer printed."""

from __future__ import annotations

import json

import pytest
from _net_fixtures import TS, WS, plain, run

from ibee_cli.render import EXIT_CODES

REQ = {"x-request-id": "req-7"}


def store(**overrides):
    record = {"id": "st-1", "organization_id": "org", "workspace_id": WS, "name": "payments",
              "store_key": "payments", "description": "keys", "status": "active",
              "created_at": TS, "updated_at": TS}
    record.update(overrides)
    return record


def secret(**overrides):
    record = {"id": "sec-1", "organization_id": "org", "workspace_id": WS, "store_id": "st-1",
              "store_key": "payments", "secret_name": "db-url", "status": "active",
              "created_at": TS, "updated_at": TS}
    record.update(overrides)
    return record


ACCESS = {"identity_id": "id-1", "auth_method": "approle", "role_id": "role-1",
          "secret_id": "fresh-secret-id", "secret_id_accessor": "acc-1"}


# -- lists -------------------------------------------------------------------


def test_store_list_table_and_json(gw) -> None:
    gw.on("GET", "secret-store/stores", (200, {"stores": [store()], "total": 1, "page": 1, "limit": 50}))
    result = run(["secrets", "stores", "list"])
    assert result.exit_code == 0, result.output
    text = plain(result)
    for part in ("Secret stores", "st-1", "payments", "active", "2026-09-01"):
        assert part in text
    # Only the flag's own default is sent; page and limit are left to the server.
    assert gw.last("GET", "secret-store/stores").params == {"workspace_id": WS, "include_archived": "true"}

    result = run(["-o", "json", "secrets", "stores", "list", "--active-only", "--limit", "500"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["stores"][0]["id"] == "st-1"
    params = gw.last("GET", "secret-store/stores").params
    assert params["include_archived"] == "false" and params["limit"] == "500"


def test_secret_list_passes_query_as_typed(gw) -> None:
    gw.on("GET", "secret-store/stores/st-1/secrets", (200, {"secrets": [secret()], "total": 1}))
    result = run(["secrets", "list", "-s", "st-1", "-q", "  DB  "])
    assert result.exit_code == 0, result.output
    assert "db-url" in plain(result)
    assert gw.last("GET", "secret-store/stores/st-1/secrets").params == {"workspace_id": WS, "q": "  DB  "}


# -- creates -----------------------------------------------------------------


def test_store_create_sends_body_unchanged(gw) -> None:
    gw.on("POST", "secret-store/stores", (201, store(name="My Store!")))
    result = run(["secrets", "stores", "create", "My Store!", "-d", "team keys"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "secret-store/stores").json == {"name": "My Store!", "description": "team keys"}
    assert json.loads(result.stdout)["name"] == "My Store!"
    assert len(gw.calls) == 1


def test_secret_create_sends_name_as_typed(gw) -> None:
    gw.on("POST", "secret-store/stores/st-1/secrets", (201, secret(secret_name="DB_URL")))
    result = run(["secrets", "create", "-s", "st-1", "--name", "DB_URL", "--value", "user=app, pass=x"])
    assert result.exit_code == 0, result.output
    body = gw.last("POST", "secret-store/stores/st-1/secrets").json
    assert body == {"secret_name": "DB_URL", "value": {"user": "app", "pass": "x"}}
    assert len(gw.calls) == 1


def test_batch_create_is_one_request_and_exits_1_on_failures(gw, tmp_path) -> None:
    items = [{"secret_name": f"k{i}", "value": {"v": str(i)}} for i in range(501)]
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({"secrets": items}))
    answer = {"results": [{"secret_name": "k0", "status": "failed", "error": "name_taken"}],
              "created_count": 500, "skipped_count": 0, "failed_count": 1}
    gw.on("POST", "secret-store/stores/st-1/secrets:batchIngest", (200, answer))
    result = run(["secrets", "batch-create", "-s", "st-1", "-f", str(path)])
    assert result.exit_code == 1, result.output
    assert len(gw.calls) == 1 and gw.calls[0].json == {"secrets": items}
    text = plain(result)
    assert "500 created" in text and "1 failed" in text and "name_taken" in text


def test_scope_create_sends_only_given_fields(gw) -> None:
    scope = {"id": "sc-1", "identity_id": "id-1", "scope_type": "store", "store_id": "st-2",
             "organization_id": "org", "workspace_id": WS, "access_mode": "read_only",
             "allow_version_read": True, "allow_rollback": True, "allow_destroy": False, "created_at": TS}
    gw.on("POST", "secret-store/identities/id-1/scopes", (201, scope))
    result = run(["secrets", "identities", "scopes", "create", "id-1", "-s", "st-2", "--allow-rollback"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "secret-store/identities/id-1/scopes").json == {"store_id": "st-2", "allow_rollback": True}


# -- confirmations -----------------------------------------------------------


def test_delete_with_yes_sends_one_request(gw) -> None:
    gw.on("DELETE", "secret-store/secrets/sec-1", (200, secret(status="soft_deleted")))
    result = run(["secrets", "delete", "sec-1", "--yes"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "soft_deleted"
    assert [(c.method, c.path) for c in gw.calls] == [("DELETE", "secret-store/secrets/sec-1")]


def test_delete_declined_exits_1_and_sends_nothing(gw) -> None:
    result = run(["secrets", "delete", "sec-1"], input="n\n")
    assert result.exit_code == 1
    assert "Delete secret 'sec-1'?" in result.output
    assert gw.calls == []


def test_destroy_versions_prompt_then_versions_as_typed(gw) -> None:
    gw.on("POST", "secret-store/secrets/sec-1/destroy", (200, {"status": "destroyed"}))
    result = run(["secrets", "destroy-versions", "sec-1", "--versions", "3, 1,3"], input="y\n")
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "secret-store/secrets/sec-1/destroy").json == {"versions": [3, 1, 3]}

    result = run(["secrets", "undelete", "sec-1", "--versions", "one"])
    assert result.exit_code == 2
    assert len(gw.calls) == 1


# -- secret display ----------------------------------------------------------


def test_identity_access_needs_show_sensitive_or_structured_output(gw) -> None:
    result = run(["secrets", "identities", "access", "id-1"])
    assert result.exit_code == 2
    assert "--show-sensitive" in result.output
    assert gw.calls == []

    gw.on("GET", "secret-store/identities/id-1/access", (200, ACCESS))
    result = run(["secrets", "identities", "access", "id-1", "--show-sensitive"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["secret_id"] == "fresh-secret-id"

    result = run(["-o", "json", "secrets", "identities", "access", "id-1"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["secret_id"] == "fresh-secret-id"


def test_rotate_secret_id_with_output_id_prints_the_identity_not_the_secret(gw) -> None:
    gw.on("POST", "secret-store/identities/id-1/rotate-secret-id", (200, ACCESS))
    result = run(["-o", "id", "secrets", "identities", "rotate-secret-id", "id-1"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "id-1"
    assert "fresh-secret-id" not in result.output


# -- errors ------------------------------------------------------------------


def test_api_error_prints_status_code_message_and_request_id(gw) -> None:
    body = {"detail": {"code": "secret_not_found", "message": "Secret not found"}}
    gw.on("GET", "secret-store/secrets/sec-9", (404, body, REQ))
    result = run(["secrets", "get", "sec-9"])
    assert result.exit_code == EXIT_CODES[404] == 14
    text = plain(result)
    for part in ("HTTP 404", "code: secret_not_found", "message: Secret not found", "request_id: req-7"):
        assert part in text


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (403, {"error": "insufficient_scope", "required_scope": "secret-store.write"}, 13),
        (423, {"detail": {"code": "store_archived", "message": "Store is archived"}}, 17),
        (503, {"detail": {"code": "upstream_unavailable", "message": "Try again"}}, 19),
    ],
)
def test_server_rules_are_relayed_with_the_status_exit_code(gw, status, body, code) -> None:
    gw.on("POST", "secret-store/stores/st-1/secrets", (status, body, REQ))
    result = run(["secrets", "create", "-s", "st-1", "-n", "Bad Name", "--value", '{"k": "v"}'])
    assert result.exit_code == code
    assert f"HTTP {status}" in plain(result)
    assert gw.last("POST", "secret-store/stores/st-1/secrets").json["secret_name"] == "Bad Name"
