output "jupyter_data_volume_id" {
  description = "ID of the jupyter data volume."
  value       = aws_ebs_volume.jupyter_data.id
}

output "resolved_ebs_mounts" {
  description = "List of resolved EBS mounts."
  value       = local.resolved_ebs_mounts
}

output "resolved_efs_mounts" {
  description = "List of resolved EFS mounts."
  value       = local.resolved_efs_mounts
}

output "persist_ebs_volumes" {
  description = "List of EBS volumes with persist=true."
  value       = local.persist_ebs_volumes
}

output "persist_efs_file_systems" {
  description = "List of EFS file systems with persist=true."
  value       = local.persist_efs_file_systems
}

output "additional_ebs_volumes" {
  description = <<-EOT
    JSON-encoded inventory of the configured additional EBS mounts, for `jd volume`.

    Each entry is {name, mount_point, volume_id}. `name` is the volume identity (empty for a mount
    referenced by id, which this template never recreates and so cannot back up); `volume_id` resolves
    only after apply, so the whole output reads "known after apply" on a first create.

    JSON in a string rather than a structured output because the CLI's output definitions only carry
    `string` and `list(string)`.
  EOT
  value = jsonencode([
    for idx, ebs_mount in var.additional_ebs_mounts : {
      name = lookup(ebs_mount, "name", null) != null ? local.additional_volume_names[idx] : ""
      # The path the user sees in the app, which is what the identity above is derived from.
      mount_point = "/home/jovyan/${ebs_mount["mount_point"]}"
      volume_id = (
        lookup(ebs_mount, "id", null) != null
        ? lookup(ebs_mount, "id", "")
        : aws_ebs_volume.additional_volumes[idx].id
      )
    }
  ])
}

output "additional_efs_volumes" {
  description = <<-EOT
    JSON-encoded inventory of the configured additional EFS mounts, for `jd volume`.

    Same shape as additional_ebs_volumes so one manifest declaration covers both, but EFS is regional:
    a zone change relocates only its mount target, so these are listed for visibility and declare no
    backups map.
  EOT
  value = jsonencode([
    for idx, efs_mount in var.additional_efs_mounts : {
      name        = lookup(efs_mount, "name", null) != null ? local.additional_efs_names[idx] : ""
      mount_point = "/home/jovyan/${efs_mount["mount_point"]}"
      volume_id = (
        lookup(efs_mount, "id", null) != null
        ? lookup(efs_mount, "id", "")
        : aws_efs_file_system.additional_file_systems[idx].id
      )
    }
  ])
}
