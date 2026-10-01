# Jupyter Deploy

Jupyter deploy is an open-source command line interface tool (CLI) to deploy and manage interactive applications such as **JupyterLab** to the Cloud.

The CLI is vendor-neutral, but it provides a default template that uses `terraform` as the infrastructure-as-code engine and AWS as the cloud provider.

**Documentation:** [jupyter-deploy.readthedocs.io](https://jupyter-deploy.readthedocs.io)

## Getting started with the default template

We recommend using [uv](https://docs.astral.sh/uv/) to set up your Python virtual environment.

Install the CLI and the default template:
```bash
uv init . --bare
uv venv
source .venv/bin/activate

uv add "jupyter-deploy[aws,proxy]"
uv add jupyter-deploy-tf-aws-ec2-jupyterlab
```

Create a project directory and let the CLI scaffold it:
```bash
mkdir my-first-deployment && cd my-first-deployment
jd init .
```

At this point, you need valid AWS credentials, plus `terraform`, the AWS CLI and `jq` installed locally.
The CLI checks for these tools and points you to the installation instructions for anything missing.

Deploy your app to AWS and start working:
```bash
jd config
jd up
jd open
```

`jd open` starts a local proxy that connects your browser to your own **JupyterLab** app
running on a dedicated EC2 instance in your AWS account.

The default template supports temporarily turning off your instance to reduce your cloud bill.
```bash
jd host stop

# To restart it later
jd host start

# You may also need to start the containers that run your application
jd server start
```

To delete all the resources, run:
```bash
jd down
```

## Templates
The `jupyter-deploy` CLI interacts with templates: infrastructure-as-code packages
that you can use to create your own project and deploy resources in your own cloud provider account.

Templates are nominally Python packages distributed on `PyPI`. You can install and manage templates in your virtual
environment with `pip` or `uv`. `jupyter-deploy` automatically finds the templates installed in your Python environment.

The default template is [jupyter-deploy-tf-aws-ec2-jupyterlab](https://pypi.org/project/jupyter-deploy-tf-aws-ec2-jupyterlab/):
a single-user **JupyterLab** on an EC2 instance, reached through the local client proxy over pinned
self-signed TLS and authorized by your AWS identity.

Other templates are available, such as:
- [jupyter-deploy-tf-aws-ec2-base](https://pypi.org/project/jupyter-deploy-tf-aws-ec2-base/) for a
multi-user **JupyterLab** served on your own domain
- [jupyter-deploy-tf-aws-eks-oidc](https://pypi.org/project/jupyter-deploy-tf-aws-eks-oidc/) for
multi-tenant workspaces on an EKS cluster

## Installation

Consider creating or activating a virtual environment.

From your `uv` environment:
```bash
uv add jupyter-deploy
```

Or with pip:
```bash
pip install jupyter-deploy
```

The CLI is cloud-provider agnostic, so cloud dependencies come as optional extras:
- `aws`: required by every AWS template
- `k8s`: required by Kubernetes templates such as `jupyter-deploy-tf-aws-eks-oidc`
- `proxy`: installs the local client proxy that `jd open` and `jd proxy` use to reach your deployment

Pick the extras that your template requires, for example `"jupyter-deploy[aws,proxy]"` for the
default template. Each template's PyPI page lists its install command and prerequisites.

## Entry points
From a terminal, run:

```bash
jupyter-deploy --help

# or use the alias
jd --help

# or use the jupyter CLI
jupyter deploy --help
```

## License

The `jupyter-deploy` CLI is licensed under the [MIT License](https://github.com/jupyter-infra/jupyter-deploy/blob/main/libs/jupyter-deploy/LICENSE).
