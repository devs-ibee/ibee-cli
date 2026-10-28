"""One command per contract operation, flags 1:1 with its fields, one SDK call each.

The expectations are read from ``contract/ibee-cloud.yaml`` here, independently of
``scripts/generate_commands.py``; every request goes through the real SDK.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest
import typer.main
import yaml
from _net_fixtures import WS, run

from ibee_cli.main import app

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = yaml.safe_load((ROOT / "contract" / "ibee-cloud.yaml").read_text(encoding="utf-8"))
COMPONENTS = CONTRACT["components"]
VM_CREATES = {"createCloudVm", "createGpuVm"}
#: Destructive commands besides DELETE: they ask for confirmation unless --yes.
CONFIRMED_POSTS = {
    "archiveSecretStore", "revokeSecretIdentitySessions", "destroySecretVersions", "revokeS3Credential", "purgeCdnCache",
    "restoreCloudVmSnapshot", "restoreCloudVmBackup", "detachCloudVmVolume",
    "restoreGpuVmSnapshot", "restoreGpuVmBackup", "detachGpuVmVolume",
}
#: The 23 operations contract 0.6.2 removed (from 0.5.0) and the commands that called them.
REMOVED_COMMANDS = [
    "vms mount-guidance-acknowledge", "gpus mount-guidance-acknowledge", "block-storage attach", "block-storage detach",
    "billing eligibility", "console close", "console create", "console get", "vms backups create", "gpus backups create",
    "registries empty", "cdn generate-url", "secrets patch-value", "vms backup-policy reschedule",
    "gpus backup-policy reschedule", "vms resize-plan", "vms resize-root-disk", "gpus resize-plan", "gpus resize-root-disk",
    "secrets identities update", "secrets identities scopes update", "secrets stores update", "vpcs subnets update",
]


def resolve(node):
    while isinstance(node, dict) and "$ref" in node:
        _, _, kind, name = node["$ref"].split("/", 3)
        node = COMPONENTS[kind][name]
    return node


def body_fields(schema) -> tuple[dict, set]:
    schema = resolve(schema) or {}
    props, required = {}, set()
    for part in schema.get("allOf", []):
        more, req = body_fields(part)
        props.update(more)
        required |= req
    props.update(schema.get("properties") or {})
    required |= set(schema.get("required") or [])
    return props, required


def kind(schema) -> str:
    schema = resolve(schema) or {}
    if "allOf" in schema and len(schema["allOf"]) == 1 and not schema.get("properties"):
        return kind(schema["allOf"][0])
    for key in ("oneOf", "anyOf"):
        if key in schema:
            options = [s for s in map(resolve, schema[key]) if s.get("type") != "null"]
            return kind(options[0]) if len(options) == 1 else "json"
    t = schema.get("type")
    if t == "array":
        item = kind(schema.get("items") or {})
        return "list:" + item if item in ("str", "int", "float") else "json-array"
    return {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}.get(t, "json")


def operations():
    for path, item in CONTRACT["paths"].items():
        for method in ("get", "post", "put", "patch", "delete"):
            if method in item:
                op = item[method]
                params = [resolve(p) for p in item.get("parameters", []) + op.get("parameters", [])]
                body = op.get("requestBody")
                props, required = body_fields(body["content"]["application/json"]["schema"]) if body else ({}, set())
                yield SimpleOp(op["operationId"], method.upper(), path, params, props, required)


class SimpleOp:
    def __init__(self, op_id, method, path, params, props, required):
        self.id, self.method, self.path, self.params, self.props, self.required = op_id, method, path, params, props, required

    @property
    def confirm(self) -> bool:
        return self.method == "DELETE" or self.id in CONFIRMED_POSTS

    def option(self, param) -> str:
        if param["in"] == "header":
            return "--idempotency-key"
        return "--" + param["name"].replace("_", "-")


OPS = list(operations())


def command_tree() -> dict:
    """``{operationId: (cli words, click command)}`` read from each command's help."""
    found: dict = {}

    def walk(cmd, words):
        if hasattr(cmd, "commands"):  # a group (Typer may vendor its own click)
            for name, sub in cmd.commands.items():
                walk(sub, [*words, name])
            return
        match = re.search(r"API: (\w+) (\S+) \((\w+)\)\.", cmd.help or "")
        assert match, f"`ibee {' '.join(words)}` names no operation"
        assert match.group(3) not in found, f"{match.group(3)} has two commands"
        found[match.group(3)] = (words, cmd)

    walk(typer.main.get_command(app), [])
    return found


TREE = command_tree()


def test_one_command_per_operation() -> None:
    assert len(OPS) == 225
    assert CONTRACT["info"]["version"] == "0.6.3"
    assert set(TREE) == {op.id for op in OPS}


