# Prerequisites

## AWS account

The template needs to create AWS resources. Your local environment needs access to valid AWS credentials.

If you do not have an AWS account, follow the [official guide](https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-creating.html) to create one.

If you already have an AWS account, make sure your [CLI credentials are configured](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html).

You must deploy as an **IAM role** (assumed role) or an **IAM user**. The template rejects root and
federated identities at plan time, because access to the application is granted by allowlisting IAM
role and user names.

That is all: unlike the **AWS Base Template**, this template requires no domain, no Route 53 hosted
zone, and no GitHub OAuth app.

## IAM permissions for daily use

Beyond the permissions Terraform needs to create the resources at deploy time, the local
credentials you use day-to-day need:

- `ec2:DescribeInstances`: resolve the instance's current public IP (there is no Elastic IP)
- `ssm:GetParameter`: read the published self-signed certificate (the TLS pin) from SSM Parameter Store
- `ec2:StartInstances` / `ec2:StopInstances`: only for `jd host start` and `jd host stop`

Minting the AWS-identity token is a local presign that makes no API call, so it needs no extra IAM
permission.

## Session Manager plugin (optional)

The interactive `jd host connect` and `jd server connect` commands open an AWS SSM session and
require the [AWS Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
installed locally. It is not needed for `jd up`, `jd open`, `jd proxy`, or the
`jd server logs` / `jd server exec` commands.
