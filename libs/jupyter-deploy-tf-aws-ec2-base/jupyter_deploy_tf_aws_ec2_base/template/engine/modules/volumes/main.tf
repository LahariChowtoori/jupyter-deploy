# Volume identity, as the user sees it in the app file system: `home` for the data volume and
# `home/<mount_point>` for each additional mount. This one string is the key into var.ebs_snapshot_ids,
# the `--name` a user passes to `jd volume`, and the base of the Name tag -- composed HERE, next to the
# lookup that consumes it, so the three cannot drift apart.
#
# Identity is the mount path rather than the mount's `name` field because `mount_point` is required on
# every entry while `name` is optional (name XOR id), so this also covers referenced volumes; because
# mount_point is uniqueness-validated; and because its charset forbids "/", so no additional mount can
# ever collide with the bare `home` identity.
locals {
  home_volume_name = "home"
  additional_volume_names = {
    for idx, ebs_mount in var.additional_ebs_mounts :
    idx => "${local.home_volume_name}/${ebs_mount["mount_point"]}"
  }
  additional_efs_names = {
    for idx, efs_mount in var.additional_efs_mounts :
    idx => "${local.home_volume_name}/${efs_mount["mount_point"]}"
  }

  # Every identity this configuration can restore into. EFS is excluded deliberately: a filesystem is
  # regional and has no snapshot to restore from, so naming one here would be a key that does nothing.
  restorable_volume_names = concat([local.home_volume_name], values(local.additional_volume_names))
  unknown_snapshot_keys   = setsubtract(keys(var.ebs_snapshot_ids), local.restorable_volume_names)

  # Mount points are uniqueness-validated WITHIN additional_ebs_mounts and WITHIN additional_efs_mounts,
  # but nothing validates them across the two. Two mounts at one path shadow each other on the instance
  # and collide on volume identity, so refuse the plan. Cannot be a variable validation: those may only
  # reference their own variable.
  duplicate_mount_points = [
    for mount_point in distinct([for m in var.additional_ebs_mounts : m["mount_point"]]) :
    mount_point
    if contains([for m in var.additional_efs_mounts : m["mount_point"]], mount_point)
  ]
}

# Define EBS volume for the notebook data (will mount on /home/jovyan)
resource "aws_ebs_volume" "jupyter_data" {
  availability_zone = var.availability_zone
  size              = var.volume_size_gb
  type              = var.volume_type
  encrypted         = true
  # Absent key -> null -> an empty volume, which is the fresh-deployment default. try() rather than
  # lookup() because lookup's default would have to be "" and aws_ebs_volume wants null.
  snapshot_id = try(var.ebs_snapshot_ids[local.home_volume_name], null)

  tags = merge(
    var.combined_tags,
    {
      # Console label only, NOT the identity: it carries the deployment postfix, which is unknown when
      # the manifest declares this volume. `jd volume` never reads tags to recover an identity -- the
      # inventory comes from the additional_ebs_volumes output and the manifest's static declaration.
      Name = "jupyter-data-${var.postfix}"
    }
  )

  # Attached HERE because this resource always exists in every configuration -- and because a
  # precondition is evaluated even when the resource itself is a no-op, so a collision introduced
  # without touching the home volume is still caught.
  #
  # `precondition`, NOT `check`: a failing check block is only a WARNING and leaves the plan exiting 0,
  # so `jd config && jd up` would print it and then apply anyway. A precondition fails the plan.
  #
  # Cross-variable input validation, which is why it lives here rather than in a `variable validation`
  # block: it compares additional_ebs_mounts against additional_efs_mounts, and HCL variable validation
  # can only see one variable.
  lifecycle {
    # A key naming nothing is silently IGNORED by the `try(...)` lookups, which yields an empty volume
    # where the caller asked for a restore -- the exact failure a restore is supposed to prevent.
    precondition {
      condition = length(local.unknown_snapshot_keys) == 0
      error_message = format(
        "ebs_snapshot_ids names volumes that do not exist in this configuration: %s. Known volume names: %s.",
        join(", ", local.unknown_snapshot_keys),
        join(", ", local.restorable_volume_names),
      )
    }

    precondition {
      condition = length(local.duplicate_mount_points) == 0
      error_message = format(
        "The same mount_point appears in both additional_ebs_mounts and additional_efs_mounts: %s. Each mount_point must be unique across both lists.",
        join(", ", local.duplicate_mount_points),
      )
    }
  }
}

