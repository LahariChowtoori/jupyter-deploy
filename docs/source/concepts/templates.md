# Templates

A **template** is a reusable blueprint for a deployment. It targets a specific
application, cloud provider, and infrastructure-as-code engine, and bundles the
infrastructure-as-code files, configuration presets, and deployment logic needed to
stand that deployment up.

You turn a template running deployment by creating a [project](projects) from it.

## Installation and discovery

Templates ship as Python packages, and must be installed into the same virtual environment
as `jupyter-deploy` itself. We recommend a dedicated virtual environment per set of
deployments (for example with [uv](https://github.com/astral-sh/uv) or `venv`), so the
CLI and its templates are isolated from other Python tooling.

At startup, `jupyter-deploy` discovers the installed templates through Python
[entry points](https://packaging.python.org/en/latest/specifications/entry-points/) —
each template package registers itself under the `jupyter_deploy.terraform_templates`
group.

You can install templates from several sources:

- **PyPI (recommended)** — the published packages, with version pinning:

  ```bash
  pip install jupyter-deploy-tf-aws-ec2-jupyterlab
  
  # pin a version
  pip install "jupyter-deploy-tf-aws-ec2-jupyterlab==0.1.1"
  ```

- **GitHub** — install a branch or tag directly from the repository, for pre-release
  or in-development templates.
- **Local wheel or source** — a `.whl` file or a local checkout, useful when
  developing your own template.

Because the template is just an installed package, upgrading it (`pip install -U …`)
picks up the maintainer's changes. Note however that changes only reach an existing
projecy when you carry it into its directory (see [Projects](projects)).

## Manifest

Every template carries a `manifest.yaml` that declares its metadata and the provider
commands backing the `jd` subcommands; for example which output holds the URL that
`jd open` targets. The manifest lets the generic `jd` command surface drive template-specific
infrastructure.

## Official and default templates

Refer to the [Templates](../templates/index) section to find a list of **official templates**,
along with a feature comparison.

`jd init` selects a template from four coordinates — engine (`-E`), provider (`-P`),
infrastructure (`-I`), and template name (`-T`). It defaults to
`terraform` / `aws` / `ec2` / `jupyterlab`.

Running `jd init <PROJECT-DIR>` with no flags therefore selects the
**AWS EC2 JupyterLab Template**, a single-user JupyterLab deployment on Amazon EC2 that needs
only AWS credentials, and `jd init` prints which template it used. Pass the flags to choose
another, for example the EKS OIDC template:

```bash
jd init <PROJECT-DIR> -E terraform -P aws -I eks -T oidc
```

`--template` (`-T`) also accepts the full template name. In that case, pass it on its own:
`jd init` exits with an error if you combine a full template name with `--provider` (`-P`) or
`--infrastructure` (`-I`).

```bash
jd init <PROJECT-DIR> --template aws:eks:oidc
```

```{note}
Before v0.8.0, the default template was the **AWS Base Template** (`aws:ec2:base`). To change
your default, run `jd preferences set --default-template <TEMPLATE>`, for example
`jd preferences set --default-template aws:ec2:base`. See
[`jd preferences`](preferences) for details.
```
