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
export IBEE_TOKEN="ibee_live_xxxxxxxxxxxx"
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

The CLI targets production by default. Use `--dev` (or `IBEE_ENV=dev`) for the development environment, or `--base-url` for a custom endpoint:

```bash
ibee --dev buckets list
```

## Related

- [Python SDK](https://github.com/devs-ibee/ibee-python) — `pip install ibee`
- [API documentation](https://ibee.ai/docs/api-reference)