# Attach the main jupyter data volume to the EC2 instance
resource "aws_volume_attachment" "jupyter_data_attachment" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.jupyter_data.id
  instance_id = var.instance_id
}

# STEP 1: EBS creation or reference
# Create additional EBS volumes when 'name' is specified
resource "aws_ebs_volume" "additional_volumes" {
  for_each = {
    for idx, ebs_mount in var.additional_ebs_mounts :
    idx => ebs_mount if lookup(ebs_mount, "name", null) != null
  }

  availability_zone = var.availability_zone
  size              = try(tonumber(lookup(each.value, "size_gb", "30")), 30)
  type              = lookup(each.value, "type", "gp3")
  encrypted         = true
  snapshot_id       = try(var.ebs_snapshot_ids[local.additional_volume_names[each.key]], null)

  tags = merge(
    var.combined_tags,
    {
      Name = "${lookup(each.value, "name", "")}-${var.postfix}"
    }
  )
}

# Import the referenced EBS volumes when 'id' is specified
data "aws_ebs_volume" "referenced_volumes" {
  for_each = {
    for idx, ebs_mount in var.additional_ebs_mounts :
    idx => lookup(ebs_mount, "id", "") if lookup(ebs_mount, "id", null) != null
  }

  filter {
    name   = "volume-id"
    values = [each.value]
  }
}

# STEP 2: EFS creation or reference
# Create EFS file systems when 'name' is specified
resource "aws_efs_file_system" "additional_file_systems" {
  for_each = {
    for idx, efs_mount in var.additional_efs_mounts :
    idx => efs_mount if lookup(efs_mount, "name", null) != null
  }

  encrypted = true
  tags = merge(
    var.combined_tags,
    {
      Name = "${lookup(each.value, "name", "")}-${var.postfix}"
    }
  )
}

# Import the referenced EFS filesystems when 'id' is specified
data "aws_efs_file_system" "referenced_file_systems" {
  for_each = {
    for idx, efs_mount in var.additional_efs_mounts :
    idx => lookup(efs_mount, "id", "") if lookup(efs_mount, "id", null) != null
  }
  file_system_id = each.value
}

# STEP 3: Generate the volumes mappings
locals {
  # combine created and referenced EBS volumes into a single map
  resolved_ebs_mounts = [
    for idx, ebs_mount in var.additional_ebs_mounts : {
      volume_id   = lookup(ebs_mount, "id", null) != null ? lookup(ebs_mount, "id", "") : aws_ebs_volume.additional_volumes[idx].id
      mount_point = ebs_mount["mount_point"]
      # Starts with /dev/sdg and increments
      # jupyter-data mounts on /dev/sdf, so we start one letter after
      device_name = "/dev/sd${substr("ghijklmnopqrstuvwxyz", idx, 1)}"
    }
  ]
  # combine created and referenced EFS file systems into a single map
  resolved_efs_mounts = [
    for idx, efs_mount in var.additional_efs_mounts : {
      file_system_id = lookup(efs_mount, "id", null) != null ? lookup(efs_mount, "id", "") : aws_efs_file_system.additional_file_systems[idx].id
      mount_point    = efs_mount["mount_point"]
    }
  ]

  # List of EBS volumes with persist=true
  persist_ebs_volumes = [
    for idx, ebs_mount in var.additional_ebs_mounts :
    "aws_ebs_volume.additional_volumes[\"${idx}\"]"
    if lookup(ebs_mount, "persist", "") == "true"
  ]

  # List of EFS file systems with persist=true
  persist_efs_file_systems = [
    for idx, efs_mount in var.additional_efs_mounts :
    "aws_efs_file_system.additional_file_systems[\"${idx}\"]"
    if lookup(efs_mount, "persist", "") == "true"
  ]

  # Removed cloudinit_volumes_script - moved to main.tf
}

# STEP 4: Associate EBS and EFS to the EC2 instance
# first for additional EBS volumes
resource "aws_volume_attachment" "additional_ebs_attachments" {
  for_each = {
    for idx, ebs_mount in local.resolved_ebs_mounts :
    idx => {
      volume_id   = ebs_mount["volume_id"]
      device_name = ebs_mount["device_name"]
    }
  }
  device_name = each.value.device_name
  volume_id   = each.value.volume_id
  instance_id = var.instance_id
}

