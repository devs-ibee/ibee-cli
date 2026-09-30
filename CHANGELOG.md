# Changelog

## 0.4.2

- Every billing preflight flag (`--check-billing`, `IBEE_CHECK_BILLING` and the
  per-command `--billing-check/--no-billing-check`) is accepted as a no-op. The API
  decides admission for every create; the CLI never applies its own billing veto
  or cost estimate and still sends exactly one request.
- `billing eligibility` reports a denial as data (`allowed=false`, exit 0); only
  `--require` turns a denial into a nonzero exit.
- Typed upstream errors are preserved as raised, with the reason, SKU and
  admission context of a billing denial.
- Denials print no invented currency, minimum top-up amount or "add credits"
  guidance; top-up guidance appears only when upstream lists `billing_topup` in
  `allowed_operations`.
- Require Python SDK 0.4.2; publish that dependency before this CLI release.

## 0.4.1

- VM preflight checks Billing account status without a client-calculated price or
  a SKU-only monthly-price probe. Selected-term pricing and affordability are
  decided by upstream services when create is submitted.
- Preserve upstream denials, the selected catalog term, and one create attempt.
- Require Python SDK 0.4.1; publish that dependency before this CLI release.

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
  (not yet part of the published API contract; behaviour may change). They need the
  backend release that provides these operations: available on the development
  environment today; production returns 404/405 until that release.
- Validation errors print their details (for example a resize precheck's decision,
  reasons and warnings), and a 409 resize conflict prints its decision and reasons.
- Networking follows the portal (see "Networking rules the CLI applies" in the
  README):
  - `vpcs sites --available-only`; `vpcs create --nat-billing-catalog[-file]`
    (alias `--nat-billing-catalog-json`) and `--check-site/--no-check-site`;
    `vpcs delete --delete-nat-gateway [--nat-ip-action reserve|release]
    [--nat-billing-catalog[-file]] --check-state/--no-check-state`.
  - `vpcs subnets create --check-state/--no-check-state`; `vpcs nodes attach
    --private-ip` and `--check-state/--no-check-state`.
  - `vpcs nat create --billing-catalog[-file] --preflight --check-state`;
    `vpcs nat delete --ip-action reserve|release --billing-catalog[-file] --wait
    --check-state`; new `vpcs nat replace-ip`.
  - `vpcs forwarding create|update --target vm|vip --target-vm-id` and
    `--check-state`; new `vpcs forwarding enable` and `disable`.
  - New `vpcs virtual-ips list|get|create|delete|attach-ip|detach-ip`.
  - `reserved-ips reserve --billing-catalog[-file] --check-billing`; `attach
    --detach-from-service`; `--check-state/--no-check-state` on attach, move,
    detach and release; new `reserved-ips convert` and `reserved-ips
    attach-virtual-ip` (hidden alias `attach-vip`).
  - `firewalls list --summary --all`; `firewalls rules create|update --port 22|8000-8080
    --source CIDR[,CIDR]` (`--remote-target` still works); `--check-state` on rule
    update and delete; `firewalls attachments list --limit --skip`.
  - Load balancers: `--backend TYPE:TARGET:PORT[:WEIGHT][:tls]`, `--algorithm`,
    `--timeout-ms`, `--retries`, `--per-retry-timeout-ms`, `--retry-on`,
    `--proxy-protocol`, `--policy`, `--health-check-type|-path|-interval-ms|-timeout-ms`,
    `--healthy-threshold`, `--unhealthy-threshold`, `--health-check`, `--logs` on
    create and update; `--sticky-header`, `--rule PRIORITY:PATH_PREFIX[:HEADER=VALUE]`
    on L7; `update-l7 --clear-custom-domain`; `--check-billing` on create; `list` and
    `get --include-deleted`.
  - Virtual IPs, `nat replace-ip`, `reserved-ips convert` and `attach-virtual-ip`,
    `firewalls list --summary`, `--include-deleted`, `--private-ip`, the forwarding
    target options and the load-balancer policy, health-check and logs options are
    not yet part of the published API contract; behaviour may change.
- Warnings from the SDK (for example a NAT gateway created without a billing
  catalog) are printed as `Warning: ...` on stderr. Some API errors add a hint (for
  example how to empty a VPC before deleting it), and a Reserved IP attach to a VM
  outside a VPC points at `reserved-ips convert`.
