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
# Object storage
ibee buckets list
ibee buckets create my-bucket --region in-south-1
ibee buckets get my-bucket
ibee buckets update my-bucket --public
ibee buckets delete my-bucket --yes

# S3-compatible credentials (secret_access_key is shown only at creation)
ibee buckets credentials list
ibee buckets credentials create --name deploy
ibee buckets credentials create \
  --name backups --bucket-scope specific --allowed-bucket my-bucket
ibee buckets credentials get ACCESS_KEY_ID
ibee buckets credentials revoke ACCESS_KEY_ID --yes

# Secret Store — stores
ibee secrets stores list
ibee secrets stores create production-secrets --description "prod"
ibee secrets stores get STORE_ID
ibee secrets stores update STORE_ID --name renamed
ibee secrets stores archive STORE_ID --yes

# Secret Store — secrets
ibee secrets list --store-id STORE_ID
ibee secrets create --store-id STORE_ID --name db-url --value '{"url":"postgres://..."}'
ibee secrets get SECRET_ID           # metadata
ibee secrets value SECRET_ID         # current value
ibee secrets set-value SECRET_ID --value '{"url":"postgres://new"}'
ibee secrets delete SECRET_ID --yes

# Compute catalog (discover placement / plans / images for `create`)
ibee compute sites
ibee compute plans --vm-type cloud
ibee compute images --vm-type gpu

# Billing admission (SKU must come from an IBEE product catalog)
ibee billing eligibility --sku-code PLAN_SKU
ibee -o table billing eligibility --sku-code PLAN_SKU --require

# Standalone Block Storage
ibee block-storage list
ibee block-storage create data --size-gb 100 --site-id SITE_ID
ibee block-storage get VOLUME_ID
ibee block-storage operations VOLUME_ID
ibee block-storage attach VOLUME_ID --node-name NODE --vm-id VM_ID
ibee block-storage detach VOLUME_ID --node-name NODE --confirm-unmounted
ibee block-storage resize VOLUME_ID --new-size-gb 200
ibee block-storage delete VOLUME_ID --yes

# CDN
ibee cdn list
ibee cdn create assets --origin-id BUCKET_NAME
ibee cdn get DISTRIBUTION_ID
ibee cdn update DISTRIBUTION_ID --cache-policy media
ibee cdn website set DISTRIBUTION_ID --index-document index.html
ibee cdn domains create DISTRIBUTION_ID static.example.com
ibee cdn domains verify DISTRIBUTION_ID static.example.com
ibee cdn purge DISTRIBUTION_ID --mode all
ibee cdn generate-url BUCKET_NAME path/to/object.jpg --expires-in 3600
ibee cdn delete DISTRIBUTION_ID --yes

# Cloud VMs
ibee vms list
ibee vms get VM_ID
ibee vms create web-01 --plan-id PLAN_ID --template-id IMAGE_ID --ssh-key-id KEY_ID --wait
ibee vms start VM_ID
ibee vms stop VM_ID
ibee vms reboot VM_ID
ibee vms metrics VM_ID
ibee vms metrics-timeseries VM_ID --range 24h
ibee vms bandwidth VM_ID --month 2026-08
ibee vms events VM_ID --limit 50

# Access credentials. Passwords are never accepted as command arguments.
printf '%s\n' "$NEW_VM_PASSWORD" | ibee vms access-update VM_ID --password-stdin --wait
ibee vms access-update VM_ID --prompt-password --enable-password-auth --wait
ibee vms access-update VM_ID --ssh-key-mode add --ssh-key-id SSH_KEY_ID --wait

# Resize and persistent block volumes
ibee vms resize-precheck VM_ID --cpu 4 --ram-mb 8192 --disk-gb 80
ibee vms resize VM_ID --cpu 4 --ram-mb 8192 --disk-gb 80 --wait
ibee vms resize-plan VM_ID --cpu 8 --ram-mb 16384 --confirm-downgrade --wait
ibee vms resize-root-disk VM_ID --new-size-gb 120 --wait
ibee vms volume-attach VM_ID VOLUME_ID --mode single-writer --wait
ibee vms mount-guidance-acknowledge VM_ID VOLUME_ID
ibee vms volume-detach VM_ID VOLUME_ID --confirm-unmounted --yes --wait

# Snapshots and restores
ibee vms snapshots list VM_ID
ibee vms snapshots create VM_ID before-upgrade --mode all_attached
ibee vms snapshots get SNAPSHOT_SET_ID
ibee vms snapshots restore VM_ID SNAPSHOT_SET_ID \
  --target-mode new_vm --target-vm-name restored-web --auto-start --yes
ibee vms snapshots restore-status RESTORE_ID
ibee vms snapshots delete SNAPSHOT_SET_ID --yes

# Automated backup policy and manual backup runs
ibee vms backup-policy get VM_ID
ibee vms backup-policy enable VM_ID \
  --frequency daily --timezone Asia/Kolkata --hour 2 --retention-days 30
ibee vms backup-policy update VM_ID --frequency weekly --day-of-week 6
ibee vms backup-policy reschedule VM_ID --next-run-at 2026-08-10T02:00:00Z
ibee vms backup-policy disable VM_ID
ibee vms backups list VM_ID
ibee vms backups create VM_ID --reason before-release
ibee vms backups get BACKUP_RUN_ID
ibee vms backups restore VM_ID RECOVERY_POINT_ID --target-mode replace --yes
ibee vms backups restore-status RESTORE_ID
ibee vms delete VM_ID --yes --wait

