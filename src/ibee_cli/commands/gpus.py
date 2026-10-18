"""GPU VM commands (``ibee gpus``)."""

import typer

from .vm_lifecycle import GPU_VM, register_vm_commands

app = typer.Typer(help="GPU VMs", no_args_is_help=True)
register_vm_commands(app, GPU_VM)
