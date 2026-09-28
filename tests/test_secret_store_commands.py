"""Secret Store commands against the real Python SDK and a scripted gateway.

The SDK's portal rules run for real: a broken rule exits 2 before any request.
"""

from __future__ import annotations

import json

import pytest

from ibee_cli.commands import secrets

from _net_fixtures import TS, WS, plain, run, runner
from ibee_cli.main import app

S = "secret-store/stores"
SEC = "secret-store/secrets"
IDN = "secret-store/identities"
SCP = "secret-store/scopes"
ELIGIBILITY = "billing/resource-eligibility"


def store(store_id="store-1", name="Production", status="active", **overrides):
    record = {
        "id": store_id,
        "organization_id": "org",
        "workspace_id": WS,
        "name": name,
        "store_key": name.lower(),
        "description": "",
        "status": status,
        "created_at": TS,
        "updated_at": TS,
    }
    record.update(overrides)
    return record


def store_list(*stores, total=None, page=1, limit=100):
    return {"stores": list(stores), "total": len(stores) if total is None else total, "page": page, "limit": limit}


def secret(secret_id="sec-1", name="db-url", status="active"):
    return {
        "id": secret_id,
        "organization_id": "org",
        "workspace_id": WS,
        "store_id": "store-1",
        "store_key": "production",
        "secret_name": name,
        "status": status,
        "created_at": TS,
        "updated_at": TS,
    }


def value(version=3):
    return {"id": "sec-1", "secret_name": "db-url", "data": {"url": "x"}, "metadata": {"version": version}}


def versions(current=3, destroyed=(), deleted=()):
    return {
        "secret_id": "sec-1",
        "secret_name": "db-url",
        "current_version": current,
        "oldest_version": 1,
        "versions": {
            str(n): {
                "version": n,
                "created_time": TS,
                "deletion_time": TS if n in deleted else "",
                "destroyed": n in destroyed,
            }
            for n in range(1, current + 1)
        },
    }


def identity(identity_id="idn-1", auth_method="approle", status="active", mode="read_write"):
    return {
        "id": identity_id,
        "organization_id": "org",
        "workspace_id": WS,
        "name": "worker",
        "auth_method": auth_method,
        "openbao_role_name": "role",
        "status": status,
        "token_policy_mode": mode,
        "created_at": TS,
        "updated_at": TS,
    }


def scope(scope_id="scp-1", store_id="store-1", access_mode="read_only"):
    return {
        "id": scope_id,
        "identity_id": "idn-1",
        "scope_type": "store",
        "store_id": store_id,
        "organization_id": "org",
        "workspace_id": WS,
        "access_mode": access_mode,
        "allow_version_read": True,
        "allow_rollback": False,
        "allow_destroy": False,
        "created_at": TS,
    }


