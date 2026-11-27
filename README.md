# IBEE Solutions CLI

Official command-line interface for the IBEE Solutions cloud platform, built on the
[`ibee` Python SDK](https://github.com/devs-ibee/ibee-python). It has one command per
operation of the IBEE public API (contract 0.14.3, 222 operations): each command makes
that operation's one SDK call and prints the server's answer. The server decides
prices, eligibility, limits and states; the CLI checks nothing before sending.

The API manages resources. Data stays on each product's own endpoint, with the
clients that already exist for it: files over the S3 API
(`<workspace>.blob.ibeestorage.com`, any S3 tool such as `aws s3` or `rclone`, or one
file at a time with `ibee buckets objects upload` and an upload or download link),
container images with `docker` at `registry.ibee.ai`, and secret values with `bao` or
`vault` at your Secret Manager endpoint.

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

Both can also be passed per command with `--token` and `--workspace`. The workspace is
sent as every operation's `workspace_id`.

## Commands

`ibee --help` lists the groups (`vms`, `gpus`, `ops`, `compute`, `block-storage`,
`buckets`, `cdn`, `secrets`, `vpcs`, `reserved-ips`, `firewalls`, `load-balancers`,
`registries`, `billing`); [COMMANDS.md](COMMANDS.md) lists every command with its API operation,
and `ibee COMMAND --help` shows each option's description from the contract.

Every command follows the contract, field for field:

- path parameters are arguments, in path order (`ibee vms get VM_ID`);
- every query parameter, header and request-body field is an option of the same name
  (`ssh_key_ids` is `--ssh-key-ids`); the contract's required fields are required
  options;
- booleans are `--name/--no-name`; arrays of strings or numbers repeat the option
  (`--tags a --tags b`); objects and arrays of objects take JSON, and a JSON value of
  `@FILE` reads the file, `@-` standard input (`--rules @rules.json`);
- `X-Idempotency-Key` (and Container Registry's `Idempotency-Key`) is
  `--idempotency-key`; without it the SDK sends a new random key.

```bash
ibee compute sites
ibee compute plans --vm-type cloud --site-id SITE_ID
ibee compute images --vm-type cloud --site-id SITE_ID

ibee vms create --name web-1 --site-id SITE_ID --plan-id PLAN_ID --template-id IMAGE_ID \
  --os-type linux --os-distro ubuntu --cpu 2 --ram-mb 4096 --disk-gb 50 \
  --billing-interval HOURLY --ssh-keys "$(cat ~/.ssh/id_ed25519.pub)"
ibee ops get OPERATION_ID
ibee vms list --limit 20
ibee vms resize VM_ID --cpu 4 --ram-mb 8192 --disk-gb 80 --plan-id PLAN_ID --billing-interval HOURLY
ibee billing wallet
ibee vms delete VM_ID --public-ip-action release --yes

ibee buckets create --name assets --region in-south-2
ibee buckets cors set assets --rules @cors.json
ibee buckets credentials create --name ci --permission-type object_rw
ibee buckets objects upload assets docs/report.pdf ./report.pdf --content-type application/pdf
ibee buckets objects metadata assets docs/report.pdf
ibee buckets objects download-link assets docs/report.pdf --expires 600 --disposition attachment
ibee load-balancers list --include-deleted
ibee secrets credentials create --name ci --access read --folder apps
ibee secrets update apps/billing/db --custom-metadata '{"owner": "billing"}'
ibee load-balancers regions
ibee registries tokens create REGISTRY_ID --token-name ci-push --permission PULL_PUSH
```

### VM creates: `--billing-interval`

`vms create` and `gpus create` need the contract's `billing_catalog` (a
`ComputeBillingSelection`). Pass it as JSON with `--billing-catalog`, or pass
`--billing-interval HOURLY|MONTHLY`: the CLI then reads the `--plan-id` plan from
`GET /compute/plans` (with the create's `vm_type` and `site_id`), copies its
`billing_catalog` and merges in the whole `billing_options` entry for that interval,
exactly as the portal does. Nothing else is filled in: CPU, RAM, disk, OS and the
other fields are sent as you give them. `MONTHLY` is a one-month commitment (the API
answers `409 VM_COMMITTED` to a delete or resize before it ends).

`vms resize` and `gpus resize` move a VM to another plan. Send the target plan's exact
`--cpu`, `--ram-mb` and `--disk-gb`, and its `billing_catalog`: `--billing-catalog JSON`,
or `--plan-id PLAN_ID --billing-interval HOURLY|MONTHLY` to build it the same way from
`GET /compute/plans`. A resize body has no plan field, so `--plan-id` is only used for
this lookup and is not sent.

### One file: `buckets objects upload`

`ibee buckets objects upload BUCKET_NAME OBJECT_PATH FILE` (`-` reads stdin) uploads
one file with an upload link: the SDK's `upload_file` computes the file's size and
SHA-256, asks for the link (`createObjectUploadLink`) and PUTs the file to it on the
workspace's S3 host; the API token is not sent there. It prints the key, size, SHA-256
and ETag; `buckets objects metadata` shows what was stored. One link carries one whole
file of at most 80,000,000 bytes (the server's limit); use an S3 tool for larger
files. `buckets objects upload-link`, `download-link` and `metadata` are the plain
operations.

## Global options

| Option | Environment variable | Meaning |
| --- | --- | --- |
| `--token` | `IBEE_TOKEN` (or `IBEE_API_TOKEN`) | API token |
| `--workspace`, `-w` | `IBEE_WORKSPACE_ID` | Workspace ID (`workspace_id`) |
| `--dev` | `IBEE_ENV=dev` | Use the development environment |
| `--base-url` | `IBEE_BASE_URL`, `IBEE_ENDPOINT` | Custom API endpoint |
| `-o`, `--output` | `IBEE_OUTPUT` | `json` (default), `yaml`, `table` or `id` |
| `--json` | | Same as `-o json` |
| `--yes`, `-y` | `IBEE_ASSUME_YES=1` | Answer yes to every confirmation |

The endpoint is chosen in this order: `--base-url`, `IBEE_BASE_URL`, `IBEE_ENDPOINT`,
`--dev`, `IBEE_ENV`; the default is production, `https://api.ibee.ai/v1`.

## Output

The server's answer is printed as JSON by default. `-o yaml` prints it as YAML,
`-o table` as a table (one row per item of a list, or one row per field of an
object) and `-o id` one identifier per line (for a list, the value the API takes for
each item, such as a bucket's name). Every delete prints its answer, including
`deletion`: whether the resource is gone (`deleted`) or still going (`deleting`).
Destructive commands (every delete and revoke, `buckets credentials revoke`, `cdn purge`,
VM restores and volume detach, `secrets versions destroy` and `secrets workspace enable`,
which can't be undone) ask for confirmation; `--yes` (or the global `--yes`) skips it.

## Errors

Every error prints the HTTP status and the server's fields as sent:

```text
Error: HTTP 409
  code: VM_COMMITTED
  message: ...
  request_id: 6f1c2e...
```

A Billing refusal adds `reason` (`billing_reason`) and, with
`initial_topup_required`, Billing's first top-up minimum:

```text
Error: HTTP 402
  code: billing_denied
  reason: initial_topup_required
  required_topup_minor: 50000
  billing_currency: INR
  message: Add at least ₹500 to your wallet ...
```

With `-o json` the same fields, plus the raw `body`, are printed on stderr as
`{"error": {...}}`. A success answer the SDK cannot read (it does not match the
contract) is printed the same way, as received, with exit code 1.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Declined confirmation, an answer that does not match the contract, or another error |
| 2 | Usage or configuration error (missing option, token or workspace, bad JSON, unsafe endpoint); nothing was sent |
| 4 | Network error (no HTTP answer) |
| 10, 11, 12, 13, 14 | HTTP 400, 401, 402, 403, 404 |
| 15, 16, 17, 18 | HTTP 409, 422, 423, 429 |
| 19 | HTTP 5xx |

## Retries and idempotency keys

The SDK retries reads on 429, 502, 503 and 504, and writes that carry an idempotency
key on 429 and 503, never on a billing or admission answer; VM creates are never
retried. After a retryable failure the CLI prints `Retry safely with:
--idempotency-key KEY` so the command can be repeated without doing the write twice.

## Development

```bash
pip install "ibee @ git+https://github.com/devs-ibee/ibee-python@main"  # or the SDK branch you need
pip install -e ".[dev]"
python scripts/generate_commands.py --check   # the commands match contract/ibee-cloud.yaml
python scripts/check_sdk_calls.py             # every SDK method has a command, and only those are called
pytest -q
```

The command modules in `src/ibee_cli/commands/` and `COMMANDS.md` are generated from
`contract/ibee-cloud.yaml` (a copy of the public API contract) by
`scripts/generate_commands.py`; after updating the contract, run the script and commit
what it writes. The hand-written parts are the global options (`context.py`), output
and errors (`render.py`), the shared request helpers (`helpers.py`) and the VM
`billing_catalog` convenience (`billing_selection.py`). `buckets objects upload` is
generated too; it calls the SDK's `upload_file`.

CI runs the tests on Python 3.10-3.12 with the ibee-python branch of the same name (or
ibee from PyPI), and the "SDK drift" check against ibee from PyPI (once a release in
the CLI's range exists) and ibee-python `main` (or its branch of the same name).

## Related

- [Python SDK](https://github.com/devs-ibee/ibee-python): `pip install ibee`
- [API documentation](https://ibee.ai/docs/api-reference)
