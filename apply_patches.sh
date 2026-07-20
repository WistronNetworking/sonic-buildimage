#!/bin/bash

# Enable 'set -e' to exit immediately if a command exits with a non-zero status
set -e

PATCH_DIR="wistron_patches"
ZTP_DIR="$PATCH_DIR/ztp_workaround"
ZTP_ACTION="disable"
# 1-based port mapping, sfp-10G defaults and copp_cfg dhcp_l2 are committed in
# source on this branch; retired to wistron_patches/attic/. Only the submodule-side
# ZTP workarounds remain (sonic-swss / sonic-ztp cannot be pushed upstream).
ZTP_PATCH_FILES=(
    "$ZTP_DIR/0002-Add-dhcp_l2-dhcpv6_l2-to-copp-supported-list.patch"
    "$ZTP_DIR/0002-ztp-workaround-mac-table-added.patch"
)

# Parse arguments for simpler execution
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -z|--ztp|ZTP=yes) ZTP_ACTION="enable" ;;
        ZTP=no) ZTP_ACTION="revert" ;;
        -h|--help) 
            echo "Usage: $0 [OPTIONS]"
            echo "Options:"
            echo "  -z, --ztp, ZTP=yes    Also apply the ZTP workaround patches (after the regular ones)"
            echo "  ZTP=no                Revert ZTP workaround patches if applied, and skip them"
            echo "  -h, --help            Show this help message"
            echo "  (Default: Skip ZTP workaround patches without reverting)"
            exit 0
            ;;
        *) echo "[ERROR] Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

echo "[INFO] Starting to apply patches..."

# 1. Check if the patch directory exists
if [ ! -d "$PATCH_DIR" ]; then
    echo "[ERROR] Patch directory not found: $PATCH_DIR"
    exit 1
fi

# Function to apply a single patch
apply_patch_file() {
    local patch_file="$1"

    if [ ! -f "$patch_file" ]; then
        echo "[ERROR] Patch file not found: $patch_file"
        exit 1
    fi

    # Check if the patch is already applied by doing a dry-run in reverse (-R).
    # If this succeeds, the patch is already present.
    # --force (NOT --batch): --batch answers "Unreversed patch detected!" with
    # "ignore -R" and retries FORWARD, so an unapplied patch that applies cleanly
    # passes this reverse check and gets skipped as "already applied".
    if patch -p1 -R --dry-run --force < "$patch_file" >/dev/null 2>&1; then
        echo "[INFO] SKIP: $patch_file (Already applied)"
        echo "----------------------------------------"
        return 0
    fi

    echo "[ACTION] APPLYING: $patch_file"
    # --batch: never prompt interactively (a partially-merged patch used to hang here)
    # --forward: NEVER auto-reverse; --batch alone answers 'Assume -R?' with yes,
    #            silently UN-applying an already-applied patch
    if patch -p1 --batch --forward < "$patch_file"; then
        echo "[SUCCESS] DONE: $patch_file"
        echo "----------------------------------------"
    else
        echo "[ERROR] FAILED: $patch_file"
        echo "[DEBUG] Please check the patch file contents or resolve conflicts."
        exit 1
    fi
}

# Function to revert a single patch
revert_patch_file() {
    local patch_file="$1"

    if [ ! -f "$patch_file" ]; then
        echo "[ERROR] Patch file not found: $patch_file"
        exit 1
    fi

    # Check if the patch is already applied by doing a dry-run in reverse (-R).
    # If this succeeds, the patch is already present, meaning we can revert it.
    if patch -p1 -R --dry-run --force < "$patch_file" >/dev/null 2>&1; then
        echo "[ACTION] REVERTING: $patch_file"
        if patch -p1 -R --batch < "$patch_file"; then
            echo "[SUCCESS] REVERTED: $patch_file"
            echo "----------------------------------------"
        else
            echo "[ERROR] FAILED TO REVERT: $patch_file"
            exit 1
        fi
    else
        # Not currently applied, so no need to revert
        echo "[INFO] SKIP REVERT: $patch_file (Not currently applied)"
        echo "----------------------------------------"
    fi
}

shopt -s nullglob

# 2. Handle ZTP patch revert first; with -z the ZTP patches are applied AFTER the
# regular ones below (a single '-z' run now covers the full fresh-clone setup).
if [ "$ZTP_ACTION" == "revert" ]; then
    echo "[INFO] --- Reverting ZTP patches ---"
    echo "[INFO] Reverting ZTP workaround patches in reverse fixed order..."
    for ((i=${#ZTP_PATCH_FILES[@]}-1; i>=0; i--)); do
        revert_patch_file "${ZTP_PATCH_FILES[$i]}"
    done
    echo "[INFO] ZTP patch revert finished!"
    exit 0
elif [ "$ZTP_ACTION" != "enable" ]; then
    echo "[INFO] --- ZTP patches disabled (uses default behavior, skipped) ---"
fi

echo "[INFO] --- Applying regular patches ---"
# 3. Find regular patches
patch_files=("$PATCH_DIR"/[0-9][0-9][0-9][0-9]-*.patch)

# 4. Check if there are any files matching the pattern
if [ ${#patch_files[@]} -eq 0 ]; then
    echo "[WARNING] No matching .patch files found in $PATCH_DIR."
else
    # Loop through and apply each regular patch
    for patch_file in "${patch_files[@]}"; do
        apply_patch_file "$patch_file"
    done
fi

# 5. Fixups that patch(1) cannot express: mode-only changes are silently skipped
# (a diff with no content hunks patches nothing). dpkg-buildpackage requires an
# executable debian/rules; the sonic-bmp fork commits it as 0644.
if [ -f src/sonic-bmp/debian/rules ]; then
    chmod +x src/sonic-bmp/debian/rules
fi

# 6. ZTP workaround patches, after the regular ones
if [ "$ZTP_ACTION" == "enable" ]; then
    echo "[INFO] --- Applying ZTP workaround patches ---"
    for patch_file in "${ZTP_PATCH_FILES[@]}"; do
        apply_patch_file "$patch_file"
    done
    echo "[INFO] ZTP patch apply finished!"
fi

echo "[INFO] Patch execution finished!"
