import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from jupyter_deploy import cmd_utils
from jupyter_deploy.cli.error_decorator import handle_cli_errors
from jupyter_deploy.cli.simple_display import SimpleDisplayManager
from jupyter_deploy.handlers.resource import volume_handler

volume_app = typer.Typer(
    help="Manage the storage volumes of a project and their backups.",
    no_args_is_help=True,
)


@volume_app.command(name="list")
def list_volumes(
    project_dir: Annotated[
        Path | None,
        typer.Option("--path", "-p", help="Directory of the project."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
    text_output: Annotated[bool, typer.Option("--text", help="Output as comma-separated names.")] = False,
) -> None:
    """List the storage volumes of this project.

    Run either from a project directory that you created with <jd init>;
    or pass --path <project-dir>.
    """
    console = Console()
    err_console = Console(stderr=True)

    if json_output and text_output:
        err_console.print(":x: Cannot use both --json and --text.", style="red")
        raise typer.Exit(code=1)

    with handle_cli_errors(console), cmd_utils.project_dir(project_dir):
        simple_display_manager = SimpleDisplayManager(console=console)
        handler = volume_handler.VolumeHandler(display_manager=simple_display_manager)

        with simple_display_manager.spinner("Listing volumes..."):
            volumes = handler.list_volumes()

        if json_output:
            console.print(json.dumps([asdict(v) for v in volumes]), highlight=False, markup=False, soft_wrap=True)
            return
        if text_output:
            console.out(",".join(v.name for v in volumes))
            return

        table = Table()
        table.add_column("Name", style="bold cyan")
        table.add_column("Class")
        table.add_column("Description")
        for volume in volumes:
            table.add_row(volume.name, volume.volume_class, volume.description)
        console.print(table)


@volume_app.command()
def show(
    name: Annotated[str | None, typer.Option("--name", help="Name of the volume.")] = None,
    project_dir: Annotated[
        Path | None,
        typer.Option("--path", "-p", help="Directory of the project."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Display detailed information about a storage volume.

    Defaults to the primary volume of the app when you omit --name <volume-name>.

    Run either from a project directory that you created with <jd init>;
    or pass --path <project-dir>.
    """
    console = Console()
    with handle_cli_errors(console), cmd_utils.project_dir(project_dir):
        simple_display_manager = SimpleDisplayManager(console=console)
        handler = volume_handler.VolumeHandler(display_manager=simple_display_manager)

        with simple_display_manager.spinner(f"Retrieving volume {name or 'details'}..."):
            details = handler.show_volume(name)

        if json_output:
            console.print(json.dumps(details.to_dict()), highlight=False, markup=False, soft_wrap=True)
            return

        console.print_json(json.dumps(details.to_dict()))


@volume_app.command()
def status(
    name: Annotated[str | None, typer.Option("--name", help="Name of the volume.")] = None,
    project_dir: Annotated[
        Path | None,
        typer.Option("--path", "-p", help="Directory of the project."),
    ] = None,
) -> None:
    """Display the status of a storage volume.

    Defaults to the primary volume of the app when you omit --name <volume-name>.

    Run either from a project directory that you created with <jd init>;
    or pass --path <project-dir>.
    """
    console = Console()
    with handle_cli_errors(console), cmd_utils.project_dir(project_dir):
        simple_display_manager = SimpleDisplayManager(console=console)
        handler = volume_handler.VolumeHandler(display_manager=simple_display_manager)

        with simple_display_manager.spinner("Retrieving volume status..."):
            volume_status = handler.get_status(name)

        console.print(f"Volume status: [bold cyan]{volume_status}[/]")


@volume_app.command()
def backup(
    name: Annotated[str | None, typer.Option("--name", help="Name of the volume.")] = None,
    all_volumes: Annotated[bool, typer.Option("--all", help="Back up every volume of the project.")] = False,
    project_dir: Annotated[
        Path | None,
        typer.Option("--path", "-p", help="Directory of the project."),
    ] = None,
) -> None:
    """Back up one storage volume, or all of them.

    Backs up the primary volume of the app when you pass neither --name <volume-name> nor --all.

    Run either from a project directory that you created with <jd init>;
    or pass --path <project-dir>.
    """
    console = Console()

    if name and all_volumes:
        console.print(":x: Pass either --name or --all, not both.", style="bold red")
        raise typer.Exit(code=1)

    with handle_cli_errors(console), cmd_utils.project_dir(project_dir):
        simple_display_manager = SimpleDisplayManager(console=console)
        handler = volume_handler.VolumeHandler(display_manager=simple_display_manager)

        with simple_display_manager.spinner("Backing up volumes..."):
            results = handler.backup_all() if all_volumes else [handler.backup_volume(name)]

        for result in results:
            simple_display_manager.success(f"Backed up '{result.name}' to '{result.backup_id}'.")
