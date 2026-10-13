# Build SONiC Switch Images

## Description

Following are the instructions on how to build an ONIE compatible
network operating system (NOS) installer image for Wistron network switches.

## Usage

To build SONiC installer image, run the following commands:

```shell
# Enter the source directory
cd sonic-buildimage

# (Optional) Checkout a specific branch. By default, it uses master branch.
git checkout master-mrvl-prestera

# Execute make init once after cloning the repo,
# or after fetching remote repo with submodule updates
time make init

# Configure ARM64; regular Wistron patches are automatically applied before
# the build containers are prepared. This also runs in the official Azure CI.
time make configure PLATFORM=marvell-prestera PLATFORM_ARCH=arm64

# To also apply the optional ZTP workaround patches:
# time make configure PLATFORM=marvell-prestera PLATFORM_ARCH=arm64 WISTRON_ZTP_PATCHES=y

# Manual patch preparation remains available when needed:
# ./apply_patches.sh
# ./apply_patches.sh -z
# Revert ZTP workaround patches explicitly; normal configure does not revert them:
# ./apply_patches.sh ZTP=no

# Build SONiC image with 3 jobs in parallel.
# Note: You can set this higher, but 3 is a good number for most cases
#       and is well-tested.

time make NOJESSIE=1 NOSTRETCH=1 NOBUSTER=1 NOBULLSEYE=1 SONIC_BUILD_JOBS=8 \
     target/sonic-marvell-prestera-arm64.bin

```

The tracked SAI `1.17.1-2` package and Wistron ES1227/ES2227 platform packages
are used only for ARM64 builds. ARMHF and AMD64 download SAI `1.17.1-13`
from the official Marvell binary repository, matching SONiC 202511.

Run `make init` before the first configure after cloning or updating submodules.
Patch failures stop configure before a build starts. Repeated configure calls
skip patches that are already applied; unresolved local edits must be reviewed
if a patch cannot be applied or recognized as present.