def fail(status, code, message, details=None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return (status, {"error": error})


def ok(result):
    assert result.exit_code == 0, result.output
    return result


def usage(result):
    assert result.exit_code == 2, result.output
    return result


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


def test_stores_list_matches_portal_defaults_and_hints_at_more_pages(gw):
    gw.on("GET", S, store_list(store(), store("store-2", "Old", "archived"), total=120))
    result = ok(run(["secrets", "stores", "list"]))
    call = gw.last("GET", S)
    assert call.params == {"workspace_id": WS, "include_archived": "true", "page": "1", "limit": "100"}
    text = plain(result)
    assert "archived" in text and "Showing 2 of 120 stores" in text


def test_stores_list_active_only_and_all_pages(gw):
    gw.on("GET", S, store_list(store(), total=1))
    ok(run(["secrets", "stores", "list", "--active-only", "--limit", "5"]))
    assert gw.last("GET", S).params["include_archived"] == "false"
    gw.calls.clear()
    gw.routes.clear()
    gw.on("GET", S, store_list(store("a"), store("b"), total=3, limit=2), store_list(store("c"), total=3, page=2, limit=2))
    result = ok(run(["-o", "id", "secrets", "stores", "list", "--all", "--limit", "2"]))
    assert result.output.split() == ["a", "b", "c"]
    assert [c.params["page"] for c in gw.calls] == ["1", "2"]


@pytest.mark.parametrize("args", [["--all", "--page", "2"], ["--limit", "201"], ["--page", "0"]])
def test_stores_list_rejects_bad_paging(gw, args):
    usage(run(["secrets", "stores", "list", *args]))
    assert gw.calls == []


def test_secret_store_workspace_must_have_two_digits(gw):
    result = runner.invoke(app, ["--token", "t", "--workspace", "7", "secrets", "stores", "list"])
    usage(result)
    assert "2 to 128 digits" in plain(result)
    assert gw.calls == []


def test_store_create_runs_billing_preflight_then_creates_trimmed_name(gw):
    gw.on("POST", ELIGIBILITY, {"allowed": True, "reason": "ok", "sku_code": "SECRETMA-STD", "organization_id": "org"})
    gw.on("POST", S, store())
    result = ok(run(["secrets", "stores", "create", "  Production  ", "-d", " main "]))
    assert [(c.method, c.path) for c in gw.calls] == [("POST", ELIGIBILITY), ("POST", S)]
    assert gw.last("POST", ELIGIBILITY).json["sku_code"] == "SECRETMA-STD"
    assert gw.last("POST", S).json == {"name": "Production", "description": "main"}
    assert "created" in plain(result)


def test_store_create_billing_denied_sends_no_create(gw):
    gw.on("POST", ELIGIBILITY, {"allowed": False, "reason": "insufficient_balance", "sku_code": "SECRETMA-STD", "organization_id": "org"})
    result = run(["secrets", "stores", "create", "Production"])
    assert result.exit_code == 1, result.output
    assert [c.path for c in gw.calls] == [ELIGIBILITY]
    assert "402" in plain(result) or "Payment" in plain(result)


def test_store_create_skips_billing_check_without_billing_read(gw):
    gw.on("POST", ELIGIBILITY, (403, {"error": "insufficient_scope", "required_scope": "billing.read"}))
    gw.on("POST", S, store())
    result = ok(run(["secrets", "stores", "create", "Production"]))
    assert "Billing preflight skipped" in plain(result)
    assert gw.last("POST", S).json == {"name": "Production"}


def test_store_create_no_billing_check_sends_one_request(gw):
    gw.on("POST", S, store())
    ok(run(["secrets", "stores", "create", "Production", "--no-billing-check"]))
    assert [c.path for c in gw.calls] == [S]


@pytest.mark.parametrize("name", ["---", "   ", "x" * 129])
def test_store_create_name_rules(gw, name):
    usage(run(["secrets", "stores", "create", name, "--no-billing-check"]))
    assert gw.calls == []


def test_store_create_if_exists_reuse_returns_the_existing_store(gw):
    gw.on("POST", S, fail(409, "CONFLICT", "Store name already exists"))
    gw.on("GET", S, store_list(store("store-9", "production", "archived")))
    result = ok(run(["secrets", "stores", "create", "Production", "--no-billing-check", "--if-exists", "reuse"]))
    text = plain(result)
    assert "Store already exists" in text and "store-9" in text and "unarchive store-9" in text
    assert gw.last("GET", S).params["include_archived"] == "true"


def test_store_create_conflict_without_reuse_fails(gw):
    gw.on("POST", S, fail(409, "CONFLICT", "Store name already exists"))
    result = run(["secrets", "stores", "create", "Production", "--no-billing-check"])
    assert result.exit_code == 1
    assert "Conflict (409)" in plain(result)
    assert gw.requests("GET") == []


def test_store_update_requires_a_field_and_strips(gw):
    gw.on("PATCH", f"{S}/store-1", store(name="Renamed"))
    ok(run(["secrets", "stores", "update", "store-1", "--name", " Renamed "]))
    assert gw.last("PATCH", f"{S}/store-1").json == {"name": "Renamed"}


def test_store_update_archived_store_prints_hint(gw):
    gw.on("PATCH", f"{S}/store-1", fail(409, "STORE_ARCHIVED", "Store is archived"))
    result = run(["secrets", "stores", "update", "store-1", "--description", "x"])
    assert result.exit_code == 1
    assert "unarchive the store first" in plain(result)


def test_store_archive_confirms_with_portal_text_and_skips_archived(gw):
    gw.on("GET", f"{S}/store-1", store())
    gw.on("POST", f"{S}/store-1/archive", store(status="archived"))
    result = run(["secrets", "stores", "archive", "store-1"], input="n\n")
    assert result.exit_code == 1
    assert "Archiving Production will block access until you restore it" in plain(result)
    assert gw.writes() == []
    ok(run(["secrets", "stores", "archive", "store-1", "--yes"]))
    assert gw.last("POST", f"{S}/store-1/archive")

    gw.calls.clear()
    gw.routes.clear()
    gw.on("GET", f"{S}/store-1", store(status="archived"))
    result = ok(run(["secrets", "stores", "archive", "store-1", "--yes"]))
    assert "already archived" in plain(result)
    assert gw.writes() == []


def test_store_archive_refuses_a_deleting_store(gw):
    gw.on("GET", f"{S}/store-1", store(status="deleting"))
    usage(run(["secrets", "stores", "archive", "store-1", "--yes"]))
    assert gw.writes() == []


def test_store_unarchive_prompts_only_on_a_terminal(gw, monkeypatch):
    gw.on("GET", f"{S}/store-1", store(status="archived"))
    gw.on("POST", f"{S}/store-1/unarchive", store())
    ok(run(["secrets", "stores", "unarchive", "store-1"]))
    assert len(gw.writes()) == 1
    monkeypatch.setattr(secrets, "_stdin_is_tty", lambda: True)
    result = run(["secrets", "stores", "unarchive", "store-1"], input="n\n")
    assert result.exit_code == 1
    assert "Restore store?" in plain(result)
    assert len(gw.writes()) == 1
    ok(run(["secrets", "stores", "unarchive", "store-1", "--yes"]))
    assert len(gw.writes()) == 2


def test_store_unarchive_active_store_is_a_no_op(gw):
    gw.on("GET", f"{S}/store-1", store())
    result = ok(run(["secrets", "stores", "unarchive", "store-1"]))
    assert "already active" in plain(result)
    assert gw.writes() == []


def test_store_delete_permanent_prompt_and_incomplete_deletion(gw):
    gw.on("GET", f"{S}/store-1", store())
    gw.on(
        "DELETE",
        f"{S}/store-1/permanent",
        fail(503, "LIFECYCLE_OPERATION_INCOMPLETE", "Deletion incomplete", {"store_id": "store-1", "failed_steps": ["kv_cleanup"]}),
    )
    result = run(["secrets", "stores", "delete-permanent", "store-1"], input="n\n")
    assert result.exit_code == 1
    assert "This removes Production, its secrets, and its access entries" in plain(result)
    assert gw.writes() == []
    result = run(["secrets", "stores", "delete-permanent", "store-1", "--yes"])
    assert result.exit_code == 1
    text = plain(result)
    assert "failed steps: kv_cleanup" in text and "run the same command again" in text


def test_store_id_with_slash_is_rejected_before_any_request(gw):
    usage(run(["secrets", "stores", "get", "a/b"]))
    assert gw.calls == []


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


def test_secrets_list_query_paging_and_table(gw):
    gw.on("GET", f"{S}/store-1/secrets", {"secrets": [secret()], "total": 1, "page": 1, "limit": 100})
    result = ok(run(["secrets", "list", "-s", "store-1", "-q", "  db  "]))
    assert gw.last("GET", f"{S}/store-1/secrets").params == {
        "workspace_id": WS,
        "q": "db",
        "page": "1",
        "limit": "100",
    }
    assert "db-url" in plain(result)
    ok(run(["secrets", "list", "-s", "store-1", "-q", "   "]))
    assert "q" not in gw.last("GET", f"{S}/store-1/secrets").params
    usage(run(["secrets", "list", "-s", "store-1", "-q", "x" * 129]))


def test_secrets_list_all(gw):
    gw.on("GET", f"{S}/store-1/secrets", {"secrets": [secret("a"), secret("b")], "total": 2, "page": 1, "limit": 200})
    result = ok(run(["-o", "id", "secrets", "list", "-s", "store-1", "--all"]))
    assert result.output.split() == ["a", "b"]
    assert gw.last("GET", f"{S}/store-1/secrets").params["limit"] == "200"


def test_secret_create_normalizes_name_and_runs_preflight(gw):
    gw.on("POST", ELIGIBILITY, {"allowed": True, "reason": "ok", "sku_code": "SECRETMA-STD", "organization_id": "org"})
    gw.on("POST", f"{S}/store-1/secrets", secret())
    result = ok(run(["secrets", "create", "-s", "store-1", "-n", " DB-URL ", "--value", '{" url ":"postgres://x"}']))
    assert gw.last("POST", f"{S}/store-1/secrets").json == {"secret_name": "db-url", "value": {"url": "postgres://x"}}
    assert "normalized to 'db-url'" in plain(result)
    assert [c.path for c in gw.calls] == [ELIGIBILITY, f"{S}/store-1/secrets"]


@pytest.mark.parametrize(
    ("name", "val"),
    [
        ("a", "k=v"),
        ("-db", "k=v"),
        ("db_url", "k=v"),
        ("db", "k="),
        ("db", '{"k":"  "}'),
        ("db", "{}"),
        ("db", '{"k":null}'),
        ("db", '{"a":"1"," a":"2"}'),
    ],
)
def test_secret_create_rules_exit_2_without_requests(gw, name, val):
    usage(run(["secrets", "create", "-s", "store-1", "-n", name, "--value", val]))
    assert gw.calls == []


def test_secret_create_in_archived_store_prints_hint(gw):
    gw.on("POST", f"{S}/store-1/secrets", fail(409, "STORE_ARCHIVED", "Store is archived"))
    result = run(["secrets", "create", "-s", "store-1", "-n", "db", "--value", "k=v", "--no-billing-check"])
    assert result.exit_code == 1
    assert "unarchive the store first" in plain(result)


def test_batch_create_normalizes_warns_on_duplicates_and_reports_failures(gw, tmp_path):
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            [
                {"secret_name": "API-KEY", "value": {"k": "v"}},
                {"secret_name": "api-key", "value": {"k": "w"}},
                {"secret_name": "db", "value": {"u": "p"}},
            ]
        )
    )
    gw.on(
        "POST",
        f"{S}/store-1/secrets:batchIngest",
        {
            "results": [
                {"secret_name": "api-key", "status": "created", "secret": secret()},
                {"secret_name": "api-key", "status": "skipped", "error": "duplicate_in_request"},
                {"secret_name": "db", "status": "failed", "error": "write_failed"},
            ],
            "created_count": 1,
            "skipped_count": 1,
            "failed_count": 1,
        },
    )
    result = run(["secrets", "batch-create", "-s", "store-1", "-f", str(batch)])
    assert result.exit_code == 1, result.output
    text = plain(result)
    assert "duplicate secret names" in text and "api-key" in text
    assert "1 created, 1 skipped, 1 failed" in text and "failed: db (write_failed)" in text
    sent = gw.last("POST", f"{S}/store-1/secrets:batchIngest").json["secrets"]
    assert [item["secret_name"] for item in sent] == ["api-key", "api-key", "db"]


