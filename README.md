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
ibee buckets create my-bucket --site-id SITE_ID
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

# Cloud VMs
ibee vms list
ibee vms get VM_ID
ibee vms create web-01 --site-id SITE_ID --plan-id PLAN_ID --template-id IMAGE_ID --ssh-key-id KEY_ID --wait
ibee vms start VM_ID
ibee vms stop VM_ID
ibee vms reboot VM_ID
ibee vms metrics VM_ID
ibee vms delete VM_ID --yes --wait

# GPU VMs (same verbs as Cloud VMs)
ibee gpus list
ibee gpus create train-01 --site-id SITE_ID --gpu-model A100 --gpu-count 1 \
  --plan-id PLAN_ID --template-id IMAGE_ID --wait
ibee gpus start VM_ID
ibee gpus stop VM_ID
ibee gpus reboot VM_ID
ibee gpus metrics VM_ID
ibee gpus delete VM_ID --yes

# Async operations — poll a create/delete/power action to completion
ibee ops get OPERATION_ID --wait

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

Create/delete/power actions are asynchronous; add `--wait` to block until the
operation finishes, or poll it later with `ibee ops get`.

For load-balancer backends, routing, TLS, and L7 rules, pass JSON matching the
[API reference](https://ibee.ai/docs/api-reference). This keeps advanced
configurations available without a large set of fragile shell flags.

Every command accepts `--json` for raw output:

```bash
ibee --json buckets list
```

## Environments

The CLI targets production at `https://api.ibee.ai/v1` by default. Use `--dev`
(or `IBEE_ENV=dev`) for `https://api.ibee.co.in/v1`, or `--base-url` for a
custom endpoint:

```bash
ibee --dev buckets list
```

## Related

- [Python SDK](https://github.com/devs-ibee/ibee-python) — `pip install ibee`
- [API documentation](https://ibee.ai/docs/api-reference)