# GPU VMs expose the same lifecycle, snapshot, backup, metrics, and volume commands
ibee gpus list
ibee gpus create train-01 --gpu-model A100 --gpu-count 1 \
  --plan-id PLAN_ID --template-id IMAGE_ID --wait
ibee gpus start VM_ID
ibee gpus stop VM_ID
ibee gpus reboot VM_ID
ibee gpus metrics VM_ID
ibee gpus resize-precheck VM_ID --cpu 16 --ram-mb 65536
ibee gpus snapshots create VM_ID before-training --mode root_only
ibee gpus backup-policy enable VM_ID --frequency daily --retention-days 14
ibee gpus delete VM_ID --yes

# Short-lived graphical console sessions for either VM type
ibee console create VM_ID --vm-type cloud
ibee console get SESSION_ID
ibee console close SESSION_ID --yes

# Async operations — poll a create/delete/power action to completion
ibee ops get OPERATION_ID
ibee ops wait OPERATION_ID --timeout 1800 --poll-interval 10

# VPCs
ibee vpcs sites
ibee vpcs list
ibee vpcs create production --site-id SITE_ID --cidr 10.44.0.0/22 --no-auto-cidr
ibee vpcs get VPC_ID
ibee vpcs update VPC_ID --name production-apps
ibee vpcs delete VPC_ID --yes

# Subnets and VM attachments
ibee vpcs subnets list VPC_ID
ibee vpcs subnets create VPC_ID services --cidr 10.44.0.0/24 --no-auto-cidr
ibee vpcs subnets update VPC_ID SUBNET_ID --dns 1.1.1.1 --dns 8.8.8.8
ibee vpcs nodes list VPC_ID
ibee vpcs nodes attach VPC_ID VM_ID --subnet-id SUBNET_ID --connectivity private
ibee vpcs nodes detach VPC_ID VM_ID --yes

# NAT and port forwarding
ibee vpcs nat list VPC_ID
ibee vpcs nat create VPC_ID --name egress --reserved-ip-id RESERVED_IP_ID
ibee vpcs forwarding list VPC_ID NAT_GATEWAY_ID
ibee vpcs forwarding create VPC_ID NAT_GATEWAY_ID ssh \
  --external-port 2222 --internal-ip 10.44.0.10 --internal-port 22
ibee vpcs forwarding update VPC_ID NAT_GATEWAY_ID RULE_ID --external-port 2200

# Reserved public IPs
ibee reserved-ips list --site-id SITE_ID
ibee reserved-ips reserve --site-id SITE_ID --label edge
ibee reserved-ips update RESERVED_IP_ID --reverse-dns app.example.com
ibee reserved-ips attach RESERVED_IP_ID VM_ID --vpc-id VPC_ID --subnet-id SUBNET_ID
ibee reserved-ips detach RESERVED_IP_ID
ibee reserved-ips move RESERVED_IP_ID NEW_VM_ID --vpc-id VPC_ID --subnet-id SUBNET_ID
ibee reserved-ips release RESERVED_IP_ID --yes

# Firewall groups, rules, and attachments
ibee firewalls create web --description "Web ingress"
ibee firewalls rules create FIREWALL_GROUP_ID \
  --protocol tcp --port-start 443 --port-end 443 --remote-target 0.0.0.0/0
ibee firewalls rules update FIREWALL_GROUP_ID RULE_ID --disabled
ibee firewalls attachments attach FIREWALL_GROUP_ID VM_ID
ibee firewalls attachments detach FIREWALL_GROUP_ID VM_ID --yes

# L4 and L7 load balancers
ibee load-balancers list --layer l7
ibee load-balancers create-l4 tcp-edge \
  --backends '[{"type":"ip","target":"10.44.0.10","port":443}]'
ibee load-balancers create-l7 web \
  --protocol https \
  --backends '[{"type":"service","target":"api","port":8080}]' \
  --routing '{"algorithm":"least_request"}' \
  --custom-domain app.example.com
ibee load-balancers update-l7 LOAD_BALANCER_ID --name public-web
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

Billable creates send one product request. The public gateway performs the
authoritative, fail-closed billing decision before routing; a valid denial is
returned without calling the product service. `ibee billing eligibility`
remains available as an optional point-in-time preview, and the global
`--check-billing` flag (or `IBEE_CHECK_BILLING=1`) runs the portal's preflight
before a billable create: the create is sent only when billing answers
`allowed: true`, otherwise the portal's explanation is printed (with top-up
guidance when adding credits in the portal can resolve it) and the command
exits 1. The preflight never reserves funds.

For load-balancer backends, routing, TLS, and L7 rules, pass JSON matching the
[API reference](https://ibee.ai/docs/api-reference). This keeps advanced
configurations available without a large set of fragile shell flags.

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
| `--check-billing` | `IBEE_CHECK_BILLING=1` | Billing preflight before billable creates |

Without `-o`, each command keeps its usual format (tables for most lists, JSON
for single resources and operation results). `-o yaml` prints YAML and `-o id`
prints one identifier per line (for an accepted operation, its `operation_id`):

```bash
ibee --json buckets list
ibee -o id vms list
ibee -o yaml vms get VM_ID
```

`ibee vms list`, `ibee gpus list` and `ibee firewalls list` fetch every page.
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
| 1 | API error, network error, failed/cancelled/timed-out operation, declined confirmation, or billing denial |
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
