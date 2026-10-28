#!/usr/bin/env python3
"""CI check: the CLI and the installed ``ibee`` SDK cover the same operations, with no raw HTTP.

1. Every SDK call the CLI makes (``client.<resource>.<method>``, and any name with the
   shape of an SDK method) is a method of the installed SDK's generated resources.
2. Every method of those resources is called by the CLI: each SDK method (one per
   contract operation) has a command.
3. No raw HTTP: httpx may be used only for its exception classes, no other HTTP library
   may be imported, and nothing may reach the SDK's HTTP client (``httpx_client``,
   ``_client_wrapper``).

The SDK's methods are read from its generated client (``ibee.base_client.BaseIbee``),
so a hand layer on top of it cannot add or hide one.

Run it against the installed SDK: python scripts/check_sdk_calls.py
"""

from __future__ import annotations

import ast
import importlib.metadata
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "ibee_cli"
OTHER_HTTP_LIBRARIES = ("requests", "urllib3", "aiohttp", "http.client", "urllib.request", "pycurl")
SDK_INTERNALS = {"httpx_client", "_client_wrapper", "client_wrapper"}


def sdk_resources() -> dict:
    """``{resource: {method, ...}}`` of the installed SDK's generated client (no request is sent)."""
    try:
        from ibee.base_client import BaseIbee as Client

        client = Client(base_url="https://api.ibee.ai/v1", token="check")
    except ImportError:  # pragma: no cover - an SDK without a separate generated client
        from ibee import Ibee as Client

        client = Client(token="ibee_prod_key_check", base_url="https://api.ibee.ai/v1")
    resources = {}
    for name in dir(type(client)):
        if name.startswith("_") or not isinstance(getattr(type(client), name), property):
            continue
        resource = type(getattr(client, name))
        resources[name] = {
            m for m in dir(resource) if not m.startswith("_") and m != "with_raw_response" and callable(getattr(resource, m))
        }
    return resources


RESOURCES = sdk_resources()
SDK_METHODS = set().union(*RESOURCES.values())
VERBS = {name.split("_")[0] for name in SDK_METHODS}


def httpx_error(name: str) -> bool:
    import httpx  # installed with the SDK

    target = getattr(httpx, name, None)
    return isinstance(target, type) and issubclass(target, BaseException)


def sdk_shaped(name: str) -> bool:
    return "_" in name and not name.startswith("_") and name.split("_")[0] in VERBS


def root_name(node):
    while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


class Module:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.rel = path.relative_to(ROOT)
        self.tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        self.imports = {}  # local name -> imported module or object path
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                # Relative imports are the CLI's own modules (``from . import context``).
                prefix = "." * node.level + (node.module or "")
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = f"{prefix}.{alias.name}".lstrip(".") if node.level == 0 else f"{prefix}.{alias.name}"
        # Library objects: parameters typed as one (``ctx: typer.Context``) and names assigned
        # from one (``app = typer.Typer(...)``, ``table = Table()``); their attributes are not SDK calls.
        self.library_params = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.arg) and node.annotation is not None:
                origin = self.imports.get(root_name(node.annotation) or "", "")
                if origin and not origin.startswith(("typing", "ibee")):
                    self.library_params.add(node.arg)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                origin = self.imports.get(root_name(node.value.func) or "", "")
                if origin and not origin.startswith(("ibee", ".")):
                    self.library_params.update(t.id for t in node.targets if isinstance(t, ast.Name))

    def is_library(self, node) -> bool:
        root = root_name(node)
        origin = self.imports.get(root or "")
        return root in self.library_params or (origin is not None and not origin.startswith("ibee"))


def check(module: Module, called: set) -> list:
    problems = []

    def flag(node, message):
        problems.append(f"{module.rel}:{node.lineno}: {message}")

    for node in ast.walk(module.tree):
        # Raw HTTP: httpx only for its exception classes, no other HTTP library, and never
        # the SDK's HTTP client.
        if isinstance(node, ast.Attribute) and node.attr in SDK_INTERNALS:
            flag(node, f"{node.attr}: raw HTTP through the SDK's HTTP client")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                if any(name == lib or name.startswith(lib + ".") for lib in OTHER_HTTP_LIBRARIES):
                    flag(node, f"import {name}: raw HTTP")
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "httpx":
                for alias in node.names:
                    if not httpx_error(alias.name):
                        flag(node, f"from httpx import {alias.name}: raw HTTP")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if module.imports.get(node.value.id) == "httpx" and not httpx_error(node.attr):
                flag(node, f"{node.value.id}.{node.attr}: raw HTTP")

        # SDK calls must be written out (``client.<resource>.<method>``): no getattr with a
        # name built at run time or an SDK method's name.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            name = node.args[1] if len(node.args) > 1 else None
            constant = isinstance(name, ast.Constant) and isinstance(name.value, str)
            if node.args and not module.is_library(node.args[0]) and (not constant or sdk_shaped(name.value)):
                flag(node, "getattr: write SDK calls out as client.<resource>.<method>")
        if not isinstance(node, ast.Attribute) or module.is_library(node.value):
            continue
        # ``client.<resource>.<method>``: the method must be one of that resource's.
        if isinstance(node.value, ast.Attribute) and node.value.attr in RESOURCES:
            resource = node.value.attr
            if node.attr in RESOURCES[resource]:
                called.add((resource, node.attr))
            else:
                flag(node, f"{resource}.{node.attr} is not a method of the installed ibee SDK")
        # Any other name with the shape of an SDK method must exist too.
        elif sdk_shaped(node.attr) and node.attr not in SDK_METHODS:
            flag(node, f"{node.attr} is not a method of the installed ibee SDK")
    return problems


def main() -> int:
    called: set = set()
    problems = [p for path in sorted(CLI.rglob("*.py")) for p in check(Module(path), called)]
    expected = {(resource, method) for resource, methods in RESOURCES.items() for method in methods}
    for resource, method in sorted(expected - called):
        problems.append(f"{resource}.{method}: no command calls this SDK method")
    for problem in sorted(set(problems)):
        print(problem, file=sys.stderr)
    version = importlib.metadata.version("ibee")
    if problems:
        print(f"The CLI and the installed ibee {version} SDK do not match.", file=sys.stderr)
        return 1
    print(
        f"The CLI calls every method of ibee {version} ({len(expected)} methods), only those, "
        "and makes no raw HTTP calls."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