# Use the security group provided by the network module

# Create mount targets for EFS file systems
resource "aws_efs_mount_target" "additional_efs_targets" {
  for_each = {
    for idx, efs_mount in local.resolved_efs_mounts :
    idx => {
      file_system_id = efs_mount["file_system_id"]
      mount_point    = efs_mount["mount_point"]
    }
  }
  file_system_id  = each.value.file_system_id
  subnet_id       = var.subnet_id
  security_groups = length(var.additional_efs_mounts) > 0 && var.efs_security_group_id != null ? [var.efs_security_group_id] : []
}

# STEP 5: Reap this deployment's volume backups on destroy
#
# `jd volume backup` creates snapshots OUTSIDE terraform state (deliberately: a managed
# aws_ebs_snapshot would be destroyed with the stack, which is the opposite of a backup). Nothing else
# would ever delete them, so teardown does.
#
# NOT best-effort: a delete that fails must fail the destroy. The provisioner runs as the OPERATOR
# (local-exec), so a missing ec2:DeleteSnapshot grant is a likely misconfiguration, and swallowing it
# would leak billable snapshots on every teardown while reporting success. What makes failing safe is
# idempotency, not tolerance -- an already-deleted snapshot is treated as success, so re-running
# `jd down` after fixing a permission converges.
resource "null_resource" "reap_volume_backups" {
  triggers = {
    deployment_id = var.postfix
    region        = var.region
  }

  provisioner "local-exec" {
    when        = destroy
    interpreter = ["/bin/bash", "-c"]
    command     = <<-DOC
      set -uo pipefail

      SNAP_IDS=$(aws ec2 describe-snapshots \
        --owner-ids self \
        --region "${self.triggers.region}" \
        --filters "Name=tag:DeploymentId,Values=${self.triggers.deployment_id}" \
        "Name=tag:Source,Values=jupyter-deploy" \
        --query 'Snapshots[].SnapshotId' \
        --output text 2>&1) || {
        # FAILS the destroy, like a failed delete below: if the backups cannot even be listed, there is no
        # way to tell whether this teardown is leaking billable snapshots, and reporting success while it
        # might be is the one outcome that is never discovered. The raw error is printed rather than
        # classified -- a missing grant then reads as itself, with no code here to keep in sync with IAM.
        #
        # So `ec2:DescribeSnapshots` is a prerequisite for destroying ANY deployment of this template,
        # including one that never took a backup. Deliberate: the alternative tolerates a silent leak.
        #
        # Not gated on `length(var.ebs_snapshot_ids)`: a destroy provisioner may only read `self`, so the
        # count would have to be a trigger, and changing a trigger REPLACES this resource -- which runs
        # this provisioner. `jd config --restore-volumes` + `jd up` would then reap the very snapshots it
        # is restoring from, mid-apply.
        echo "ERROR: could not list the volume backups of deployment ${self.triggers.deployment_id}, so none were reaped: $SNAP_IDS" >&2
        exit 1
      }

      if [ -z "$SNAP_IDS" ]; then
        echo "No volume backups to reap for deployment ${self.triggers.deployment_id}."
        exit 0
      fi

      FAILED=""
      for snap_id in $SNAP_IDS; do
        OUTPUT=$(aws ec2 delete-snapshot --region "${self.triggers.region}" --snapshot-id "$snap_id" 2>&1)
        RC=$?
        if [ $RC -eq 0 ]; then
          echo "Deleted volume backup $snap_id"
        elif echo "$OUTPUT" | grep -q "InvalidSnapshot.NotFound"; then
          # Already gone -- a concurrent reap or a manual delete. Idempotent, so not a failure.
          echo "Volume backup $snap_id already deleted"
        else
          echo "ERROR: failed to delete volume backup $snap_id: $OUTPUT" >&2
          FAILED="$FAILED $snap_id"
        fi
      done

      if [ -n "$FAILED" ]; then
        echo "ERROR: volume backups left behind:$FAILED" >&2
        exit 1
      fi
    DOC
  }
}
