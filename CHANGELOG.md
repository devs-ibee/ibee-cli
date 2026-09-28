# Changelog

## 0.4.0

Requires `ibee>=0.4.0,<0.5.0`.

### Added

- Global options `-o/--output table|json|yaml|id` (env `IBEE_OUTPUT`), `--yes/-y`
  (env `IBEE_ASSUME_YES`) and `--check-billing` (env `IBEE_CHECK_BILLING`). `--json`
  is now an alias for `-o json`. YAML output uses PyYAML when installed and a
  built-in emitter otherwise (no new dependency).
- `IBEE_ENDPOINT` environment variable (after `--base-url` and `IBEE_BASE_URL`).
- `ibee ops wait OPERATION_ID [--timeout S] [--poll-interval S]`.
- `--timeout` and `--poll-interval` on every asynchronous VM verb and on
  `ops get --wait` (timeout 1-7200 s, default 1200; interval 1-60 s, default 5).
- `--idempotency-key` on VM and GPU VM create, delete, start, stop, reboot,
  access-update, resize, resize-plan, resize-root-disk, volume-attach and
  volume-detach, and on Block Storage create, attach, detach, resize and delete.
  Block Storage delete now sends its key as the `idempotency_key` query
  parameter.
- `--check-billing` billing preflight on every billable create (VMs, GPU VMs,
  Block Storage volumes, buckets, S3 credentials, NAT gateways, Reserved IPs,
  load balancers, CDN distributions and custom domains, secret stores and
  secrets). Without the flag creates still send exactly one request.
- `ibee billing eligibility --operation OP --require`, with table output that
  explains a denial in the portal's words.
- `--limit`, `--offset`, `--search`, `--sort-by`, `--sort-direction` on
  `vms list` and `gpus list`; `--limit` and `--offset` on `firewalls list`.
- Exit code 3 for a `--wait` that times out while the operation is still running.

### Changed

- `vms list`, `gpus list` and `firewalls list` now return every item (all pages)
  instead of the first 10. `firewalls list` now uses the Python SDK.
- Direct gateway requests follow the SDK retry policy: reads, and writes that
  carry an idempotency key on a route that honours it, are retried on 429, 502,
  503 and 504 and on network errors (honouring `Retry-After`, at most 30 s).
  Unkeyed writes and 408, 409 and 500 responses are never retried.
- API errors are the SDK's typed errors (`CliApiError` is now an alias of
  `ibee.core.api_error.ApiError`) and are reported with specific messages: the
  portal's billing explanation and top-up guidance for 402, the missing scope
  for 403 `insufficient_scope`, field errors for 422, and request IDs for 5xx.
  The 404 message no longer mentions routes rolling out.
- A token whose environment does not match the endpoint (`ibee_dev_key_` with
  `api.ibee.ai`, `ibee_prod_key_` with `api.ibee.co.in`) exits 2 before any request.
- An invalid `IBEE_ENV` value exits 2 (it previously fell back to production).
  An unsafe `--base-url` (plain `http://` other than localhost, credentials,
  query or fragment) exits 2.
- Client-side validation errors (for example an invalid idempotency key or wait
  bounds) exit 2; billable create bodies over 64 KiB are rejected before sending.
- `--wait` now waits up to 1200 s (was 300 s), polls every 5 s (was 3 s),
  tolerates two consecutive transient poll failures, and exits 1 when an
  operation ends `failed`, `cancelled` or `timed_out` (only `failed` did before)
  and 3 when the wait times out (it exited 0 before). `ops get --wait` follows the
  same rules.
- Generated idempotency keys use the portal format
  (`cli-<action>-<id>-<hash>-<random>`), sanitised to letters, digits, `-` and `_`.
- With `-o json|yaml|id`, `--wait` prints only the final operation on stdout.
