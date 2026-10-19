#!/usr/bin/env python3
"""CI check: the CLI calls only methods of the installed ``ibee`` SDK, and no raw HTTP.

SDK methods. Every name the CLI uses with the shape of an SDK method (``<verb>_...``,
where the verbs are the first words of the SDK's own method names, e.g. ``list_vpcs``)
must be a method of a resource of ``ibee.Ibee``. Names built at run time are expanded:

* the VM command templates of ``commands/vm_lifecycle.py`` (``_sdk(ctx, spec, "get_{}")``,
  ``verb + "_{}"``), for every ``VmCommandSpec`` (cloud and GPU), on that spec's resource;
* ``getattr(obj, f"create_{layer}load_balancer")``, with the constant values the module
  passes for ``layer`` (call arguments and ``for`` loops).

Attributes of imported modules (``typer``, ``click``, ...) and of parameters typed as
their objects (``ctx: typer.Context``) are not SDK calls. A name that cannot be expanded
fails the check.

Raw HTTP. httpx may be used only to catch its errors (``httpx.TransportError``), no
other HTTP library may be imported, and nothing may reach the SDK's HTTP client
(``httpx_client``, ``_client_wrapper``).

Run it against the installed SDK: python scripts/check_sdk_calls.py
"""

import ast
import functools
import importlib.metadata
import pathlib
import sys

from ibee import Ibee

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI = ROOT / "src" / "ibee_cli"
OTHER_HTTP_LIBRARIES = ("requests", "urllib3", "aiohttp", "http.client", "urllib.request", "pycurl")
SDK_INTERNALS = {"httpx_client", "_client_wrapper", "client_wrapper"}


def httpx_error(name: str) -> bool:
    import httpx  # installed with the SDK

    target = getattr(httpx, name, None)
    return isinstance(target, type) and issubclass(target, BaseException)

_client = Ibee(token="ibee_prod_key_check", base_url="https://api.ibee.ai/v1")  # no request is sent
RESOURCES = {
    name: {m for m in dir(getattr(_client, name)) if not m.startswith("_") and callable(getattr(getattr(_client, name), m))}
    for name in dir(type(_client))
    if not name.startswith("_") and isinstance(getattr(type(_client), name), property)
}
SDK_METHODS = set().union(*RESOURCES.values())
VERBS = {name.split("_")[0] for name in SDK_METHODS}


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
        self.tree = ast.parse(path.read_text(), str(path))
        self.imports = {}  # local name -> imported module or object path
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        # Parameters typed as a library object (``ctx: typer.Context``): their attributes are not SDK calls.
        self.library_params = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.arg) and node.annotation is not None:
                origin = self.imports.get(root_name(node.annotation) or "", "")
                if origin and not origin.startswith(("typing", "ibee")):
                    self.library_params.add(node.arg)

    def is_library(self, node) -> bool:
        root = root_name(node)
        origin = self.imports.get(root or "")
        return root in self.library_params or (origin is not None and not origin.startswith("ibee"))

    def values(self, name: str):
        """Constant strings this module gives ``name``: ``for name in (...)`` and call arguments."""
        found = set()
        params = {}  # function name -> parameter names
        for node in ast.walk(self.tree):
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == name:
                if isinstance(node.iter, (ast.Tuple, ast.List)):
                    found |= {e.value for e in node.iter.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params[node.name] = [a.arg for a in node.args.args]
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and name in params.get(node.func.id, []):
                index = params[node.func.id].index(name)
                args = node.args[index:index + 1] + [k.value for k in node.keywords if k.arg == name]
                found |= {a.value for a in args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
        return found

    def expand(self, node):
        """The strings ``node`` can be (constants, ``+`` and f-strings over names with constant values)."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.expand(node.left), self.expand(node.right)
            return {a + b for a in left for b in right} if left and right else set()
        if isinstance(node, ast.Name):
            return self.values(node.id)
        if isinstance(node, ast.JoinedStr):
            results = {""}
            for part in node.values:
                options = {part.value} if isinstance(part, ast.Constant) else self.expand(part.value)
                if not options:
                    return set()
                results = {r + o for r in results for o in options}
            return results
        return set()


@functools.lru_cache(maxsize=None)
def vm_specs():
    """The ``VmCommandSpec(...)`` constants of commands/vm_lifecycle.py, as ``{field: value}``."""
    tree = ast.parse((CLI / "commands" / "vm_lifecycle.py").read_text())
    spec = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "VmCommandSpec")
    fields = [n.target.id for n in spec.body if isinstance(n, ast.AnnAssign)]
    return [
        dict(zip(fields, (a.value for a in n.value.args)))
        for n in tree.body
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name) and n.value.func.id == "VmCommandSpec"
    ]


def check(module: Module):
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

        # SDK methods named as attributes (not of imported modules or library objects).
        if isinstance(node, ast.Attribute) and sdk_shaped(node.attr) and not module.is_library(node.value):
            if node.attr not in SDK_METHODS:
                flag(node, f"{node.attr} is not a method of the installed ibee SDK")

        # SDK methods named at run time.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_sdk" and len(node.args) >= 3:  # vm_lifecycle: _sdk(ctx, spec, template)
                templates = module.expand(node.args[2])
                if not templates:
                    flag(node, "cannot expand this VM command template")
                for template in templates:
                    for spec in vm_specs():
                        name = template.format(spec["fragment"])
                        if name not in RESOURCES.get(spec["resource"], set()):
                            flag(node, f"{spec['resource']}.{name} is not a method of the installed ibee SDK")
            elif node.func.id == "getattr" and len(node.args) >= 2 and not module.is_library(node.args[0]):
                name_node = node.args[1]
                if isinstance(name_node, (ast.Constant, ast.JoinedStr, ast.BinOp)):  # not a plain variable
                    names = module.expand(name_node)
                    if not names:
                        flag(node, "cannot expand this SDK method name")
                    for name in names:
                        if sdk_shaped(name) and name not in SDK_METHODS:
                            flag(node, f"{name} is not a method of the installed ibee SDK")
    return problems


def main() -> int:
    problems = [p for path in sorted(CLI.rglob("*.py")) for p in check(Module(path))]
    for problem in sorted(set(problems)):
        print(problem, file=sys.stderr)
    version = importlib.metadata.version("ibee")
    if problems:
        print(f"The CLI calls outside the installed ibee {version} SDK.", file=sys.stderr)
        return 1
    print(f"The CLI calls only methods of ibee {version} ({len(SDK_METHODS)} methods) and makes no raw HTTP calls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
