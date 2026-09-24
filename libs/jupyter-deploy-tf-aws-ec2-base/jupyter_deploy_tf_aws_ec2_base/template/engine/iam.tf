# IAM Policy Modifications
#
# This file contains IAM policies and policy attachments that modify IAM resources
# created by various modules. These modifications are placed here rather than in the
# modules themselves to avoid circular dependencies.
#
# As the template evolves, additional IAM policy modifications for other resources
# (e.g., secrets manager, cloudwatch, EFS) may be added to this file when they face
# similar circular dependency constraints.
#
# ================================================================================
# EC2 IAM ROLE - S3 BUCKET ACCESS
# ================================================================================
#
# The S3 bucket access policy cannot be added inside the ec2_iam_role module because
# of this dependency chain:
#   1. ec2_iam_role module creates the IAM role
#   2. ec2_instance module depends on ec2_iam_role (needs instance_profile_name)
#   3. volumes module depends on ec2_instance (needs instance_id, availability_zone)
#   4. s3_bucket module depends on volumes indirectly through local.all_script_files
#      - local.all_script_files includes docker-compose.yml
#      - docker-compose.yml template needs module.volumes.resolved_ebs_mounts
#      - docker-compose.yml template needs module.volumes.resolved_efs_mounts

data "aws_iam_policy_document" "deployment_bucket_s3_access" {
  statement {
    sid = "S3ReadDeploymentBucket"
    actions = [
      "s3:GetObject",
      "s3:ListBucket"
    ]
    resources = [
      module.s3_bucket.bucket_arn,
      "${module.s3_bucket.bucket_arn}/*"
    ]
  }
}

resource "aws_iam_policy" "deployment_bucket_s3_access" {
  name_prefix = "deployment-bucket-s3-access-"
  tags        = local.combined_tags
  policy      = data.aws_iam_policy_document.deployment_bucket_s3_access.json
}

resource "aws_iam_role_policy_attachment" "deployment_bucket_s3_access" {
  role       = module.ec2_iam_role.execution_role_name
  policy_arn = aws_iam_policy.deployment_bucket_s3_access.arn
}

# ================================================================================
# EC2 IAM ROLE - CERTIFICATES SECRET ACCESS
# ================================================================================
#
# The certificates secret access policy cannot be added inside the ec2_iam_role
# module because of this dependency chain:
#
#   1. ec2_iam_role module creates the IAM role
#   2. ec2_instance module depends on ec2_iam_role (needs instance_profile_name)
#   3. volumes module depends on ec2_instance (needs instance_id, availability_zone)
#   4. s3_bucket module depends on volumes indirectly through local.all_script_files
#      - local.all_script_files includes docker-compose.yml
#      - docker-compose.yml template needs module.volumes.resolved_ebs_mounts
#      - docker-compose.yml template needs module.volumes.resolved_efs_mounts
#   5. certs_secret module is instantiated after the volumes module
#
# The EC2 instance needs to read/write TLS certificates to persist them across
# instance replacements (e.g., GPU switches).

data "aws_iam_policy_document" "certs_secret_access" {
  statement {
    sid = "SecretsManagerCertsAccess"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:PutSecretValue",
      "secretsmanager:DescribeSecret"
    ]
    resources = [
      module.certs_secret.secret_arn
    ]
  }
}

resource "aws_iam_policy" "certs_secret_access" {
  name_prefix = "certs-secret-access-"
  tags        = local.combined_tags
  policy      = data.aws_iam_policy_document.certs_secret_access.json
}

resource "aws_iam_role_policy_attachment" "certs_secret_access" {
  role       = module.ec2_iam_role.execution_role_name
  policy_arn = aws_iam_policy.certs_secret_access.arn
}

# ================================================================================
# EC2 IAM ROLE - ACCESS ALLOWLIST PARAMETER
# ================================================================================
#
# Lives here rather than inside the ec2_iam_role module for the same reason as the two policies
# above: the parameter is created in services.tf, which is downstream of the role.
#
# cloud-init reads the allowlist from this parameter on first boot instead of having it interpolated
# into the startup document, which is what lets an allowlist change avoid re-running the whole boot
# path (see services.tf and cloudinit.sh.tftpl). Read-only, and scoped to this deployment's single
# parameter: the instance never writes it, and has no business reading any other.
data "aws_iam_policy_document" "auth_allowlist_ssm_access" {
  statement {
    sid = "SsmAuthAllowlistRead"
    actions = [
      "ssm:GetParameter"
    ]
    resources = [
      aws_ssm_parameter.auth_allowlist.arn
    ]
  }
}

resource "aws_iam_policy" "auth_allowlist_ssm_access" {
  name_prefix = "auth-allowlist-ssm-access-"
  tags        = local.combined_tags
  policy      = data.aws_iam_policy_document.auth_allowlist_ssm_access.json
}

resource "aws_iam_role_policy_attachment" "auth_allowlist_ssm_access" {
  role       = module.ec2_iam_role.execution_role_name
  policy_arn = aws_iam_policy.auth_allowlist_ssm_access.arn
}
