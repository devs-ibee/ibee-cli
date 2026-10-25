# IBEE Solutions CLI

Official command-line interface for the IBEE Solutions cloud platform, built on the
[`ibee` Python SDK](https://github.com/devs-ibee/ibee-python). Each command makes one
SDK call and prints the server's answer; the server decides prices, eligibility,
limits and states.

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
ibee compute sites
ibee compute plans --vm-type cloud --site-id SITE_ID
ibee compute images --vm-type cloud --site-id SITE_ID

ibee vms create web-1 --site-id SITE_ID --plan-id PLAN_ID --template-id IMAGE_ID \
  --ssh-key-file ~/.ssh/id_ed25519.pub --wait
ibee vms list
ibee vms get VM_ID
ibee vms stop VM_ID --wait
ibee vms delete VM_ID --public-ip-action release --yes

ibee ops wait OPERATION_ID
ibee vpcs list
ibee reserved-ips list
ibee firewalls list
ibee load-balancers list
ibee block-storage list
ibee buckets list
ibee cdn list
ibee secrets stores list
ibee billing eligibility --resource-type cpu_vm
```

### Container Registry

```bash
ibee registries name-check acme-apps
ibee registries create acme-apps --sku REGISTRY-STORAGE-250GB   # 202 while provisioning
ibee registries get REGISTRY_ID                                 # observed_state: ACTIVE
ibee -o json registries tokens create REGISTRY_ID ci-push --permission PULL_PUSH
ibee registries repositories list REGISTRY_ID
ibee registries artifacts list REGISTRY_ID --repository api
ibee registries vulnerabilities summary REGISTRY_ID
ibee registries empty REGISTRY_ID --confirm-name acme-apps --yes
ibee registries delete REGISTRY_ID --confirm-name acme-apps --yes
```

`registries` has one command per Container Registry API operation (`plan`, `tokens`,
`repositories`, `artifacts`, `security`, `vulnerabilities`, `sboms`, `metrics`). An
access token's username and secret are printed only by `tokens create` and
`tokens rotate`, and only with `--show-sensitive` or `-o json|yaml` (`-o id` prints the
token ID); otherwise nothing is sent. The registry service and Billing decide plans,
names and states; the CLI prints their answer.

`ibee --help` and `ibee GROUP --help` list every command. `gpus` takes the same
commands as `vms`. Until the server resolves VM SKUs itself, `vms/gpus create` fills
CPU, RAM, disk and OS from the plan and the image and sends the plan's billing SKU for
`--billing-term` (see the SDK's `held.py`).

## Global options

| Option | Environment variable | Meaning |
| --- | --- | --- |
| `--token` | `IBEE_TOKEN` (or `IBEE_API_TOKEN`) | API token |
| `--workspace`, `-w` | `IBEE_WORKSPACE_ID` | Workspace ID |
| `--dev` | `IBEE_ENV=dev` | Use the development environment |
| `--base-url` | `IBEE_BASE_URL`, `IBEE_ENDPOINT` | Custom API endpoint |
| `-o`, `--output` | `IBEE_OUTPUT` | `table`, `json`, `yaml` or `id` |
| `--json` | | Same as `-o json` |
| `--yes`, `-y` | `IBEE_ASSUME_YES=1` | Answer yes to every confirmation |

The endpoint is chosen in this order: `--base-url`, `IBEE_BASE_URL`, `IBEE_ENDPOINT`,
`--dev`, `IBEE_ENV`; the default is production, `https://api.ibee.ai/v1`. Without
`-o`, each command keeps its usual format (tables for most lists, JSON for single
resources).

## Errors

Every error prints the HTTP status and the server's fields as sent:

```text
Error: HTTP 402
  code: billing_denied
  reason: resource_limit_reached
  message: -
  request_id: 6f1c2e...
  admission_context_id: adm_...
```

With `-o json` the same fields, plus the raw `body`, are printed on stderr as
`{"error": {...}}`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Failed, cancelled or timed-out operation (`--wait`), declined confirmation, or another error |
| 2 | Usage or configuration error (missing token or workspace, unsafe endpoint, token for the other environment); nothing was sent |
| 3 | `--wait` reached `--timeout` while the operation was still running; resume with `ibee ops wait OPERATION_ID` |
| 4 | Network error (no HTTP answer) |
| 10, 11, 12, 13, 14 | HTTP 400, 401, 402, 403, 404 |
| 15, 16, 17, 18 | HTTP 409, 422, 423, 429 |
| 19 | HTTP 5xx |

## Retries and idempotency keys

The SDK retries reads on 429, 502, 503 and 504, and writes that carry an idempotency
key on 429 and 503, never on a billing or admission answer. Write commands that take
`--idempotency-key KEY` send it unchanged (without one the SDK sends a random key);
after a retryable failure the CLI prints `Retry safely with: --idempotency-key KEY` so
the command can be repeated without doing the write twice. `--wait` polls the
operation every `--poll-interval` seconds (default 5) for up to `--timeout` seconds
(default 1200).

## Development

`pip install -e ".[dev]" && pytest -q`. CI also runs `python scripts/check_sdk_calls.py`
against `ibee` from PyPI (once a release in the CLI's range exists) and from
ibee-python `main` (or its branch of the same name): every SDK method the CLI calls
(including the names the VM commands build from templates) must exist in the installed
SDK, and the CLI must make no raw HTTP calls.

## Related

- [Python SDK](https://github.com/devs-ibee/ibee-python): `pip install ibee`
- [API documentation](https://ibee.ai/docs/api-reference)