def test_batch_create_splits_large_files_and_merges_results(gw, tmp_path):
    items = [{"secret_name": f"s-{n:04d}", "value": {"k": "v"}} for n in range(650)]
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps({"secrets": items}))

    def response(count):
        return {"results": [], "created_count": count, "skipped_count": 0, "failed_count": 0}

    gw.on("POST", f"{S}/store-1/secrets:batchIngest", response(500), response(150))
    result = ok(run(["-o", "json", "secrets", "batch-create", "-s", "store-1", "-f", str(batch)]))
    posts = gw.requests("POST")
    assert [len(c.json["secrets"]) for c in posts] == [500, 150]
    assert json.loads(result.output)["created_count"] == 650


def test_batch_create_item_rule_names_the_index(gw, tmp_path):
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps([{"secret_name": "ok-1", "value": {"k": "v"}}, {"secret_name": "Bad Name", "value": {"k": "v"}}]))
    result = usage(run(["secrets", "batch-create", "-s", "store-1", "-f", str(batch)]))
    assert "secrets[1]" in plain(result)
    assert gw.calls == []


def test_set_value_cas_conflict_and_validation(gw):
    gw.on("PUT", f"{SEC}/sec-1/value", fail(502, "OPENBAO_ERROR", "OpenBao request failed"))
    result = run(["secrets", "set-value", "sec-1", "--value", "k=v", "--cas", "2"])
    assert result.exit_code == 1
    assert "Check-and-set conflict" in plain(result)
    assert gw.last("PUT", f"{SEC}/sec-1/value").json == {"value": {"k": "v"}, "cas": 2}
    assert len(gw.requests("PUT")) == 1  # never retried
    usage(run(["secrets", "set-value", "sec-1", "--value", "k=v", "--cas", "-1"]))


