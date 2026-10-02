# Getting Started

`jupyter-deploy` is an open-source CLI tool to deploy Jupyter and interactive applications to the Cloud.

## Setup

### Prerequisites
- Python 3.12+

### Installation

We recommend using [uv](https://github.com/astral-sh/uv) for dependency management.

```bash
# prepare your virtual environment
uv init . --bare
uv venv
source .venv/bin/activate

# install jupyter-deploy and the default template
uv add "jupyter-deploy[aws,proxy]"
uv add jupyter-deploy-tf-aws-ec2-jupyterlab
```

Or with `pip`:
```bash
pip install "jupyter-deploy[aws,proxy]"
pip install jupyter-deploy-tf-aws-ec2-jupyterlab
```

Verify installation:
```bash
jd --help

# recommended: install auto-completion
jd --install-completion
```

## Quick Start

### Prerequisites for the default template

The default template is the [**AWS EC2 JupyterLab Template**](../templates/aws-ec2-jupyterlab-template/index).
It needs only:
- An AWS account, with local credentials for an IAM role or IAM user
- `terraform`, the AWS CLI and `jq` installed locally; `jupyter-deploy` checks for these tools and
  points you to the installation instructions for anything missing

```{note}
Before v0.8.0, the default template was the [**AWS Base Template**](../templates/aws-base-template/index).
To keep using it as your default, run `jd preferences set --default-template aws:ec2:base`.
See [`jd preferences`](../concepts/preferences) for details.
```

### 1. Initialize a new project

```bash
mkdir my-first-deployment && cd my-first-deployment
jd init .
```

`jupyter-deploy` will scaffold your project in your local directory. You'll see something like:

```
my-first-deployment/
├── manifest.yaml       # Declares template metadata and provider commands
├── variables.yaml      # Variable definitions and configuration presets
├── AGENT.md            # Template-specific instructions for AI assistants
├── TROUBLESHOOT.md     # How to investigate and resolve common issues
├── .gitignore
├── engine/             # Infrastructure-as-code files (e.g., Terraform .tf files)
└── services/           # Application service definitions and configurations
```

### 2. Configure your project

The next step is to configure your project by setting the values of the variables.

```bash
jd config
```

The default template has no required variables, so `jd config` uses the template defaults
without prompting. To change a default such as the region or instance type, pass it as a flag
(for example `jd config --instance-type t3.large`) or edit the `overrides:` section of the
`variables.yaml` file.

You can view details about all variables with:
```bash
jd config --help
```

You can describe a specific variable with:
```bash
jd show -v <VARIABLE-NAME> --description
```

### 3. Deploy

```bash
jd up
```

`jupyter-deploy` creates the resources in your AWS account using `terraform`.

### 4. Open your application

```bash
jd open
```

`jd open` starts a local proxy that connects your browser to your own **JupyterLab** app
running on a dedicated EC2 instance in your AWS account.

## What's Next

- Explore the [**AWS EC2 JupyterLab Template**](../templates/aws-ec2-jupyterlab-template/index) for single-user deployments with AWS credentials as the only prerequisite
- Explore the [**AWS Base Template**](../templates/aws-base-template/index) for multi-user **JupyterLab** served on your own domain
- Explore the [**AWS EKS OIDC Template**](../templates/aws-eks-oidc-template/index) for multi-user workspace platforms
- Learn about the [**CLI Reference**](../reference/overview) available
- Read the [**Contributor Guide**](../contributor-guide/index) to get involved