- Storage follows the portal (see "Storage rules the CLI applies" in the README):
  - New `block-storage attach-vm VOLUME_ID VM_ID` and `block-storage detach-vm
    VOLUME_ID [VM_ID]` (the portal's attach and detach: the volume's SKU is sent,
    the cloud or GPU endpoint is picked from the volume, `--wait` polls every 2 s
    for up to 120 s).
  - `block-storage list --site-id --vm-type --limit --offset --all` (and `-o table`);
    `create --vm-type --delete-on-termination/--keep-on-termination
    --check-site/--no-check-site`; `operations --limit`; `--check-state/--no-check-state`
    on `delete`, `resize` and `attach`.
  - `buckets list --all`; `buckets create --retention-mode --retention-days
    --retention-years --bucket-lock/--no-bucket-lock --preflight-billing`; `buckets
    update --yes`; `buckets delete --skip-preflight --check-state/--no-check-state`.
  - `buckets credentials delete` (permanent delete; `revoke` does the same);
    `buckets credentials create --preflight-billing`; `credentials list -o table`.
  - `cdn cache-policies` and `cdn metrics DISTRIBUTION_ID --range 24h|7d|30d` (not
    yet part of the published API contract; behaviour may change); `cdn create
    --check-origin/--no-check-origin --preflight-billing`; `cdn domains create
    --preflight-billing`; `cdn domains verify --wait --timeout --poll-interval`;
    `cdn purge --yes`; `cdn list -o table`.
- Secret Store (every command applies the Python SDK's Secret Store rules first; see
  "Secret Store rules the CLI applies" in the README):
  - `secrets stores list --include-archived/--active-only --page --limit --all`
    (archived stores are listed by default, 100 per page, as in the portal).
  - `secrets stores create --billing-check/--no-billing-check --if-exists
    error|reuse`; `secrets create --billing-check/--no-billing-check`.
  - `secrets stores unarchive --yes`, `secrets identities disable --yes` and
    `secrets identities enable --yes` (they ask only on a terminal).
  - `--check-state/--no-check-state` on `secrets stores archive`, `unarchive` and
    `delete-permanent`, `secrets delete` and `delete-permanent`, `secrets rollback`,
    `secrets identities rotate-secret-id` and `secrets identities scopes create`.
  - `secrets list --query/-q --page --limit --all`.
  - `secrets identities scopes create --allow-version-read/--deny-version-read`.
  - `-o table secrets versions` (newest first, with the portal's version state).
  - Clear messages for Secret Store errors: lifecycle denials, missing resources,
    archived or inactive stores, disabled identities, soft-deleted values,
    check-and-set conflicts and incomplete store deletions.

### Changed

- `vms list`, `gpus list` and `firewalls list` now return every item (all pages)
  instead of the first 10. `firewalls list` now uses the Python SDK.
- Direct gateway requests follow the SDK retry policy: reads, and writes that
  carry an idempotency key on a route that honours it, are retried on 429, 502,
  503 and 504 and on network errors (honouring `Retry-After`, at most 30 s).
  Unkeyed writes and 408, 409 and 500 responses are never retried.
- API errors are the SDK's typed errors (`CliApiError` is now a subclass of
  `ibee.core.api_error.ApiError` that still accepts the 0.3.0 `CliApiError(status_code, body)`
  form; catch `ApiError` to handle every API error) and are reported with specific messages: the
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
- `vpcs`, `reserved-ips`, `firewalls` and `load-balancers` commands now call the
  Python SDK instead of sending requests directly, so the portal's rules are checked
  before anything is sent (exit 2) and read-only pre-checks run first
  (`--no-check-state` skips them). Responses are the SDK models, printed as JSON
  (`-o table` now works for the list commands).
- `vpcs create` defaults to `--connectivity private` (was `public`, now deprecated),
  sends `auto_cidr=false` with `--cidr` (it always sent `true`), and no longer sends
  an empty description or `is_default=false`. `vpcs nodes attach` no longer defaults
  `--connectivity` to `private`: the API picks `nat` in a `nat_gateway` VPC and
  `private` otherwise.
- `vpcs nat create` in a VPC that already has a NAT gateway prints it instead of
  sending a create (use `--no-check-state` to send it). `vpcs nat delete` and
  `vpcs delete` check the dependencies first and name them in the confirmation.
- `reserved-ips reserve` no longer sends an empty label. With `--check-billing` it
  checks the RESERVED-IP SKU, and load-balancer creates check LOADBALA-STD.
- `firewalls create --default` is rejected (default groups are platform-managed and
  hidden from lists) and is no longer shown in help. Firewall rules default to the
  portal's source `0.0.0.0/0`, and icmp/any rules no longer accept ports.
- Load-balancer `https` and `tls_passthrough` creates send the managed TLS setting
  automatically (the API rejected them without it); custom certificates are refused.
  `load-balancers list` no longer sends `limit=100&skip=0` unless you pass them.
- Port and range checks use the portal's messages (exit 2) rather than the generic
  option-range errors.