def test_set_value_reports_new_version(gw):
    gw.on("PUT", f"{SEC}/sec-1/value", value(4))
    result = ok(run(["secrets", "set-value", "sec-1", "--value", "k=v"]))
    assert "version 4" in plain(result)
    assert gw.last("PUT", f"{SEC}/sec-1/value").json == {"value": {"k": "v"}}


def test_patch_value_allows_null_to_delete_a_key(gw):
    gw.on("PATCH", f"{SEC}/sec-1/value", value(5))
    ok(run(["secrets", "patch-value", "sec-1", "--value", '{"old":null,"new":"1"}']))
    assert gw.last("PATCH", f"{SEC}/sec-1/value").json == {"value": {"old": None, "new": "1"}}


def test_value_of_soft_deleted_secret_prints_hint(gw):
    gw.on("GET", f"{SEC}/sec-1/value", fail(404, "NOT_FOUND", "Secret value not found"))
    result = run(["secrets", "value", "sec-1"])
    assert result.exit_code == 1
    assert "undelete it or write a new value" in plain(result)


def test_versions_table_is_newest_first_with_portal_states(gw):
    gw.on("GET", f"{SEC}/sec-1/versions", versions(current=3, destroyed=(1,), deleted=(2,)))
    result = ok(run(["-o", "table", "secrets", "versions", "sec-1"]))
    lines = [line for line in result.output.splitlines() if line.strip().startswith("│")]
    body = [line for line in lines if any(ch.isdigit() for ch in line.split("│")[1])]
    states = [line.split("│")[2].strip() for line in body]
    assert states == ["active", "soft_deleted", "destroyed"]
    # Default output stays JSON.
    result = ok(run(["secrets", "versions", "sec-1"]))
    assert json.loads(result.output)["current_version"] == 3


