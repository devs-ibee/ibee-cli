# IBEE Solutions CLI

Official command-line interface for the IBEE Solutions cloud platform. Built on the [`ibee` Python SDK](https://github.com/devs-ibee/ibee-python).

## Installation

```bash
pip install ibee-cli
```

Requires Python 3.10+.

## Authentication

Create an API token in the portal under **Settings > API Tokens**, then:

```bash
export IBEE_TOKEN="ibee_prod_key_xxxxxxxxxxxx"
export IBEE_WORKSPACE_ID="710995"
```

Both can also be passed per command with `--token` and `--workspace`.

## Usage

```bash
# Object storage (region defaults to in-south-1 on api.ibee.ai, in-south-2 on api.ibee.co.in)
ibee buckets list
ibee buckets list --all
ibee buckets create my-bucket
ibee buckets create locked-bucket --retention-mode GOVERNANCE --retention-days 30
ibee buckets get my-bucket
ibee buckets update my-bucket --public
ibee buckets update my-bucket --private --yes   # removes its CDN distribution
ibee buckets delete my-bucket --yes             # empty buckets only

# S3-compatible credentials (secret_access_key is shown only at creation)
ibee buckets credentials list
ibee buckets credentials create --name deploy                 # admin_rw, all buckets
ibee buckets credentials create \
  --name backups --permission-type object_rw \
  --bucket-scope specific --allowed-bucket my-bucket
ibee buckets credentials get ACCESS_KEY_ID
ibee buckets credentials delete ACCESS_KEY_ID --yes           # permanent (alias: revoke)

# Secret Store — stores (archived stores are listed too; --active-only hides them)
ibee secrets stores list [--active-only] [--page N --limit N | --all]
ibee secrets stores create production-secrets --description "prod" [--if-exists reuse]
ibee secrets stores get STORE_ID
ibee secrets stores update STORE_ID --name renamed
ibee secrets stores archive STORE_ID --yes
ibee secrets stores unarchive STORE_ID [--yes]
ibee secrets stores delete-permanent STORE_ID --yes

# Secret Store — secrets
ibee secrets list --store-id STORE_ID [--query db] [--all]
ibee secrets create --store-id STORE_ID --name db-url --value '{"url":"postgres://..."}'
ibee secrets batch-create --store-id STORE_ID --file secrets.json   # any size; split into 500-item / 64 KiB requests
ibee secrets get SECRET_ID           # metadata
ibee secrets value SECRET_ID         # current value
ibee secrets set-value SECRET_ID --value '{"url":"postgres://new"}' [--cas 3]
ibee secrets patch-value SECRET_ID --value '{"old_key":null,"new_key":"x"}'
ibee -o table secrets versions SECRET_ID
ibee secrets rollback SECRET_ID --version 2
ibee secrets delete SECRET_ID --yes
ibee secrets undelete SECRET_ID      # restores the current version

# Secret Store — application identities and their store access
ibee secrets identities create --store-id STORE_ID --name worker --auth-method approle
ibee secrets identities access IDENTITY_ID --show-sensitive
ibee secrets identities rotate-secret-id IDENTITY_ID --show-sensitive
ibee secrets identities scopes create IDENTITY_ID --store-id OTHER_STORE_ID \
  --access-mode read_write --allow-rollback

# Compute catalog (discover placement / plans / images for `create`)
ibee compute sites
ibee compute plans --vm-type cloud
ibee compute images --vm-type gpu

# Billing admission (SKU must come from an IBEE product catalog)
ibee billing eligibility --sku-code PLAN_SKU
ibee -o table billing eligibility --sku-code PLAN_SKU --require

# Standalone Block Storage
ibee block-storage list --all
ibee block-storage create data --size-gb 100 --site-id SITE_ID
ibee block-storage create gpu-data --size-gb 500 --site-id SITE_ID --vm-type gpu
ibee block-storage get VOLUME_ID
ibee block-storage operations VOLUME_ID --limit 50
ibee block-storage attach-vm VOLUME_ID VM_ID --wait
ibee block-storage detach-vm VOLUME_ID --confirm-unmounted --wait
ibee block-storage resize VOLUME_ID --new-size-gb 200 --vm-state stopped
ibee block-storage delete VOLUME_ID --yes

# CDN
ibee cdn list
ibee cdn create assets --origin-id BUCKET_NAME           # the bucket must be public
ibee cdn get DISTRIBUTION_ID
ibee cdn update DISTRIBUTION_ID --cache-policy media
ibee cdn metrics DISTRIBUTION_ID --range 7d
ibee cdn website set DISTRIBUTION_ID --index-document index.html
ibee cdn domains create DISTRIBUTION_ID static.example.com
ibee cdn domains verify DISTRIBUTION_ID static.example.com --wait
ibee cdn purge DISTRIBUTION_ID --mode url --path /index.html --path /app.js
ibee cdn purge DISTRIBUTION_ID --mode all --yes
ibee cdn generate-url BUCKET_NAME path/to/object.jpg --expires-in 3600
ibee cdn delete DISTRIBUTION_ID --yes

# Cloud VMs
ibee vms list
ibee vms get VM_ID
ibee vms create web-01 --site-id SITE_ID --plan-id PLAN_ID --template-id IMAGE_ID \
  --ssh-key-file ~/.ssh/id_ed25519.pub --wait
ibee vms create web --count 3 --site-id SITE_ID --plan-id PLAN_ID --template-id IMAGE_ID \
  --billing-term MONTHLY --firewall-group-id FIREWALL_GROUP_ID \
  --vpc-id VPC_ID --subnet-id SUBNET_ID --network-connectivity nat
ibee vms start VM_ID
ibee vms stop VM_ID
ibee vms reboot VM_ID
ibee vms metrics VM_ID
ibee vms metrics-timeseries VM_ID --range 24h
ibee vms bandwidth VM_ID              # current UTC month; --month 2026-08 for another
ibee vms events VM_ID --limit 50

# Access credentials. Passwords are never accepted as command arguments.
printf '%s\n' "$NEW_VM_PASSWORD" | ibee vms access-update VM_ID --password-stdin --wait
ibee vms access-update VM_ID --prompt-password --enable-password-auth --wait
ibee vms access-update VM_ID --ssh-key-mode add --ssh-key-file ~/.ssh/id_ed25519.pub --wait

# Resize and persistent block volumes
ibee vms resize-precheck VM_ID --plan-id PLAN_ID
ibee vms resize VM_ID --plan-id PLAN_ID --billing-term HOURLY --wait
ibee vms resize VM_ID --cpu 4 --ram-mb 8192 --disk-gb 80 --wait
ibee vms resize-plan VM_ID --cpu 8 --ram-mb 16384 --confirm-downgrade --wait
ibee vms resize-root-disk VM_ID --new-size-gb 120 --wait
ibee vms volume-attach VM_ID VOLUME_ID --mode single-writer --wait
ibee vms mount-guidance-acknowledge VM_ID VOLUME_ID
ibee vms volume-detach VM_ID VOLUME_ID --confirm-unmounted --yes --wait

# Snapshots and restores
ibee vms snapshots list VM_ID
ibee vms snapshots create VM_ID before-upgrade --mode all_attached \
  --billing-catalog-file snapshot-sku.json --wait
ibee vms snapshots get SNAPSHOT_SET_ID
ibee vms snapshots restore VM_ID SNAPSHOT_SET_ID \
  --target-mode new_vm --target-plan-id PLAN_ID --target-vm-name restored-web --yes --wait
ibee vms snapshots restore-status RESTORE_ID --wait
ibee vms snapshots delete SNAPSHOT_SET_ID --yes

# Automated backup policy and manual backup runs
ibee vms backup-policy get VM_ID
ibee vms backup-policy enable VM_ID --billing-catalog-file backup-sku.json \
  --frequency daily --timezone Asia/Kolkata --hour 2 --retention-days 30
ibee vms backup-policy update VM_ID --frequency weekly --day-of-week 6
ibee vms backup-policy reschedule VM_ID --next-run-at 2026-08-10T02:00:00Z
ibee vms backup-policy disable VM_ID
ibee vms backups list VM_ID --restorable-only
ibee vms backups list-all --status succeeded
ibee vms backups create VM_ID --billing-catalog-file backup-sku.json --reason before-release --wait
ibee vms backups get BACKUP_RUN_ID
ibee vms backups restore VM_ID RECOVERY_POINT_ID --target-mode replace --yes
ibee vms backups restore-status RESTORE_ID --wait
ibee vms backups delete BACKUP_RUN_ID --yes
ibee vms delete VM_ID --wait                     # asks: keep the public IP as a Reserved IP?
ibee vms delete VM_ID --yes --release-public-ip --wait

# GPU VMs expose the same lifecycle, snapshot, backup, metrics, and volume commands
ibee gpus list
ibee gpus create train-01 --site-id SITE_ID --plan-id PLAN_ID --template-id IMAGE_ID \
  --ssh-key-file ~/.ssh/id_ed25519.pub --wait
ibee gpus start VM_ID
ibee gpus stop VM_ID
ibee gpus reboot VM_ID
ibee gpus metrics VM_ID
ibee gpus resize-precheck VM_ID --cpu 16 --ram-mb 65536
ibee gpus snapshots create VM_ID before-training --mode root_only
ibee gpus backup-policy enable VM_ID --billing-catalog-file backup-sku.json \
  --frequency weekly --day-of-week 6 --retention-days 14
ibee gpus delete VM_ID --yes

# Short-lived graphical console sessions (cloud VMs that are running)
ibee console create VM_ID --vm-type cloud
ibee console get SESSION_ID
ibee console close SESSION_ID --yes

# Async operations — poll a create/delete/power action to completion
ibee ops get OPERATION_ID
ibee ops wait OPERATION_ID --timeout 1800 --poll-interval 10

# VPCs (private by default; --connectivity nat_gateway adds a managed NAT gateway)
ibee vpcs sites --available-only
ibee vpcs list
ibee vpcs create production --site-id SITE_ID --cidr 10.44.0.0/22
ibee vpcs create egress --site-id SITE_ID --connectivity nat_gateway \
  --nat-billing-catalog-file nat-gateway-sku.json
ibee vpcs get VPC_ID
ibee vpcs update VPC_ID --name production-apps
ibee vpcs delete VPC_ID --yes
ibee vpcs delete VPC_ID --delete-nat-gateway --nat-ip-action release --yes

# Subnets and VM attachments
ibee vpcs subnets list VPC_ID
ibee vpcs subnets create VPC_ID services --cidr 10.44.1.0/24
ibee vpcs subnets update VPC_ID SUBNET_ID --dns 1.1.1.1 --dns 8.8.8.8
ibee vpcs nodes list VPC_ID
ibee vpcs nodes attach VPC_ID VM_ID --subnet-id SUBNET_ID --private-ip 10.44.1.20
ibee vpcs nodes detach VPC_ID VM_ID --yes

# NAT and port forwarding
ibee vpcs nat list VPC_ID
ibee vpcs nat create VPC_ID --reserved-ip-id RESERVED_IP_ID --billing-catalog-file nat-gateway-sku.json
ibee vpcs nat replace-ip VPC_ID NAT_GATEWAY_ID --reserved-ip-id RESERVED_IP_ID
ibee vpcs nat delete VPC_ID NAT_GATEWAY_ID --ip-action release --wait --yes
ibee vpcs forwarding list VPC_ID NAT_GATEWAY_ID
ibee vpcs forwarding create VPC_ID NAT_GATEWAY_ID ssh \
  --external-port 2222 --internal-ip 10.44.0.10 --internal-port 22
ibee vpcs forwarding create VPC_ID NAT_GATEWAY_ID web \
  --external-port 443 --internal-ip 10.44.0.50 --internal-port 443 --target vip
ibee vpcs forwarding update VPC_ID NAT_GATEWAY_ID RULE_ID --external-port 2200
ibee vpcs forwarding disable VPC_ID NAT_GATEWAY_ID RULE_ID

# Virtual IPs (MetalLB) in a VPC subnet
ibee vpcs virtual-ips list VPC_ID
ibee vpcs virtual-ips create VPC_ID --subnet-id SUBNET_ID --private-ip 10.44.0.50 \
  --announcer-vm-id VM_1 --announcer-vm-id VM_2
ibee vpcs virtual-ips attach-ip VPC_ID VIRTUAL_IP_ID --reserved-ip-id RESERVED_IP_ID
ibee vpcs virtual-ips detach-ip VPC_ID VIRTUAL_IP_ID --yes
ibee vpcs virtual-ips delete VPC_ID VIRTUAL_IP_ID --yes

# Reserved public IPs
ibee reserved-ips list --site-id SITE_ID
ibee reserved-ips reserve --site-id SITE_ID --label edge --billing-catalog-file reserved-ip-sku.json
ibee reserved-ips convert --vm-id VM_ID --site-id SITE_ID --label web
ibee reserved-ips update RESERVED_IP_ID --reverse-dns app.example.com
ibee reserved-ips attach RESERVED_IP_ID VM_ID --vpc-id VPC_ID --subnet-id SUBNET_ID
ibee reserved-ips attach-virtual-ip RESERVED_IP_ID --virtual-ip-id VIRTUAL_IP_ID
ibee reserved-ips detach RESERVED_IP_ID
ibee reserved-ips move RESERVED_IP_ID NEW_VM_ID --vpc-id VPC_ID --subnet-id SUBNET_ID
ibee reserved-ips release RESERVED_IP_ID --yes

# Firewall groups, rules, and attachments
ibee firewalls list --summary
ibee firewalls create web --description "Web ingress"
ibee firewalls rules create FIREWALL_GROUP_ID --protocol tcp --port 443 --source 0.0.0.0/0
ibee firewalls rules create FIREWALL_GROUP_ID --protocol tcp --port 8000-8080 --source 10.0.0.0/8,192.168.1.10
ibee firewalls rules update FIREWALL_GROUP_ID RULE_ID --disabled
ibee firewalls attachments list FIREWALL_GROUP_ID
ibee firewalls attachments attach FIREWALL_GROUP_ID VM_ID
ibee firewalls attachments detach FIREWALL_GROUP_ID VM_ID --yes

# L4 and L7 load balancers
ibee load-balancers list --layer l7
ibee load-balancers create-l4 tcp-edge --backend ip:10.44.0.10:443 --health-check-type tcp
ibee load-balancers create-l7 web \
  --protocol https \
  --backend service:api:8080 --backend service:api-canary:8080:10 \
  --algorithm least_request --timeout-ms 30000 --retries 3 \
  --health-check-type http --health-check-path /health \
  --rule 1:/ --rule 2:/api:X-Env=beta \
  --custom-domain app.example.com
ibee load-balancers update-l7 LOAD_BALANCER_ID --name public-web --clear-custom-domain
ibee load-balancers status LOAD_BALANCER_ID
ibee load-balancers delete LOAD_BALANCER_ID --yes
```

Create/delete/power, access, resize, and volume actions are asynchronous; add
`--wait` to block until the operation finishes (default timeout 1200 s, polling
every 5 s; change with `--timeout` and `--poll-interval`), or wait later with
`ibee ops wait OPERATION_ID`.

Snapshot and backup restores can replace a VM, create a new VM, or restore one
volume. They require `--yes` (or an interactive confirmation). Run the command
with `--help` to see placement, plan, GPU, bandwidth, and selected-volume restore
options. Snapshot deletion and volume detachment use the same confirmation rule.

`access-update` never accepts a password value in the command line, so passwords
do not appear in shell history or process listings. Use `--prompt-password` for
an interactive hidden prompt, or pipe one line to `--password-stdin`.

Bucket and VM placement are automatic when `--site-id` is omitted. Use
`ibee compute sites` and pass `--site-id` only when placement must be pinned.

All product writes reach the upstream API for fresh Billing and lifecycle decisions.
The CLI never queries billing eligibility or calculates an admission estimate automatically.
Legacy `--check-billing`, `--preflight-billing`, NAT `--preflight`,
`--billing-check/--no-billing-check`, and `IBEE_CHECK_BILLING` are deprecated no-ops.
Structural, workspace, state, scope and destructive-action checks remain in place.
`ibee billing eligibility` is an explicit diagnostic query: `allowed: false` is data (exit 0);
the explicit `--require` option retains exit 1 on denial.
It supports `REVOKE_CREDENTIAL` and `SECURITY_RECOVERY`.
Upstream errors retain their classification; suspension messages do not infer who suspended an organization.

Load balancers take backends, routing, policy, health checks and L7 rules either
as flags (`--backend TYPE:TARGET:PORT[:WEIGHT][:tls]`, `--rule
PRIORITY:PATH_PREFIX[:HEADER=VALUE]`, ...) or as JSON (`--backends`, `--routing`,
`--policy`, `--health-check`, `--rules`) matching the
[API reference](https://ibee.ai/docs/api-reference).

## VM rules the CLI applies

The VM commands follow the IBEE portal. The Python SDK checks these rules before
anything is sent, and a broken rule exits 2:

- **Create** needs `--site-id`. The plan and image are looked up for that site:
  the plan must be selectable and priced, and CPU, RAM, disk, GPU model and count
  and OS come from the plan and image (values you pass must match them). The plan's
  billing SKU is sent for `--billing-term` (cloud VMs default to `HOURLY`; GPU VMs
  send the plan SKU unchanged unless you pass a term). Hostnames use letters,
  digits and `-`. `--count 2..5` creates `NAME-1..NAME-N` (or `--instance-name`
  overrides), one request and idempotency key per VM, stopping at the first error.
  Inline `--ssh-key`/`--ssh-key-file` public keys are recommended: saved
  `--ssh-key-id` keys resolve only for portal users. At most one
  `--firewall-group-id`. `--network-connectivity` needs `--vpc-id` and
  `--subnet-id`: `nat` needs a NAT Gateway VPC, and `public_ip` on a private VPC
  needs an unattached `--reserved-public-ip-id` in the same site (one VM only).
- **Windows images** need `--windows-license` (the Windows licence SKU as JSON,
  priced per vCPU). The public API cannot list that SKU yet.
- **Delete** asks whether to keep an auto-assigned public IP as a Reserved IP
  (default: release; `--yes` releases). Reserving needs
  `--reserved-ip-billing-catalog`, the Reserved IP SKU, which you can copy from
  the `billing_catalog` of a Reserved IP in the same site. Attached data volumes
  are detached and kept.
- **State checks** (on by default; `--no-check-state` skips them): start needs a
  stopped VM; stop, reboot, access-update and console need a running VM; resizes
  and volume attach/detach need running, stopped or error; delete is refused while
  the VM is being deleted or resized.
- **Resize** with `--plan-id` runs the precheck first and resizes only when it can
  run in place, sending the new plan's SKU (a Windows VM keeps its licence).
  `resize-plan` rejects an unchanged shape and needs `--confirm-downgrade` to shrink;
  `resize-root-disk` only grows (up to 10000 GB).
- **Access updates** are for Linux VMs; new passwords need 8 or more characters;
  disabling password login needs an SSH key left on the VM.
- **Volumes**: `volume-attach` reads the volume (it must be unattached, idle and in
  the VM's site) and sends its Block Storage SKU; `volume-detach` needs
  `--confirm-unmounted` or `--force`.
- **Snapshots and backups** are billed: `snapshots create`, `backup-policy enable`
  and `backups create` need `--billing-catalog` (or `--billing-catalog-file`), the
  `snapshot_storage` SKU (code `SNAPSHOT-STD`) or `backup_storage` SKU (code
  `BACKUP-STD`). The public API cannot list these SKUs yet; copy `billing_catalog`
  from an existing snapshot set or backup run. Backup schedules are daily or weekly
  (`--day-of-week` 0 = Monday, required for weekly) in an IANA time zone.
- **Restores**: `--target-mode new_vm` resolves the plan (default: the VM's plan,
  which must fit the captured root disk), its SKU and default names;
  `volume_only` needs `--selected-volume-id`. `--wait` on snapshot, backup and
  restore commands polls every 5 s for up to 30 minutes by default.
- `backups list-all` and `backups delete` need the backend release that provides
  them: available on the development environment (`--dev`) today; production
  returns 404/405 until then. They are not yet part of the published API contract;
  behaviour may change.
- `backups restore` accepts a backup run ID or a recovery point ID; the run is always
  read first and the recovery point ID it reports is sent.

## Networking rules the CLI applies

The networking commands call the Python SDK, which applies the portal's rules before
anything is sent; a broken rule exits 2. `--check-state/--no-check-state` (default
on) controls the read-only checks that need the current state.

- **VPC create** defaults to `--connectivity private` (the portal default; 0.3.0
  sent `public`, which is now deprecated and prints a warning). Names are 1-80
  characters. A custom `--cidr` must be aligned (the error suggests the aligned
  network), /22 to /28, and inside 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16;
  `--cidr` sends `auto_cidr=false`. The site is checked with `vpcs sites` first
  (`--no-check-site` skips it). A `nat_gateway` VPC should carry
  `--nat-billing-catalog` (the NAT-GATEWAY SKU); without it a warning says the
  gateway will not be metered.
- **VPC delete** is refused while nodes are attached, a NAT gateway exists (unless
  `--delete-nat-gateway`, which deletes it and waits up to 10 s first) or virtual
  IPs remain. The Python SDK runs these checks after the confirmation and before
  anything is deleted (virtual IPs are checked before the NAT gateway is deleted). If
  the NAT gateway is still reconciling when the wait ends, the command exits 1 with a
  retry hint.
- **Subnets** must lie inside the VPC CIDR, not overlap other subnets, be /29 or
  larger, and a VPC holds at most 10. `--cidr` and `--prefix-length` are exclusive.
- **Node attach** `--private-ip` must be a usable host of the subnet (not its
  network, broadcast or gateway address). `nat` needs a `nat_gateway` VPC with an
  available NAT gateway; `public_ip` is not allowed in a `nat_gateway` VPC and needs
  `--reserved-ip-id` in a private VPC. Without `--connectivity` the API picks `nat`
  in a `nat_gateway` VPC. This creates the network allocation only; the portal's
  VM-side attach and detach are not yet in the public API.
- **NAT gateways** exist only in `nat_gateway` VPCs (one per VPC; `nat create`
  prints the existing one instead of sending a create). A Reserved IP must be in
  the VPC's site, unattached and `reserved`. `--preflight` (or `--check-billing`)
  is a deprecated no-op; upstream decides admission. `nat delete` shows how many NAT VMs lose
  outbound internet and how many forwarding rules are deleted before it asks;
  `--ip-action reserve` of a platform-assigned IP needs `--billing-catalog` (the
  RESERVED-IP SKU); deletion is refused while a virtual IP holds a Reserved IP.
- **Port forwarding** uses single ports 1-65535 (no ranges) and tcp or udp; a
  protocol and external port pair may appear once per gateway. The gateway must be
  available; `--target vm` needs the private IP of a NAT-connected VM, `--target
  vip` the private IP of an available MetalLB virtual IP (announcers default to
  the virtual IP's).
- **Virtual IPs** need a usable host address of the subnet and, for MetalLB, 1-32
  NAT-connected announcer VMs in the same subnet. Delete is refused while a
  Reserved IP is attached or a forwarding rule targets the address.
- **Reserved IPs**: labels up to 120 characters; reverse DNS a valid hostname up to
  253 characters (`--reverse-dns ""` clears it); `release` is refused while
  attached; `attach` is for unattached IPs (`move` for an IP on another VM,
  `--detach-from-service` for one on a NAT gateway or virtual IP); an attach to a VM
  outside a VPC points at `reserved-ips convert`. `convert` runs the RESERVED-IP
  billing check first (`--no-billing-check` skips it; it needs `billing.read`).
- **Firewalls**: group names are unique in any case (1-120 characters) and
  customers cannot create default groups. Rules: tcp and udp need `--port` (22 or
  8000-8080), icmp and any take none; sources are IPv4 addresses or CIDRs (a bare
  IP becomes /32; default 0.0.0.0/0). System-managed rules cannot be changed.
  Attaching a group replaces the VM's current custom group; detaching restores the
  default group.
- **Load balancers**: names 1-128 characters; https and tls_passthrough get the
  managed certificate automatically; custom certificates are refused; sticky
  sessions and path rules are L7 only; `--custom-domain` needs https and a CNAME
  already pointing at IBEE. Policy and health-check options fill unspecified values
  with the portal defaults (timeout 30000 ms, 3 retries at 5000 ms; health check
  http `/health` every 10000 ms, timeout 2000 ms, thresholds 2 and 3).
- Billing catalogs (`--billing-catalog`, `--nat-billing-catalog`) cannot be looked
  up through the public API yet; copy them from your IBEE pricing, for example the
  `billing_catalog` of an existing Reserved IP or NAT gateway in the same site.
- Virtual IPs, `nat replace-ip`, `reserved-ips convert` and `attach-virtual-ip`,
  `firewalls list --summary`, `--include-deleted`, `--private-ip`, the forwarding
  `--target` options and the load-balancer policy, health-check and logs options
  are not yet part of the published API contract; behaviour may change.

## Storage rules the CLI applies

The Block Storage, Object Storage and CDN commands call the Python SDK, which applies
the portal's rules before anything is sent; a broken rule exits 2. Where a command asks
for confirmation, the read-only checks run first (`--no-check-state` skips them).

- **Block Storage volumes**: names are 3-255 lowercase letters, numbers and hyphens
  (the error suggests a valid name; nothing is renamed silently); sizes are whole GB,
  10-10000 (the site's plan may allow only some sizes); volume IDs are 24 hexadecimal
  characters. `create` reads the compute sites to fill the site name and rejects an
  unknown `--site-id` (`--no-check-site` skips it). `--vm-type gpu` creates a volume
  for GPU VMs: a volume attaches only to VMs of the type it was created for.
- **Attach and detach** a volume to a server with `attach-vm` / `detach-vm` (or
  `ibee vms|gpus volume-attach|volume-detach`). `attach-vm` reads the volume: it must
  be unattached and idle, created for the VM's type (which picks the cloud or GPU
  endpoint) and in the VM's site; the volume's Block Storage SKU is sent as the
  billing catalog. `detach-vm` needs `--confirm-unmounted` (unmount inside the server
  first) or `--force` (asks again), and finds the VM from the volume when omitted.
  `--wait` polls like the portal: every 2 s, up to 120 s. `attach` and `detach` are
  advanced storage-node commands that do not attach the disk to a VM.
- **Resize** grows only; an attached volume needs `--vm-state stopped|suspended` or
  `--allow-online`. Extend the filesystem inside the server afterwards.
- **Delete** is refused while the volume is attached ("Detach this volume from all
  servers before deleting.") or busy; `--force` detaches it everywhere and erases all
  data (asks again).
- **Buckets**: names are 3-63 lowercase letters, numbers and hyphens, starting and
  ending with a letter or number (upper case is rejected, not lower-cased). A default
  retention (`--retention-mode GOVERNANCE|COMPLIANCE` with `--retention-days 1-36500`
  or `--retention-years 1-100`) switches Object Lock on. `--private` asks first because
  it disables the public URL and deletes any CDN distribution using the bucket.
  `delete` is refused for a bucket with Object Lock or with objects
  (`--skip-preflight` skips the object count, which can lag).
- **S3 credentials** default to `admin_rw` for all buckets; `--bucket-scope specific`
  with `--allowed-bucket` is only for `object_rw`/`object_ro`. Names are 1-100
  characters. The create is never retried, so the one-time secret cannot be lost.
  `delete` (and `revoke`) permanently delete the credential.
- **CDN**: distribution names are 1-128 characters and cache policies are
  `static-assets`, `media`, `short` or `no-cache`. `create` checks that the origin
  bucket is public (`--no-check-origin` skips it). Custom domains are lower-cased and
  must include a subdomain; `domains create` prints the CNAME record to add, and
  `domains verify --wait` checks every 15 s for up to 600 s (exit 1 when it fails, 3 on
  timeout). `purge` needs `--mode`; `--mode all` asks first; only the selector for the
  mode is accepted (`--path` 1-30, https URLs only; `--hostname`, `--tag`, `--prefix`
  1-100). A purge the CDN reports as failed exits 1.
- `--check-billing` and `--preflight-billing` are deprecated no-ops for storage and CDN.
- `block-storage create --vm-type`/`--delete-on-termination`, the Block Storage delete
  idempotency key, `cdn cache-policies` and `cdn metrics` are not yet part of the
  published API contract; behaviour may change. Block Storage plans, bucket emptying,
  CORS, lifecycle rules and object operations are not in the public API yet (use the
  S3 endpoint with S3 credentials for objects).

## Secret Store rules the CLI applies

Every `ibee secrets` command runs the Python SDK's Secret Store rules before it sends
anything (exit 2 when a rule fails); commands that ask for confirmation check their
inputs first.

- **Workspace and ids**: Secret Store needs a workspace id of 2-128 digits. Store,
  secret, identity and scope ids must not contain `/`, `?`, `#` or control characters.
- **Stores**: names are trimmed, 1-128 characters, and need at least one letter or
  digit. `stores list` includes archived stores (as the portal does) and asks for 100
  per page; it prints a hint when there are more (`--page`, `--limit` 1-200, `--all`).
  `stores create --if-exists reuse` returns the existing store with the same name or
  store key instead of failing with 409. `archive` and `unarchive` read the store
  first (`--no-check-state` skips it): an already archived or active store is left
  alone, and a store being deleted is refused. `unarchive`, `identities disable` and
  `identities enable` ask for confirmation only on a terminal, so scripts keep working.
  A permanent store delete the API reports as incomplete (503) lists the failed steps;
  run the same command again to finish it.
- **Secrets**: names are trimmed and lower-cased, as in the portal, then must be 2-64
  lowercase letters, digits and hyphens starting with a letter or digit. Values need
  at least one key; keys are trimmed and must not be blank or collide; empty string
  values are refused. In `patch-value`, a JSON `null` deletes that key. `--cas` is an
  integer >= 0; a check-and-set mismatch is reported as a conflict and never retried.
  `list` sends a trimmed `--query` (at most 128 characters) only when it is not blank.
- **Billing**: store and secret writes use upstream admission. Both legacy billing-check flags are no-ops.
- **Batch create**: every item follows the `secrets create` rules and the error names
  its index. Duplicate names are reported (the API skips them). A file with more than
  500 secrets or over 64 KiB is sent as consecutive requests and the results are
  merged; the command exits 1 when any secret failed.
- **Versions**: version lists hold 1-100 integers >= 1 and are de-duplicated.
  `rollback` reads the versions first and refuses the current, an unknown or a
  destroyed version (`--no-check-state` skips the check). `undelete` without
  `--versions` restores the current version, the one `delete` soft-deletes.
  `-o table secrets versions` lists versions newest first as active, available,
  soft_deleted or destroyed.
- **Identities**: names are trimmed, 1-128 characters. `--token-policy-mode` is always
  sent (default `read_only`). Kubernetes identities need both `--k8s-namespace` and
  `--k8s-service-account` (trimmed); AppRole identities must not have them.
  `identities update` warns that every scope is rewritten and sessions are revoked.
  `rotate-secret-id` reads the identity first and refuses anything but an active
  AppRole identity. `access` and `rotate-secret-id` print credentials and are never
  retried: each AppRole call issues a new secret ID and the old one is not revoked.
- **Scopes**: `scopes create` allows version reads by default (as the portal and the
  API do; `--deny-version-read` turns it off). A `read_only` scope cannot allow
  rollback or destroy. Unless `--no-check-state`, the identity, its scopes and the
  stores are read first: the store must be active and not already granted, and a
  `read_only` identity gets `read_only` access only.
- **Errors**: a missing store, secret, identity or scope (reported by the API as 403
  "does not belong to workspace") is printed as not found; organization lifecycle
  denials name the state and the operation; archived or inactive stores, disabled
  identities and soft-deleted values print what to do next.
- Workload runtime access (AppRole or Kubernetes login and runtime secret reads) is
  not in the public API yet.

## Global options

| Option | Environment variable | Meaning |
| --- | --- | --- |
| `--token` | `IBEE_TOKEN` (or `IBEE_API_TOKEN`) | API token |
| `--workspace`, `-w` | `IBEE_WORKSPACE_ID` | Workspace ID (a positive number) |
| `--dev` | `IBEE_ENV=dev` | Use the development environment |
| `--base-url` | `IBEE_BASE_URL`, `IBEE_ENDPOINT` | Custom API endpoint |
| `-o`, `--output` | `IBEE_OUTPUT` | `table`, `json`, `yaml` or `id` |
| `--json` | | Same as `-o json` |
| `--yes`, `-y` | `IBEE_ASSUME_YES=1` | Answer yes to every confirmation |
| `--check-billing` | `IBEE_CHECK_BILLING=1` | Deprecated no-op; upstream decides admission |

Without `-o`, each command keeps its usual format (tables for most lists, JSON
for single resources and operation results). `-o yaml` prints YAML and `-o id`
prints one identifier per line (for an accepted operation, its `operation_id`):

```bash
ibee --json buckets list
ibee -o id vms list
ibee -o yaml vms get VM_ID
```

`ibee vms list`, `ibee gpus list` and `ibee firewalls list` (with or without `--summary`) fetch every page.
Pass `--limit`/`--offset` for a single page; the VM lists also take `--search`,
`--sort-by created_at|name|status|os_type` and `--sort-direction asc|desc`.

### Retries and idempotency keys

Reads, and writes that carry an idempotency key on a route that honours it (VM
create/delete/actions and Block Storage volume writes), are retried up to twice
on HTTP 429, 502, 503 and 504 and on network errors, honouring `Retry-After` (at
most 30 s). Other writes are never retried automatically, and 408, 409 and 500
are never retried. The keyed commands accept `--idempotency-key KEY` (1-128
printable ASCII characters); when a keyed write fails with a retryable error, or
`--wait` times out, the CLI prints `Retry safely with: --idempotency-key KEY` so
you can repeat the command without creating the resource twice.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success (including a billing denial reported by `billing eligibility` without `--require`) |
| 1 | API error, network error, failed/cancelled/timed-out operation, declined confirmation, billing denial, or a server-state condition reported by the SDK (printed with its hint, for example a NAT gateway still reconciling during `vpcs delete --delete-nat-gateway`) |
| 2 | Usage or client-side validation error (missing token/workspace, invalid workspace ID, token/endpoint mismatch, invalid idempotency key, invalid `IBEE_ENV`, conflicting `--json`/`-o`) |
| 3 | `--wait` reached `--timeout` while the operation was still running; resume with `ibee ops wait OPERATION_ID` |

## Environments

The CLI targets production at `https://api.ibee.ai/v1` by default. Use `--dev`
(or `IBEE_ENV=dev`) for `https://api.ibee.co.in/v1`, or `--base-url` for a
custom endpoint. The endpoint is chosen in this order: `--base-url`,
`IBEE_BASE_URL`, `IBEE_ENDPOINT`, `--dev`, `IBEE_ENV`. `IBEE_ENV` accepts `dev`,
`development`, `prod` or `production` (any other value exits 2). Endpoints must
use `https://` (`http://` only for localhost) without credentials, query or
fragment. Production tokens (`ibee_prod_key_...`) only work with `.ai` and
development tokens (`ibee_dev_key_...`) only with `.co.in`; a mismatch exits 2
before any request. Resource IDs are environment-specific:

```bash
ibee --dev buckets list
```

## Related

- [Python SDK](https://github.com/devs-ibee/ibee-python) — `pip install ibee`
- [API documentation](https://ibee.ai/docs/api-reference)
