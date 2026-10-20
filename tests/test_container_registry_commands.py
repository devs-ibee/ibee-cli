"""Container Registry commands: one SDK call each, sent as typed, the server's answer printed."""

from __future__ import annotations

import json

from _net_fixtures import TS, WS, plain, run

from ibee_cli.render import EXIT_CODES

REG = "6f1c2c1e-8d3b-4c55-9a43-0d6c1f0b9e11"
TOKEN = "0b8f6a52-2d0e-4c1b-8a35-9f5e3c7d2a10"
R = f"container-registry/registries/{REG}"


def registry(**overrides):
    record = {
        "registry_id": REG, "registry_name": "acme-apps", "registry_kind": "CUSTOMER", "organization_id": "1",
        "workspace_id": WS, "visibility": "PRIVATE", "security": {"vulnerability_pull_policy": "ALLOW"},
        "desired_state": "ACTIVE", "observed_state": "ACTIVE", "generation": 1, "observed_generation": 1,
        "created_at": TS, "updated_at": TS, "deleted_at": None,
    }
    record.update(overrides)
    return record


SECRET = {
    "token_id": TOKEN, "token_name": "ci-push", "username": "robot$acme+ci-push", "secret": "s3cr3t-value",
    "permission": "PULL_PUSH", "registry_ids": [REG], "expires_at": None, "created_at": TS,
}
TOKEN_ROW = {
    "token_id": TOKEN, "token_name": "ci-push", "harbor_username": "robot$acme+ci-push", "permission": "PULL_PUSH",
    "registry_ids": [REG], "disabled": False, "expires_at": None, "created_by_user_id": None, "created_at": TS,
    "revoked_at": None,
}


def test_list_table_json_and_ids(gw) -> None:
    page = {"items": [registry()], "total": 1, "page": 1, "page_size": 20}
    gw.on("GET", "container-registry/registries", (200, page))
    result = run(["registries", "list"])
    assert result.exit_code == 0, result.output
    for part in ("Container registries", "acme-apps", "PRIVATE", "ACTIVE"):
        assert part in plain(result)
    assert gw.last("GET", "container-registry/registries").params == {"workspace_id": WS}

    result = run(["-o", "json", "registries", "list", "--page", "2", "--page-size", "100"])
    assert json.loads(result.stdout)["items"][0]["registry_id"] == REG
    params = gw.last("GET", "container-registry/registries").params
    assert (params["page"], params["page_size"]) == ("2", "100")

    result = run(["-o", "id", "registries", "list"])
    assert result.stdout.strip() == REG


def test_create_sends_the_name_twice_and_the_sku(gw) -> None:
    gw.on("POST", "container-registry/registries", (202, registry(observed_state="PROVISIONING")))
    result = run([
        "registries", "create", "acme-apps", "--sku", "REGISTRY-STORAGE-250GB", "--visibility", "PUBLIC",
        "--ack-public-egress", "--pull-policy", "BLOCK_CRITICAL",
    ])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", "container-registry/registries").json == {
        "registry_name": "acme-apps", "confirm_registry_name": "acme-apps",
        "billing_catalog": {"sku_code": "REGISTRY-STORAGE-250GB"}, "visibility": "PUBLIC",
        "acknowledge_public_egress_charges": True, "security": {"vulnerability_pull_policy": "BLOCK_CRITICAL"},
    }
    assert json.loads(result.stdout)["observed_state"] == "PROVISIONING"


def test_create_refusal_is_printed_as_sent(gw) -> None:
    body = {"detail": {"code": "billing_resource_not_eligible", "reason": "kyc_required"}}
    gw.on("POST", "container-registry/registries", (403, body))
    result = run(["registries", "create", "acme-apps", "--sku", "X"])
    assert result.exit_code == EXIT_CODES[403]
    assert "billing_resource_not_eligible" in plain(result)
    assert len(gw.calls) == 1