@pytest.mark.parametrize(("target", "message"), [(3, "already the current version"), (1, "destroyed"), (9, "does not exist")])
def test_rollback_refuses_portal_disabled_targets(gw, target, message):
    gw.on("GET", f"{SEC}/sec-1/versions", versions(current=3, destroyed=(1,)))
    result = usage(run(["secrets", "rollback", "sec-1", "--version", str(target)]))
    assert message in plain(result)
    assert gw.writes() == []


def test_rollback_sends_after_check_and_without_it(gw):
    gw.on("GET", f"{SEC}/sec-1/versions", versions(current=3))
    gw.on("POST", f"{SEC}/sec-1/rollback", value(4))
    result = ok(run(["secrets", "rollback", "sec-1", "--version", "2"]))
    assert "restored from version 2 as version 4" in plain(result)
    gw.calls.clear()
    ok(run(["secrets", "rollback", "sec-1", "--version", "3", "--no-check-state"]))
    assert [(c.method, c.path) for c in gw.calls] == [("POST", f"{SEC}/sec-1/rollback")]


def test_undelete_defaults_to_current_version_and_dedupes(gw):
    gw.on("GET", f"{SEC}/sec-1/versions", versions(current=3))
    gw.on("POST", f"{SEC}/sec-1/undelete", secret())
    ok(run(["secrets", "undelete", "sec-1"]))
    assert gw.last("POST", f"{SEC}/sec-1/undelete").json == {"versions": [3]}
    ok(run(["secrets", "undelete", "sec-1", "--versions", "2,2,1"]))
    assert gw.last("POST", f"{SEC}/sec-1/undelete").json == {"versions": [2, 1]}
    usage(run(["secrets", "undelete", "sec-1", "--versions", "0"]))
    usage(run(["secrets", "undelete", "sec-1", "--versions", ",".join(str(n) for n in range(1, 102))]))


def test_delete_prompt_names_the_secret(gw):
    gw.on("GET", f"{SEC}/sec-1", secret())
    gw.on("DELETE", f"{SEC}/sec-1", secret(status="soft_deleted"))
    result = run(["secrets", "delete", "sec-1"], input="n\n")
    assert result.exit_code == 1
    assert "This will soft-delete db-url" in plain(result)
    assert gw.writes() == []
    ok(run(["secrets", "delete", "sec-1", "--yes"]))
    assert gw.last("DELETE", f"{SEC}/sec-1")


# ---------------------------------------------------------------------------
# Identities and scopes
# ---------------------------------------------------------------------------


