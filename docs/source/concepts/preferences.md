# Preferences

**Preferences** are your personal defaults for the `jd` commands. When you run a command
without a flag that has a preference, the command uses your preference instead of the
built-in default.

Preferences belong to you, not to a project: they apply to every project you create or manage
from your machine, and they never change a project you already configured.

## Available preferences

| Preference | Used when | Built-in default |
|---|---|---|
| `default-template` | `jd init` runs without `--template` | `aws:ec2:jupyterlab` |
| `default-store-type` | `jd projects` commands and `jd init --restore-project` run without `--store-type` | none: you must pass `--store-type` |

## How a value is chosen

A command resolves each setting in this order:

1. the **flag** you pass on the command line, for example `--template`;
2. your **preference**, if you set one;
3. the **built-in default**.

When `jd init` uses a template you did not pass explicitly, it prints which template it used,
and whether it came from your preferences or from the built-in default.

## Managing preferences

```bash
# list each preference, its value, and where the value comes from
jd preferences show

# set one or more preferences
jd preferences set --default-template aws:eks:oidc
jd preferences set --default-store-type s3-only

# clear a preference, reverting to the built-in default
jd preferences unset --default-template

# clear every preference
jd preferences unset --all
```

`--default-template` takes a full template name, in the form
`<provider>:<infrastructure>:<template>`. You can set a template you have not installed yet;
`jd preferences set` warns you, and records it anyway.

For example, to keep using the AWS Base Template, the default before v0.8.0:

```bash
jd preferences set --default-template aws:ec2:base
```

## The preferences file

`jupyter-deploy` stores your preferences in `~/.jupyter-deploy/preferences.yaml`. The
`jd preferences` commands manage it for you, but you may also edit it by hand:

```yaml
schema_version: 1
default-template: aws:ec2:base
default-store-type: s3-only
```

```{note}
If the file cannot be read or contains an invalid value, the commands that read it fail with
the path of the file. Fix the file, or run `jd preferences unset --all` to delete it.
```

See the [`jd preferences`](../reference/setup/preferences) reference for every option.
