"""Cloud VM commands (``ibee vms``)."""

import typer

from .vm_lifecycle import CLOUD_VM, register_vm_commands

app = typer.Typer(help="Cloud VMs", no_args_is_help=True)
register_vm_commands(app, CLOUD_VM)
