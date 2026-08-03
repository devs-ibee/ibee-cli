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
ibee buckets create my-bucket
ibee buckets delete my-bucket --yes

# Secret Store — stores
ibee secrets stores list
ibee secrets stores create production-secrets --description "prod"
ibee secrets stores get STORE_ID
ibee secrets stores update STORE_ID --name renamed
ibee secrets stores archive STORE_ID --yes
ibee secrets stores unarchive STORE_ID
# Irreversibly deletes the store and every store-scoped resource
ibee secrets stores delete-permanent STORE_ID --yes

# Secret Store — secrets
ibee secrets list --store-id STORE_ID
ibee secrets create --store-id STORE_ID --name db-url --value '{"url":"postgres://..."}'
ibee secrets get SECRET_ID           # metadata
ibee secrets value SECRET_ID         # current value
ibee secrets set-value SECRET_ID --value '{"url":"postgres://new"}'
ibee secrets patch-value SECRET_ID --value '{"username":"app"}'
ibee secrets versions SECRET_ID      # version metadata only
ibee secrets version SECRET_ID 1     # includes the sensitive version value
ibee secrets rollback SECRET_ID --version 1
ibee secrets delete SECRET_ID --yes
ibee secrets undelete SECRET_ID --versions 1,2
ibee secrets destroy-versions SECRET_ID --versions 1 --yes
ibee secrets delete-permanent SECRET_ID --yes

# Secret Store — application identities
ibee secrets identities list --store-id STORE_ID
ibee secrets identities create --store-id STORE_ID --name worker --auth-method approle
ibee secrets identities get IDENTITY_ID
ibee secrets identities update IDENTITY_ID --token-policy-mode read_write
ibee secrets identities disable IDENTITY_ID
ibee secrets identities enable IDENTITY_ID

# These commands print generated credentials only with an explicit opt-in.
# Keep terminal output, logs, and shell history secure.
ibee secrets identities access IDENTITY_ID --show-sensitive
ibee secrets identities rotate-secret-id IDENTITY_ID --show-sensitive
ibee secrets identities revoke-sessions IDENTITY_ID --yes

# Kubernetes identities require both binding fields
ibee secrets identities create --store-id STORE_ID --name in-cluster \
  --auth-method kubernetes --k8s-namespace apps --k8s-service-account worker

# Grant and update access to additional stores
ibee secrets identities scopes list IDENTITY_ID
ibee secrets identities scopes create IDENTITY_ID --store-id OTHER_STORE_ID \
  --access-mode read_only --allow-version-read
ibee secrets identities scopes update SCOPE_ID --access-mode read_write --allow-rollback
ibee secrets identities scopes delete SCOPE_ID --yes

# Permanently removes the identity, all scopes, its role/policy, and sessions
ibee secrets identities delete IDENTITY_ID --yes

# Compute catalog (discover placement / plans / images for `create`)
ibee compute sites
ibee compute plans --vm-type cloud
ibee compute images --vm-type gpu

# Cloud VMs
ibee vms list
ibee vms get VM_ID
ibee vms create web-01 --plan-id PLAN_ID --template-id IMAGE_ID --ssh-key-id KEY_ID --wait
ibee vms start VM_ID
ibee vms stop VM_ID
ibee vms reboot VM_ID
ibee vms metrics VM_ID
ibee vms delete VM_ID --yes --wait

# GPU VMs (same verbs as Cloud VMs)
ibee gpus list
ibee gpus create train-01 --gpu-model A100 --gpu-count 1 --plan-id PLAN_ID --wait
ibee gpus start VM_ID
ibee gpus stop VM_ID
ibee gpus reboot VM_ID
ibee gpus metrics VM_ID
ibee gpus delete VM_ID --yes

# Async operations — poll a create/delete/power action to completion
ibee ops get OPERATION_ID --wait
```

Create/delete/power actions are asynchronous; add `--wait` to block until the
operation finishes, or poll it later with `ibee ops get`.

Every command accepts `--json` for raw output:

```bash
ibee --json buckets list
```

## Environments

The CLI targets production (`https://api.ibee.ai/v1`) by default. Use `--dev`
(or `IBEE_ENV=dev`) for the development gateway
(`https://api.ibee.co.in/v1`), or `--base-url` for a custom endpoint. Match
production tokens to `.ai` and development tokens to `.co.in`; resource IDs
are environment-specific.

```bash
ibee --dev buckets list
```

## Related

- [Python SDK](https://github.com/devs-ibee/ibee-python) — `pip install ibee`
- [API documentation](https://ibee.ai/docs/api-reference)
