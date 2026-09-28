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
- VM and GPU VM create follow the portal deploy flow: `--billing-term`,
  `--billing-catalog[-file]`, `--windows-license[-file]` (cloud), inline
  `--ssh-key`/`--ssh-key-file`, `--firewall-group-id`, `--vpc-id`, `--subnet-id`,
  `--network-connectivity`, `--reserved-public-ip-id`, `--requested-by`,
  `--preflight-billing`, and `--count 1-5` with `--instance-name` for batch creates
  (one request and idempotency key per VM; `--idempotency-key K` becomes `K-1..K-N`).
- VM delete asks whether to keep an auto-assigned public IP as a Reserved IP, with
  `--reserve-public-ip`, `--release-public-ip`, `--reserved-ip-label`,
  `--reserved-ip-billing-catalog[-file]` and `--preflight-billing`, and warns that
  attached data volumes are detached.
- `--check-state/--no-check-state` (default on) on start, stop, reboot, delete,
  access-update, resize, resize-plan, resize-root-disk, volume-attach,
  volume-detach, snapshot create/delete/restore, backup-policy update, backup
  create/delete/restore and `console create`.
- `--plan-id` on `resize-precheck`, `resize` and `resize-plan`; `--billing-term`,
  `--billing-catalog[-file]` and `--windows-license[-file]` on `resize` and
  `resize-plan`; `--billing-catalog[-file]` on `resize-root-disk` and
  `volume-attach`.
- `--ssh-key-file` on `access-update`; `--all` on `vms list` and `gpus list`.
- Recovery: `--billing-catalog[-file]` on `snapshots create`, `backup-policy enable`,
  `backup-policy update` and `backups create`; `--preflight-billing` on
  `snapshots create`; `--wait` on `snapshots create`, `snapshots restore`,
  `snapshots restore-status`, `backups create`, `backups restore` and
  `backups restore-status` (default timeout 1800 s); `--target-volume-name
  SRC=NAME`, `--target-billing-catalog[-file]`, and (snapshots) `--vpc-id`,
  `--subnet-id`, `--network-connectivity`, `--ssh-key-id` on restores;
  `--restorable-only` on `backups list`.
- New `ibee vms|gpus backups list-all` and `ibee vms|gpus backups delete RUN_ID`
  (not yet part of the published API contract; behaviour may change).
- Validation errors print their details (for example a resize precheck's decision,
  reasons and warnings), and a 409 resize conflict prints its decision and reasons.

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
- `vms create` and `gpus create` require `--site-id`, and no longer send the
  fixed defaults `--cpu 2 --ram-mb 4096` (cloud), `--cpu 8 --ram-mb 32768` (GPU)
  or `--os-distro ubuntu --os-type linux`: the shape and OS come from the plan and
  image. `gpus create` no longer requires `--gpu-model`. The plan's billing SKU and
  disk size are always sent, so creates no longer fail with 422.
- With `--check-billing`, VM creates ask the SDK to check the plan's SKU and
  estimated cost (it previously sent an eligibility check without a SKU).
- `vms|gpus delete` reads the VM first (skip with `--no-check-state`) and sends the
  public-IP choice the API requires for an auto-assigned IP; VM IDs must be 24
  hexadecimal characters.
- `volume-detach` requires `--confirm-unmounted` or `--force` before asking for
  confirmation. `console create` accepts cloud VMs only (`--vm-type gpu` exits 2).
- `backup-policy --frequency` accepts `daily` or `weekly` (not `hourly`);
  `--window-minutes` is 5-180, `--retention-days` 1-365,
  `--full-backup-interval-days` 1-30. `snapshots list`/`backups list` `--limit` is
  at most 200 and `events --limit` at most 500. `resize-root-disk --new-size-gb` is
  at most 10000. `resize-plan --cpu/--ram-mb` are optional with `--plan-id`.
- `bandwidth --month` is optional (default: the current UTC month).
- Restore commands check the target-mode rules before asking for confirmation.