def test_identity_create_strips_fields_and_always_sends_policy(gw):
    gw.on("POST", f"{S}/store-1/identities", identity(auth_method="kubernetes"))
    ok(
        run(
            [
                "secrets", "identities", "create", "-s", "store-1", "-n", " worker ",
                "--auth-method", "kubernetes", "--k8s-namespace", " apps ", "--k8s-service-account", " api ",
            ]
        )
    )
    assert gw.last("POST", f"{S}/store-1/identities").json == {
        "auth_method": "kubernetes",
        "name": "worker",
        "token_policy_mode": "read_only",
        "k8s_namespace": "apps",
        "k8s_service_account": "api",
    }


def test_identity_create_blank_kubernetes_fields_and_long_name(gw):
    usage(
        run(
            [
                "secrets", "identities", "create", "-s", "store-1", "-n", "w",
                "--auth-method", "kubernetes", "--k8s-namespace", "  ", "--k8s-service-account", "api",
            ]
        )
    )
    usage(run(["secrets", "identities", "create", "-s", "store-1", "-n", "x" * 129, "--auth-method", "approle"]))
    assert gw.calls == []


def test_identity_create_in_inactive_store_prints_hint(gw):
    gw.on("POST", f"{S}/store-1/identities", fail(403, "FORBIDDEN", "Store 'store-1' is not active"))
    result = run(["secrets", "identities", "create", "-s", "store-1", "-n", "w", "--auth-method", "approle"])
    assert result.exit_code == 1
    assert "restore (unarchive) the store first" in plain(result)


def test_identity_update_warns_about_scopes_and_sessions(gw):
    gw.on("PATCH", f"{IDN}/idn-1", identity(mode="read_only"))
    result = ok(run(["secrets", "identities", "update", "idn-1", "--token-policy-mode", "read_only"]))
    assert "rewrites every scope" in plain(result)
    assert gw.last("PATCH", f"{IDN}/idn-1").json == {"token_policy_mode": "read_only"}


def test_identity_disable_prompts_only_on_a_terminal(gw, monkeypatch):
    gw.on("POST", f"{IDN}/idn-1/disable", identity(status="disabled"))
    ok(run(["secrets", "identities", "disable", "idn-1"]))
    monkeypatch.setattr(secrets, "_stdin_is_tty", lambda: True)
    result = run(["secrets", "identities", "disable", "idn-1"], input="n\n")
    assert result.exit_code == 1
    assert "until it is enabled again" in plain(result)
    assert len(gw.writes()) == 1


def test_identity_access_for_disabled_identity(gw):
    gw.on("GET", f"{IDN}/idn-1/access", fail(403, "FORBIDDEN", "Identity is disabled"))
    result = run(["secrets", "identities", "access", "idn-1", "--show-sensitive"])
    assert result.exit_code == 1
    assert "enable the identity first" in plain(result)
    assert len(gw.calls) == 1  # never retried


@pytest.mark.parametrize(
    ("record", "message"),
    [(identity(auth_method="kubernetes"), "only available for AppRole"), (identity(status="disabled"), "disabled")],
)
def test_rotate_checks_auth_method_and_status_first(gw, record, message):
    gw.on("GET", f"{IDN}/idn-1", record)
    result = usage(run(["secrets", "identities", "rotate-secret-id", "idn-1", "--show-sensitive"]))
    assert message in plain(result)
    assert gw.writes() == []


def test_rotate_sends_after_check(gw):
    gw.on("GET", f"{IDN}/idn-1", identity())
    gw.on("POST", f"{IDN}/idn-1/rotate-secret-id", {"identity_id": "idn-1", "auth_method": "approle", "role_id": "r", "secret_id": "s"})
    ok(run(["secrets", "identities", "rotate-secret-id", "idn-1", "--show-sensitive"]))
    assert [c.method for c in gw.calls] == ["GET", "POST"]


