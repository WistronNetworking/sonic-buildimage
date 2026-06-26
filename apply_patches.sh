#!/bin/bash

# Enable 'set -e' to exit immediately if a command exits with a non-zero status
set -e

PATCH_DIR="wistron_patches"
ZTP_DIR="$PATCH_DIR/ztp_workaround"
ZTP_ACTION="disable"
ZTP_PATCH_FILES=(
    "$PATCH_DIR/1-based_port_mapping/0001-1-based-port-mapping.patch"
    "$ZTP_DIR/0001-set-sfp-port-default-speed-to-10G.patch"
    "$ZTP_DIR/0001-Add-dhcp_l2-dhcpv6_l2-to-copp_cfg.json.patch"
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
            echo "  -z, --ztp, ZTP=yes    Enable 1-based port mapping and ZTP workaround patches"
            echo "  ZTP=no                Revert 1-based port mapping and ZTP workaround patches if applied, and skip them"
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
    if patch -p1 -R --dry-run < "$patch_file" >/dev/null 2>&1; then
        echo "[INFO] SKIP: $patch_file (Already applied)"
        echo "----------------------------------------"
        return 0
    fi
    
    echo "[ACTION] APPLYING: $patch_file"
    # Execute the patch command; trigger the else block if it fails
    if patch -p1 < "$patch_file"; then
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
    if patch -p1 -R --dry-run < "$patch_file" >/dev/null 2>&1; then
        echo "[ACTION] REVERTING: $patch_file"
        if patch -p1 -R < "$patch_file"; then
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

# 2. Handle ZTP patches based on action
if [ "$ZTP_ACTION" == "enable" ]; then
    echo "[INFO] --- ZTP patches enabled ---"
    echo "[INFO] Applying 1-based port mapping and ZTP workaround patches in fixed order..."
    for patch_file in "${ZTP_PATCH_FILES[@]}"; do
        apply_patch_file "$patch_file"
    done
    echo "[INFO] ZTP patch apply finished!"
    exit 0
elif [ "$ZTP_ACTION" == "revert" ]; then
    echo "[INFO] --- Reverting ZTP patches ---"
    echo "[INFO] Reverting 1-based port mapping and ZTP workaround patches in reverse fixed order..."
    for ((i=${#ZTP_PATCH_FILES[@]}-1; i>=0; i--)); do
        revert_patch_file "${ZTP_PATCH_FILES[$i]}"
    done
    echo "[INFO] ZTP patch revert finished!"
    exit 0
else
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

echo "[INFO] Patch execution finished!"
