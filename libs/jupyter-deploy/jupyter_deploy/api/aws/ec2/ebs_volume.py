from __future__ import annotations

from mypy_boto3_ec2.client import EC2Client
from mypy_boto3_ec2.type_defs import FilterTypeDef, VolumeTypeDef

# An EBS volume reports `in-use` while attached to an instance and `available` while detached. Note
# that a *stopped* instance still holds its volumes, so `in-use` says nothing about the instance state.
VOLUME_IN_USE_STATE = "in-use"


def describe_volumes_by_tags(ec2_client: EC2Client, tag_filters: dict[str, str]) -> list[VolumeTypeDef]:
    """Return every EBS volume matching every supplied tag.

    Tag keys are NOT defined here: the template owns the tagging convention and passes it down, which
    is also what lets this work for volumes the template references but does not manage.
    """
    filters: list[FilterTypeDef] = [{"Name": f"tag:{key}", "Values": [value]} for key, value in tag_filters.items()]
    response = ec2_client.describe_volumes(Filters=filters)
    return list(response.get("Volumes", []))