def test_scope_create_defaults_and_store_checks(gw):
    gw.on("GET", f"{IDN}/idn-1", identity())
    gw.on("GET", f"{IDN}/idn-1/scopes", {"scopes": [scope(store_id="store-1")], "total": 1})
    gw.on("GET", S, store_list(store("store-1"), store("store-2"), store("store-3", "Old", "archived")))
    gw.on("POST", f"{IDN}/idn-1/scopes", scope("scp-2", "store-2"))
    ok(run(["secrets", "identities", "scopes", "create", "idn-1", "-s", "store-2"]))
    assert gw.last("POST", f"{IDN}/idn-1/scopes").json == {
        "store_id": "store-2",
        "access_mode": "read_only",
        "allow_version_read": True,
        "allow_rollback": False,
        "allow_destroy": False,
    }
    for store_id, message in (("store-1", "already granted"), ("store-3", "archived"), ("store-x", "not found")):
        result = usage(run(["secrets", "identities", "scopes", "create", "idn-1", "-s", store_id]))
        assert message in plain(result)
    assert len(gw.writes()) == 1


def test_scope_create_read_only_identity_gets_read_only(gw):
    gw.on("GET", f"{IDN}/idn-1", identity(mode="read_only"))
    gw.on("GET", f"{IDN}/idn-1/scopes", {"scopes": [], "total": 0})
    gw.on("GET", S, store_list(store("store-2")))
    result = usage(run(["secrets", "identities", "scopes", "create", "idn-1", "-s", "store-2", "--access-mode", "read_write"]))
    assert "Read-only identities" in plain(result)
    assert gw.writes() == []


def test_scope_create_permission_combination_and_no_check_state(gw):
    usage(run(["secrets", "identities", "scopes", "create", "idn-1", "-s", "store-2", "--allow-rollback"]))
    assert gw.calls == []
    gw.on("POST", f"{IDN}/idn-1/scopes", scope("scp-2", "store-2", "read_write"))
    ok(
        run(
            [
                "secrets", "identities", "scopes", "create", "idn-1", "-s", "store-2",
                "--access-mode", "read_write", "--allow-destroy", "--deny-version-read", "--no-check-state",
            ]
        )
    )
    assert [c.method for c in gw.calls] == ["POST"]
    body = gw.last("POST", f"{IDN}/idn-1/scopes").json
    assert body["allow_destroy"] is True and body["allow_version_read"] is False


def test_scope_update_combination_and_422_hint(gw):
    usage(run(["secrets", "identities", "scopes", "update", "scp-1", "--access-mode", "read_only", "--allow-destroy"]))
    assert gw.calls == []
    gw.on(
        "PATCH",
        f"{SCP}/scp-1",
        fail(422, "VALIDATION_ERROR", "Read-only scopes cannot grant rollback or destroy permissions"),
    )
    result = run(["secrets", "identities", "scopes", "update", "scp-1", "--allow-rollback"])
    assert result.exit_code == 1
    assert "send access_mode='read_write'" in plain(result)


# ---------------------------------------------------------------------------
# Error rendering
# ---------------------------------------------------------------------------


def test_lifecycle_denial_is_explained(gw):
    gw.on(
        "GET",
        S,
        fail(403, "FORBIDDEN", "Operation 'READ_RESOURCE' is not allowed while organization is suspended"),
    )
    result = run(["secrets", "stores", "list"])
    assert result.exit_code == 1
    assert "organization is suspended, so READ_RESOURCE operations are not allowed" in plain(result)


def test_not_owned_resource_is_reported_as_not_found(gw):
    gw.on("GET", f"{SEC}/sec-9", fail(403, "FORBIDDEN", f"Secret 'sec-9' does not belong to workspace '{WS}'"))
    result = run(["secrets", "get", "sec-9"])
    assert result.exit_code == 1
    text = plain(result)
    assert "Secret 'sec-9' does not exist or belongs to a different workspace" in text
    assert "IBEE_WORKSPACE_ID" in text


def test_help_for_every_secret_command_renders():
    groups = {
        (): ["list", "create", "batch-create", "get", "value", "set-value", "patch-value", "versions", "version",
             "rollback", "undelete", "destroy-versions", "delete-permanent", "delete"],
        ("stores",): ["list", "create", "get", "update", "archive", "unarchive", "delete-permanent"],
        ("identities",): ["list", "create", "get", "update", "disable", "enable", "access", "rotate-secret-id",
                          "revoke-sessions", "delete"],
        ("identities", "scopes"): ["list", "create", "update", "delete"],
    }
    for prefix, commands in groups.items():
        for command in commands:
            result = runner.invoke(app, ["secrets", *prefix, command, "--help"])
            assert result.exit_code == 0, (prefix, command, result.output)
