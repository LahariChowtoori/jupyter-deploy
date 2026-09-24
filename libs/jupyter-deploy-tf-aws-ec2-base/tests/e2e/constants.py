"""Shared constants for the base E2E suite: test ordering, and the mutating pass fixtures.

Test Execution Order
====================

1. **Deployment test** (order=1) — verifies the app is reachable the moment `jd up` finishes.
2. **Mutating tests** (order >= 10) — three in-place `jd up` cycles on the SAME deployment.
3. **Non-ordered tests** — run LAST (pytest-order runs positive-ordinal tests first).

The ordinals are load-bearing, not cosmetic: this suite deliberately mutates ONE deployment in
place rather than deploying one project per configuration, because the transitions are the
coverage — terraform replacing the instance under a persisted data volume, the volume reattaching
at boot, the package-manager environment rebuilding, the app returning on the same domain.

Each apply also inherits what the previous one provisioned: apply #1 creates the external mounts
and seeds the flag files that apply #2 asserts survived a replacement. Run them in order.

Each mutating file bundles every change one apply can carry, rather than paying an instance
replacement per variable: four applies cover the whole matrix.
"""

# Deployment test — runs first
ORDER_DEPLOYMENT = 1

# Mutating tests — after the deployment test, before the non-ordered tests
_MUTATING_BASE = 10

# Apply #1: CPU + uv  ->  GPU + pixi + external EBS/EFS mounts + larger log retention
ORDER_MUTATING_GPU_PIXI = _MUTATING_BASE  # 10

# Apply #2: GPU + pixi  ->  CPU + uv (external volumes stay mounted)
ORDER_MUTATING_CPU_UV = _MUTATING_BASE + 10  # 20

# Apply #3: the allowlist-through-variables path (#367). After the swaps because it asserts that an
# allowlist-only apply leaves the app container ALONE, which needs a deployment that has been up and
# serving for a while -- and because a failure here should not cost the swap coverage.
ORDER_MUTATING_AUTH_VARIABLES = _MUTATING_BASE + 20  # 30

# Apply #4: availability-zone swap with volume preservation (test_volume_swaps.py).
#
# LAST of the mutating applies, and unlike the swaps above it is genuinely runnable on its own: its
# fixture provisions the mounts it needs rather than inheriting them from apply #1. It is ordered last
# anyway because it leaves the deployment in a different zone, and because a zone swap is the most
# expensive thing in the suite -- a failure here should not cost the cheaper coverage.
ORDER_MUTATING_VOLUME_SWAPS = _MUTATING_BASE + 30  # 40

# --------------------------------------------------------------------------- mutating pass
# Shared by BOTH swap files: apply #1 provisions the mounts and writes the flags, apply #2
# asserts they survived. They live here rather than in either test module so neither has to import
# the other -- the two applies are ordered peers, not a dependency.

# Flag written to the home volume BEFORE apply #1, so its survival proves the data volume
# reattached to a brand-new instance. The external-volume flags are written after apply #1
# (the volumes do not exist before it) and checked after apply #2.
HOME_FLAG = "e2e_flag_home.txt"
EBS_FLAG = "external-ebs1/e2e_flag_ebs.txt"
EFS_FLAG = "external-efs1/e2e_flag_efs.txt"

# Mount points the applies provision.
EBS_MOUNT = "name=ebs1,mount_point=external-ebs1,size_gb=50"
EFS_MOUNT = "name=efs1,mount_point=external-efs1"

# --------------------------------------------------------------------------- volume identities
# What `jd volume --name` takes: the mount path as the user sees it in the app, NOT the terraform
# resource name or the `name=` field of the mount spec. Derived from the mount points above so a
# renamed mount cannot leave these stale.
#
# `home` is also the DEFAULT --name, because it is the first entry of the manifest's `volumes.static`
# list. Asserted in test_volume.py rather than assumed.
HOME_VOLUME = "home"
EBS_VOLUME = f"{HOME_VOLUME}/external-ebs1"
EFS_VOLUME = f"{HOME_VOLUME}/external-efs1"
