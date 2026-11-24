"""Contract 0.14: object commands and ``buckets objects upload``, billing refusal fields,
``load-balancers list --include-deleted`` and ``volume-attach --shared-access-confirmed``."""

from __future__ import annotations

import base64
import hashlib
import json

from _net_fixtures import WS, plain, run

S3 = "https://973318.blob.ibeestorage.com"
DATA = b"hello from the ibee cli\n"
SHA = base64.b64encode(hashlib.sha256(DATA).digest()).decode()
LINK = {"url": f"{S3}/b1/docs/a.txt?X-Amz-Signature=sig", "method": "PUT",
        "headers": {"Content-Length": str(len(DATA)), "x-amz-checksum-sha256": SHA},
        "expires_in": 900, "max_bytes": 80000000}
UPLOAD_LINK = "object-storage/buckets/b1/objects/docs/a.txt/upload-link"


def test_object_commands_use_the_contract_paths(gw) -> None:
    gw.on("POST", UPLOAD_LINK, LINK)
    gw.on("POST", "object-storage/buckets/b1/objects/docs/a.txt/download-link", {"url": f"{S3}/x", "expires_in": 120})
    gw.on("GET", "object-storage/buckets/b1/objects/docs/a.txt/metadata",
          {"name": "docs/a.txt", "bucket": "b1", "size_bytes": len(DATA), "checksum_sha256": SHA})
    assert run(["buckets", "objects", "upload-link", "b1", "docs/a.txt", "--size-bytes", str(len(DATA)),
                "--checksum-sha256", SHA]).exit_code == 0
    result = run(["buckets", "objects", "download-link", "b1", "docs/a.txt", "--expires", "120", "--disposition", "attachment"])
    assert json.loads(result.output)["expires_in"] == 120
    result = run(["buckets", "objects", "metadata", "b1", "docs/a.txt"])
    assert json.loads(result.output)["checksum_sha256"] == SHA
    assert [c.json for c in gw.calls[:2]] == [{"size_bytes": len(DATA), "checksum_sha256": SHA},
                                              {"expires": 120, "disposition": "attachment"}]


def test_upload_computes_size_and_sha256_then_puts_the_file(gw, tmp_path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(DATA)
    gw.on("POST", UPLOAD_LINK, LINK)
    gw.on("PUT", "/b1/docs/a.txt", (200, b"", {"ETag": '"abc"'}))
    result = run(["buckets", "objects", "upload", "b1", "docs/a.txt", str(path), "--content-type", "text/plain"])
    assert result.exit_code == 0, result.output
    mint, put = gw.calls
    assert mint.json == {"size_bytes": len(DATA), "checksum_sha256": SHA, "content_type": "text/plain"}
    assert mint.params == {"workspace_id": [WS]}
    assert (put.method, put.json) == ("PUT", DATA)
    assert put.headers["x-amz-checksum-sha256"] == SHA
    assert "authorization" not in put.headers
    assert json.loads(result.output) == {"bucket_name": "b1", "object_path": "docs/a.txt", "size_bytes": len(DATA),
                                         "checksum_sha256": SHA, "etag": '"abc"'}


def test_upload_reads_stdin(gw) -> None:
    gw.on("POST", UPLOAD_LINK, LINK)
    gw.on("PUT", "/b1/docs/a.txt", (200, b""))
    result = run(["buckets", "objects", "upload", "b1", "docs/a.txt", "-"], input=DATA)
    assert result.exit_code == 0, result.output
    assert gw.calls[1].json == DATA


def test_unreadable_file_sends_nothing(gw, tmp_path) -> None:
    result = run(["buckets", "objects", "upload", "b1", "a.txt", str(tmp_path / "missing.bin")])
    assert result.exit_code == 2
    assert "cannot read" in plain(result)
    assert gw.calls == []


def test_refused_upload_exits_with_the_status(gw, tmp_path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(DATA)
    gw.on("POST", UPLOAD_LINK, LINK)
    gw.on("PUT", "/b1/docs/a.txt", (403, b"<Error><Code>SignatureDoesNotMatch</Code></Error>"))
    result = run(["buckets", "objects", "upload", "b1", "docs/a.txt", str(path)])
    assert result.exit_code == 13
    assert "SignatureDoesNotMatch" in result.output


def test_initial_topup_refusal_shows_the_minimum(gw) -> None:
    body = {"error": "billing_denied", "billing_reason": "initial_topup_required",
            "message": "Add at least Rs 500 to your wallet.", "required_topup_minor": 50000, "billing_currency": "INR"}
    gw.on("POST", "networking/reserved-ips", (402, body))
    result = run(["reserved-ips", "reserve", "--site-id", "VGA"])
    assert result.exit_code == 12
    text = plain(result)
    assert "reason: initial_topup_required" in text
    assert "required_topup_minor: 50000" in text and "billing_currency: INR" in text
    result = run(["-o", "json", "reserved-ips", "reserve", "--site-id", "VGA"])
    error = json.loads(result.output)["error"]
    assert (error["required_topup_minor"], error["billing_currency"]) == (50000, "INR")


def test_load_balancers_list_include_deleted(gw) -> None:
    gw.on("GET", "networking/load-balancers", {"items": [], "total": 0, "page": 1, "page_size": 50})
    assert run(["load-balancers", "list", "--include-deleted"]).exit_code == 0
    assert gw.calls[0].params["include_deleted"] == ["true"]


def test_volume_attach_shared_access_confirmed(gw) -> None:
    gw.on("POST", "compute/cloud-vms/vm-1/actions/attach-volume",
          (202, {"operation_id": "op-1", "vm_id": "vm-1", "status": "accepted", "submitted_at": "2026-01-01T00:00:00Z"}))
    result = run(["vms", "volume-attach", "vm-1", "--volume-id", "vol-1", "--billing-catalog", '{"sku_code": "BS-100"}',
                  "--mode", "multi-writer", "--shared-access-confirmed"])
    assert result.exit_code == 0, result.output
    assert gw.calls[0].json["mode"] == "multi-writer" and gw.calls[0].json["shared_access_confirmed"] is True