@pytest.mark.parametrize("words", REMOVED_COMMANDS)
def test_removed_operations_have_no_command(words: str) -> None:
    result = run(words.split())
    assert result.exit_code == 2


def test_generated_files_match_the_contract() -> None:
    check = subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_commands.py"), "--check"],
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


@pytest.mark.parametrize("op", OPS, ids=lambda op: op.id)
def test_flags_map_one_to_one(op: SimpleOp) -> None:
    words, cmd = TREE[op.id]
    arguments = [p for p in cmd.params if p.param_type_name == "argument"]
    options = {p.opts[0]: p for p in cmd.params if p.param_type_name == "option"}
    path_params = sorted((p for p in op.params if p["in"] == "path"), key=lambda p: op.path.index("{" + p["name"] + "}"))
    assert [a.metavar for a in arguments] == [p["name"].upper() for p in path_params]
    expected = {op.option(p): bool(p.get("required")) and p["in"] != "header"
                for p in op.params if p["in"] in ("query", "header") and p["name"] != "workspace_id"}
    expected.update({"--" + name.replace("_", "-"): name in op.required for name in op.props})
    if op.id in VM_CREATES:
        expected["--billing-catalog"] = False
        expected["--billing-interval"] = False
    if op.confirm:
        expected["--yes"] = False
    assert {name: option.required for name, option in options.items()} == expected


def _value(name: str, schema, where: str):
    """(argv values, value expected on the wire) for one field."""
    k = kind(schema)
    if k == "str":
        return [f"v-{name}"], f"v-{name}"
    if k == "int":
        return ["7"], 7
    if k == "float":
        return ["1.5"], 1.5
    if k == "bool":
        return [], True
    if k.startswith("list:"):
        item = k.split(":")[1]
        values = ["3", "4"] if item == "int" else ["1.5", "2.5"] if item == "float" else [f"a-{name}", f"b-{name}"]
        wire = [int(v) for v in values] if item == "int" else [float(v) for v in values] if item == "float" else values
        return values, wire
    wire = [{"k": name}] if k == "json-array" else {"k": name}
    return [json.dumps(wire)], wire


@pytest.mark.parametrize("op", OPS, ids=lambda op: op.id)
def test_every_field_is_sent_as_given(gw, op: SimpleOp) -> None:
    """Every argument and option reaches the API under its contract name, in one request."""
    gw.default = (418, {"error": "teapot"})
    words, _cmd = TREE[op.id]
    argv, path, query, headers = list(words), op.path, {}, {}
    for p in sorted((p for p in op.params if p["in"] == "path"), key=lambda p: op.path.index("{" + p["name"] + "}")):
        value = "3" if kind(p.get("schema")) == "int" else f"p-{p['name']}"
        argv.append(value)
        path = path.replace("{" + p["name"] + "}", value)
    for p in op.params:
        if p["in"] == "path" or p["name"] == "workspace_id":
            continue
        values, wire = _value(p["name"], p.get("schema"), p["in"])
        flag = op.option(p)
        if values:
            for value in values:
                argv += [flag, value]
        else:
            argv.append(flag)
        if p["in"] == "header":
            headers[p["name"].lower()] = wire
        else:
            query[p["name"]] = [str(v).lower() if isinstance(v, bool) else str(v) for v in (wire if isinstance(wire, list) else [wire])]
    body = {}
    for name, schema in op.props.items():
        values, wire = _value(name, schema, "body")
        flag = "--" + name.replace("_", "-")
        if values:
            for value in values:
                argv += [flag, value]
        else:
            argv.append(flag)
        body[name] = wire
    if op.confirm:
        argv.append("--yes")

    result = run(argv)

    assert result.exit_code == 1, result.output  # the scripted 418
    assert len(gw.calls) == 1, [(c.method, c.path) for c in gw.calls]
    call = gw.calls[0]
    assert (call.method, "/" + call.path) == (op.method, path)
    assert call.params == {"workspace_id": [WS], **query}
    for name, value in headers.items():
        assert call.headers[name] == value
    if op.props:
        assert call.json == body
    assert "teapot" in result.output


@pytest.mark.parametrize("op", [op for op in OPS if op.confirm], ids=lambda op: op.id)
def test_destructive_commands_ask_first(gw, op: SimpleOp) -> None:
    words, cmd = TREE[op.id]
    argv = list(words) + ["x"] * sum(p.param_type_name == "argument" for p in cmd.params)
    for p in cmd.params:
        if p.param_type_name == "option" and p.required:
            argv += [p.opts[0]] if p.is_flag else [p.opts[0], "{}" if p.metavar == "JSON" else "1"]
    result = run(argv, input="n\n")
    assert result.exit_code == 1 and "Aborted" in result.output
    assert gw.calls == []
