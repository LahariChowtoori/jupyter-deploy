# `volume`

Manage the storage volumes of a project and their backups.

**Usage**:

```console
$ jd volume [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: List the storage volumes of this project.
* `show`: Display detailed information about a...
* `status`: Display the status of a storage volume.
* `backup`: Back up one storage volume, or all of them.

## `volume list`

List the storage volumes of this project.

Run either from a project directory that you created with <jd init>;
or pass --path <project-dir>.

**Usage**:

```console
$ jd volume list [OPTIONS]
```

**Options**:

* `-p, --path <path>`: Directory of the project.
* `--json`: Output as JSON.
* `--text`: Output as comma-separated names.
* `--help`: Show this message and exit.

## `volume show`

Display detailed information about a storage volume.

Defaults to the primary volume of the app when you omit --name <volume-name>.

Run either from a project directory that you created with <jd init>;
or pass --path <project-dir>.

**Usage**:

```console
$ jd volume show [OPTIONS]
```

**Options**:

* `--name <str>`: Name of the volume.
* `-p, --path <path>`: Directory of the project.
* `--json`: Output as JSON.
* `--help`: Show this message and exit.

## `volume status`

Display the status of a storage volume.

Defaults to the primary volume of the app when you omit --name <volume-name>.

Run either from a project directory that you created with <jd init>;
or pass --path <project-dir>.

**Usage**:

```console
$ jd volume status [OPTIONS]
```

**Options**:

* `--name <str>`: Name of the volume.
* `-p, --path <path>`: Directory of the project.
* `--help`: Show this message and exit.

## `volume backup`

Back up one storage volume, or all of them.

Backs up the primary volume of the app when you pass neither --name <volume-name> nor --all.

Run either from a project directory that you created with <jd init>;
or pass --path <project-dir>.

**Usage**:

```console
$ jd volume backup [OPTIONS]
```

**Options**:

* `--name <str>`: Name of the volume.
* `--all`: Back up every volume of the project.
* `-p, --path <path>`: Directory of the project.
* `--help`: Show this message and exit.
