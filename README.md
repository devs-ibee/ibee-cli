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

# Secret Store
ibee secrets stores
ibee secrets list --store-id STORE_ID
ibee secrets value SECRET_ID

# Cloud VMs
ibee vms list
ibee vms get VM_ID
ibee vms start VM_ID
ibee vms stop VM_ID
ibee vms reboot VM_ID

# GPU VMs
ibee gpus list
```

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