- `block-storage`, `buckets` and `cdn` commands now call the Python SDK instead of
  sending requests directly, so the portal's rules are checked before anything is
  sent (exit 2). Paths are URL-encoded (bucket names were not).
- Block Storage: volume IDs must be 24 hexadecimal characters; `create` needs a
  3-255 character lower-case name and 10-10000 GB (was 1-10000), accepts `--class`
  `capacity|balanced|performance` only, and fills `--site-name` from the compute
  sites. `create` prints the result as JSON and `Volume ID is STATE.` on stderr.
  `delete` refuses an attached or busy volume before asking, and `--force` asks a
  second time. `detach` needs `--confirm-unmounted`, `--force` or `--vm-state
  stopped|suspended`, and `--node-name` and `--vm-type` default to the volume's
  attachment. `resize` refuses shrinking and, for an attached volume, needs
  `--vm-state stopped|suspended` or `--allow-online`.
- `buckets create`: `--region` is optional on api.ibee.ai (`in-south-1`) and
  api.ibee.co.in (`in-south-2`); names follow the portal rule (upper case is
  rejected); `--default-retention` no longer needs `--bucket-lock` (Object Lock is
  switched on automatically; `--no-bucket-lock` with a retention exits 2).
- `buckets update --private` asks for confirmation (the bucket's CDN distribution and
  public URL are removed). `buckets delete` refuses a bucket with Object Lock or with
  objects before asking, and the prompt no longer says "and all of its contents"
  (the API deletes empty buckets only). A 409 "not empty" and a 403 retention error
  print a hint.
- `buckets credentials create`: `--permission-type` accepts `admin_rw|admin_ro|
  object_rw|object_ro` only, `--bucket-scope` defaults to the portal rule (`specific`
  only for `object_*`), names are 1-100 characters, the request is never retried,
  and the S3 endpoint is printed. `revoke` asks "Permanently delete ...": the API
  deletes the credential rather than marking it revoked.
- `cdn purge` requires `--mode` (it defaulted to `all`), `--mode all` asks for
  confirmation, selectors accept comma-separated values, and a purge the CDN reports
  as failed (`success: false`) exits 1 instead of 0.
- `cdn create` refuses a private origin bucket; `--cache-policy`, `--origin-type` and
  `generate-url --disposition` accept the portal's values only. `cdn website get`
  prints "Website hosting is not configured (disabled)." (exit 0) when a distribution
  has no website configuration. Custom domains are lower-cased; `cdn domains create`
  prints the CNAME record to add; `cdn domains verify` explains a pending status.
  `cdn delete` and `cdn domains delete` confirmations describe the side effects.
- Secret Store:
  - `secrets stores create` and `secrets create` check SECRETMA-STD billing
    eligibility first, as the portal does (skipped with a warning when the token lacks
    `billing.read`; `--no-billing-check` turns it off).
  - `secrets stores list` includes archived stores and asks for 100 per page (it
    showed active stores only, 50 at most). `secrets list` asks for 100 per page and
    shows the secret name (the 0.3.0 table read a field that does not exist).
  - `secrets identities scopes create` now allows version reads by default, matching
    the portal and the API (it sent `allow_version_read=false`).
  - Secret names are trimmed and lower-cased before sending; names, values, version
    lists, `--cas`, identities and scope permission combinations are checked before
    any request (exit 2).
  - `secrets batch-create` accepts files of any size (split into requests of at most
    500 secrets and 64 KiB), checks every item, reports skipped and failed secrets and
    exits 1 when any failed.
  - `secrets undelete --versions` is optional (default: the current version).
  - `secrets rollback`, `secrets identities rotate-secret-id` and `secrets identities
    scopes create` read first and refuse what the portal does not offer (rolling back
    to the current or a destroyed version; rotating a Kubernetes or disabled identity;
    granting a store that is inactive or already granted).
  - `secrets stores archive` and `unarchive` leave an already archived or active store
    alone. Confirmation prompts use the portal's wording, and archive, unarchive,
    delete and several identity commands print their result with `-o json|yaml|id`.
  - A 403 "does not belong to workspace" from Secret Store is printed as not found.

