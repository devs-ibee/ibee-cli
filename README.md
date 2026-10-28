# IBEE Solutions CLI

Official command-line interface for the IBEE Solutions cloud platform, built on the
[`ibee` Python SDK](https://github.com/devs-ibee/ibee-python). It has one command per
operation of the IBEE public API (contract 0.6.2, 225 operations): each command makes
that operation's one SDK call and prints the server's answer. The server decides
prices, eligibility, limits and states; the CLI checks nothing before sending.

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
`registries`); [COMMANDS.md](COMMANDS.md) lists every command with its API operation,
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
ibee vms resize VM_ID --cpu 4 --ram-mb 8192
ibee vms delete VM_ID --public-ip-action release --yes

ibee buckets create --name assets --region in-south-2
ibee buckets cors set assets --rules @cors.json
ibee buckets credentials create --name ci --permission-type object_rw
ibee secrets create STORE_ID --secret-name db --value '{"password": "..."}'
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
each item, such as a bucket's name). An empty answer (HTTP 204) prints nothing.
`registries sboms download` writes the document to standard output.

Destructive commands (every delete, revoke, archive, purge, restore and volume
detach) ask for confirmation; `--yes` (or the global `--yes`) skips it.

## Errors

Every error prints the HTTP status and the server's fields as sent:

```text
Error: HTTP 409
  code: VM_COMMITTED
  message: ...
  request_id: 6f1c2e...
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
`billing_catalog` convenience (`billing_selection.py`).

CI runs the tests on Python 3.10-3.12 with the ibee-python branch of the same name (or
ibee from PyPI), and the "SDK drift" check against ibee from PyPI (once a release in
the CLI's range exists) and ibee-python `main` (or its branch of the same name).

## Related

- [Python SDK](https://github.com/devs-ibee/ibee-python): `pip install ibee`
- [API documentation](https://ibee.ai/docs/api-reference)
