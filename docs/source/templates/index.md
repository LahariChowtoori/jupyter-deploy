# Templates

A **template** defines what `jupyter-deploy` deploys and how — it targets a specific
application, cloud provider, and infrastructure-as-code engine, and bundles everything
needed to stand a deployment up.

For how templates, engines, providers, and the store fit together, see
[Concepts](../concepts/index). This page indexes the official templates and compares
them to help you choose.

## Official Templates

Each official template adds a layer of capability on top of the one before it: start with the
simplest template that fits your needs, and move up as they grow.

| | [AWS EC2 JupyterLab Template](aws-ec2-jupyterlab-template/index) | [AWS Base Template](aws-base-template/index) | [AWS EKS OIDC Template](aws-eks-oidc-template/index) |
|---|---|---|---|
| **Use case** | Move your JupyterLab to the cloud for more compute, storage, memory or GPUs | Everything in the EC2 JupyterLab template, plus collaborating with others online | Run your organization's own internal notebook platform |
| **Architecture** | Single EC2 instance | Single EC2 instance | EKS cluster with managed node groups |
| **Users** | Single user, single app | Small team collaborating on a single app | Multi-user with isolated workspaces |
| **Identity** | AWS IAM (via local proxy) | GitHub OAuth (direct) | GitHub OAuth via Dex (OIDC) |
| **Prerequisites** | AWS credentials only | Domain + GitHub OAuth app | Domain + GitHub OAuth app |
| **Access** | Local proxy, pinned TLS | Public URL on your domain | Public URL on your domain |
| **Scaling** | Vertical (instance type) | Vertical (instance type) | Horizontal (node autoscaling) |

## The Default Template

If you do not specify a template when running `jd init PROJECT-DIR`, `jupyter-deploy` defaults to the **AWS Base Template**.


See the [**AWS Base Template**](aws-base-template/index) for full documentation.
## What's next

```{toctree}
:maxdepth: 1

AWS EC2 JupyterLab Template <aws-ec2-jupyterlab-template/index>
AWS Base Template <aws-base-template/index>
AWS EKS OIDC Template <aws-eks-oidc-template/index>
```