- Review fixes (0.4.0):
  - JSON and YAML output of SDK results keeps the API's wire format: timestamps
    print as ISO 8601 (`2026-09-28T10:00:00Z`, not `2026-09-28 10:00:00+00:00`) and
    unset optional fields are omitted. `firewalls list` output now follows the SDK
    model rather than echoing the raw API response.
  - The built-in YAML emitter (used when PyYAML is not installed) quotes every string
    a YAML parser could read as another type (timestamps, times, dates, hex, `.inf`,
    values starting with `@` or ending in a space).
  - `ibee.context.Settings` accepts the 0.3.0 fifth positional `as_json` bool and the
    `as_json=` keyword again (mapped to `-o json`).
  - When `--wait` cannot poll an accepted operation (API or network error), the
    command prints the operation ID and `resume with: ibee ops wait OP_ID` before the
    error, instead of losing the operation.
  - `ops get -o table` prints a table (the default is still JSON).
  - `console create` hides the token-bearing `connect_url` unless `--show-url` or
    `--json` / `-o json|yaml|id` is given.
  - `vms|gpus delete --no-check-state` still asks whether to reserve or release the
    auto-assigned public IP (it now skips only the state rule).
  - `vms|gpus backup-policy update --no-check-state` needs the full schedule when it
    changes the schedule, because the API replaces the whole schedule.
  - `vms|gpus create --count`: more `--instance-name` values than `--count` exits 2;
    when a batch stops early, the VMs already accepted are listed with their
    operation IDs.
  - `vms|gpus access-update --ssh-key-secret-ref` needs `ssh_key_id` or `secret_name`
    (not both), as the SDK and API do.
  - `vms|gpus volume-attach` and `volume-detach --wait` poll every 2 s for up to 120 s
    by default, as the portal does.
  - `vpcs delete --delete-nat-gateway` exits 1 (not 2) with a retry hint when the NAT
    gateway was deleted but is still reconciling.
  - `vpcs virtual-ips attach-ip` checks that the Reserved IP is in the virtual IP's
    site, is unattached and is in `reserved` state before sending.
  - `firewalls list --summary --limit 100` checks for a next page instead of assuming
    one.
  - `reserved-ips convert` tells you to pass `--no-billing-check` when the token lacks
    `billing.read`.
  - `load-balancers create-l7 --protocol` still defaults to `http` (0.3.0); the help
    now says the portal defaults to `https`.
  - `buckets credentials create -o id` prints the full result, so the one-time secret
    is never dropped.
  - `cdn create` skips the origin-bucket check when the token cannot read the bucket
    (403), as for a 404 (the Python SDK now applies this rule itself).
  - `block-storage create --check-billing` validates the request before the billing
    preflight, and plan errors (400 size/SKU, 502 `ambiguous_block_storage_plan`)
    print guidance.
  - `block-storage detach-vm` and `detach` resolve the attachment before the
    confirmation prompt (the prompt names the VM or node; an unattached volume exits
    2 without a prompt).
  - `secrets undelete` without `--versions` asks for `--versions` when the token cannot
    read the secret's versions.
  - `secrets delete-permanent`, `destroy-versions`, `identities delete` and
    `identities scopes delete` print the API result with `-o json|yaml|id`.
  - The Secret Store page hint names the next page and is not printed on the last page.
  - A server refusal that the SDK reports as a validation error (Reserved IP target
    without a VPC, firewall attach to a non-OVS/OVN network) exits 1 with its hint.

- Cross-client alignment (0.4.0):
  - Any other error the SDK raises about server state (an `IbeeError` that is not a
    validation error, for example `nat_gateway_deleting`) exits 1 with its hint;
    client-side validation still exits 2.
  - `vpcs delete` leaves the attached-node, NAT gateway and virtual-IP checks to the
    SDK, which reads the VPC once and checks virtual IPs before any NAT gateway is
    deleted (also with `--delete-nat-gateway`). The confirmation is asked before these
    reads; the refusal messages are unchanged. With `--delete-nat-gateway` the prompt
    is always "Delete NAT gateway(s) and then VPC ...?".
  - `vpcs virtual-ips delete` leaves the Reserved IP and port-forwarding-rule checks to
    the SDK (asked before the reads, same messages; `--check-state` makes an
    unreadable dependency an error, exit 1).
  - `cdn create --check-origin` uses the SDK's origin check (a 403 or 404 on the bucket
    read is left for the API; a private bucket exits 2).
  - `vms|gpus backups restore` accepts a backup run ID or a recovery point ID: the SDK
    always reads the run (also with `--no-check-state`), refuses a backup that has not
    succeeded, and sends the recovery point ID the run reports.
  - `vms|gpus backups delete` and `backups list-all` help states that they need the
    backend release (development today; production returns 404/405 until then).
  - Validation codes follow the SDK's canonical table: `invalid_nat_public_ip_action`
    (was `invalid_option`, `vpcs delete --nat-ip-action` without
    `--delete-nat-gateway`), `invalid_all` (was `invalid_paging`, `firewalls list
    --all` with `--limit`/`--offset`) and `reserved_ip_not_attached` (was
    `virtual_ip_no_reserved_ip`, `vpcs virtual-ips detach-ip`).