def test_delete_and_empty_send_the_confirmation(gw) -> None:
    gw.on("DELETE", R, (202, registry(observed_state="DELETING")))
    result = run(["registries", "delete", REG, "--confirm-name", "acme-apps", "--yes"])
    assert result.exit_code == 0, result.output
    assert gw.last("DELETE", R).params == {"workspace_id": WS, "confirm_registry_name": "acme-apps"}

    gw.on("POST", f"{R}/empty", (200, {"registry_id": REG, "registry_name": "acme-apps", "deleted_repository_count": 2}))
    result = run(["registries", "empty", REG, "--confirm-name", "acme-apps", "--yes"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", f"{R}/empty").params["confirm_registry_name"] == "acme-apps"

    result = run(["registries", "delete", REG, "--confirm-name", "acme-apps"], input="n\n")
    assert result.exit_code == 1
    assert len(gw.requests("DELETE")) == 1


def test_visibility_and_security_bodies(gw) -> None:
    gw.on("PATCH", f"{R}/visibility", (202, registry(visibility="PUBLIC")))
    result = run(["registries", "visibility", REG, "PUBLIC", "--ack-public-egress"])
    assert result.exit_code == 0, result.output
    assert gw.last("PATCH", f"{R}/visibility").json == {"visibility": "PUBLIC", "acknowledge_public_egress_charges": True}

    status = {"vulnerability_pull_policy": "BLOCK_HIGH", "scan_on_push": True, "sbom_generation_on_push": True,
              "observed_state": "UPDATING", "generation": 2, "observed_generation": 1}
    gw.on("PATCH", f"{R}/security/settings", (202, status))
    result = run(["registries", "security", "update", REG, "--pull-policy", "BLOCK_HIGH"])
    assert result.exit_code == 0, result.output
    assert gw.last("PATCH", f"{R}/security/settings").json == {"vulnerability_pull_policy": "BLOCK_HIGH"}


def test_plan_upgrade_sends_a_key(gw) -> None:
    operation = {"operation_id": "1d5d2a3e-55b5-4b7e-9d4c-2f3e0a1b2c3d", "registry_id": REG, "kind": "UPGRADE",
                 "status": "PENDING", "sku_code": "REGISTRY-STORAGE-500GB", "quota_bytes": 500000000000,
                 "quota_gb": 500, "created_at": TS, "effective_at": None}
    gw.on("POST", f"{R}/upgrade", (202, operation))
    result = run(["registries", "plan", "upgrade", REG, "--sku", "REGISTRY-STORAGE-500GB"])
    assert result.exit_code == 0, result.output
    call = gw.last("POST", f"{R}/upgrade")
    assert call.json == {"billing_catalog": {"sku_code": "REGISTRY-STORAGE-500GB"}}
    assert call.headers["idempotency-key"]

    run(["registries", "plan", "upgrade", REG, "--sku", "REGISTRY-STORAGE-500GB", "--idempotency-key", "up-1"])
    assert gw.last("POST", f"{R}/upgrade").headers["idempotency-key"] == "up-1"


def test_token_secret_needs_show_sensitive_or_structured_output(gw) -> None:
    result = run(["registries", "tokens", "create", REG, "ci-push"])
    assert result.exit_code == 2
    assert "--show-sensitive" in result.output
    assert gw.calls == []

    gw.on("POST", f"{R}/access-tokens", (201, SECRET))
    result = run(["registries", "tokens", "create", REG, "ci-push", "--permission", "PULL_PUSH",
                  "--expires-in-days", "30", "--show-sensitive"])
    assert result.exit_code == 0, result.output
    assert gw.last("POST", f"{R}/access-tokens").json == {"token_name": "ci-push", "permission": "PULL_PUSH",
                                                          "expires_in_days": 30}
    assert json.loads(result.stdout)["secret"] == "s3cr3t-value"

    result = run(["-o", "id", "registries", "tokens", "create", REG, "ci-push"])
    assert result.stdout.strip() == TOKEN
    assert "s3cr3t-value" not in result.output

    result = run(["registries", "tokens", "rotate", REG, TOKEN])
    assert result.exit_code == 2
    gw.on("POST", f"{R}/access-tokens/{TOKEN}/rotate", (200, SECRET))
    result = run(["-o", "json", "registries", "tokens", "rotate", REG, TOKEN])
    assert json.loads(result.stdout)["secret"] == "s3cr3t-value"


def test_token_list_and_revoke(gw) -> None:
    gw.on("GET", f"{R}/access-tokens", (200, [TOKEN_ROW]))
    result = run(["registries", "tokens", "list", REG, "--include-revoked"])
    assert result.exit_code == 0, result.output
    assert "ci-push" in plain(result) and "s3cr3t" not in result.output
    assert gw.last("GET", f"{R}/access-tokens").params["include_revoked"] == "true"

    gw.on("DELETE", f"{R}/access-tokens/{TOKEN}", (200, {"detail": "Token revoked"}))
    result = run(["registries", "tokens", "revoke", REG, TOKEN, "--yes"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"detail": "Token revoked"}


def test_repositories_and_artifacts(gw) -> None:
    repos = {"items": [{"id": 1, "name": "team/api", "artifact_count": 2, "pull_count": 5, "update_time": TS}],
             "total": 1, "page": 1, "page_size": 20}
    gw.on("GET", f"{R}/repositories", (200, repos))
    result = run(["registries", "repositories", "list", REG, "--search", "api", "--sort", "-update_time"])
    assert result.exit_code == 0, result.output
    assert "team/api" in plain(result)
    assert gw.last("GET", f"{R}/repositories").params == {"workspace_id": WS, "search": "api", "sort": "-update_time"}

    gw.on("DELETE", f"{R}/repositories/team/api", (200, {"detail": "deleted"}))
    result = run(["registries", "repositories", "delete", REG, "team/api", "--yes"])
    assert result.exit_code == 0, result.output
    call = gw.last("DELETE", f"{R}/repositories/team/api")
    assert call.params["confirm"] == "true"

    arts = {"items": [{"id": 9, "digest": "sha256:abc", "size": 10, "tags": [{"name": "v1"}], "push_time": TS}],
            "total": 1, "page": 1, "page_size": 20}
    gw.on("GET", f"{R}/artifacts", (200, arts))
    result = run(["registries", "artifacts", "list", REG, "--repository", "team/api"])
    assert result.exit_code == 0, result.output
    assert "sha256:abc" in plain(result) and "v1" in plain(result)
    assert gw.last("GET", f"{R}/artifacts").params["repository_name"] == "team/api"

    gw.on("POST", f"{R}/artifacts/sha256:abc/scan", (202, {"detail": "queued"}))
    result = run(["registries", "artifacts", "rescan", REG, "sha256:abc", "-r", "team/api"])
    assert result.exit_code == 0, result.output

    gw.on("DELETE", f"{R}/artifacts/v1", (200, {"detail": "deleted"}))
    result = run(["registries", "artifacts", "delete", REG, "v1", "-r", "team/api", "--yes"])
    assert result.exit_code == 0, result.output
    assert gw.last("DELETE", f"{R}/artifacts/v1").params == {"workspace_id": WS, "repository_name": "team/api",
                                                             "confirm": "true"}


def test_sbom_download_writes_the_bytes(gw, tmp_path) -> None:
    doc = {"spdxVersion": "SPDX-2.3"}
    gw.on("GET", f"{R}/security/sboms/sha256:abc/download", (200, doc))
    out = tmp_path / "sbom.json"
    result = run(["registries", "sboms", "download", REG, "sha256:abc", "-r", "api", "--file", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_bytes()) == doc

    result = run(["registries", "sboms", "download", REG, "sha256:abc", "-r", "api"])
    assert json.loads(result.stdout) == doc


def test_reads_pass_filters_as_typed(gw) -> None:
    gw.on("GET", f"{R}/security/vulnerabilities", (200, {"items": [], "total": 0, "page": 1, "page_size": 20}))
    result = run(["registries", "vulnerabilities", "list", REG, "--severity", "Critical", "--repository", "api"])
    assert result.exit_code == 0, result.output
    assert gw.last("GET", f"{R}/security/vulnerabilities").params == {
        "workspace_id": WS, "severity": "Critical", "repository_name": "api"}

    gw.on("GET", f"{R}/metrics", (200, {"registry_id": REG, "range": "7d"}))
    result = run(["registries", "metrics", REG, "--range", "7d"])
    assert gw.last("GET", f"{R}/metrics").params["range"] == "7d"

    gw.on("GET", "container-registry/registry-names/acme-apps/availability",
          (200, {"registry_name": "acme-apps", "available": False, "suggestions": ["acme-apps-1"]}))
    result = run(["registries", "name-check", "acme-apps"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["suggestions"] == ["acme-apps-1"]
