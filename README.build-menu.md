# Wistron SONiC Build Menu

Start the menu from the source directory in WSL:

```bash
cd /home/otis/kyoto-sai1.17
./build-menu.sh
```

In an interactive WSL terminal, the arrow-key console opens automatically.
Use Up/Down to highlight an option and Enter to open it. The console uses a
Wistron blue-and-lime theme, a block wordmark, configuration cards, and a
highlighted selection. The menu entries remain:

```text
1. architecture
2. customer
3. clean rebuild
4. build job
5. build version
6. start build
7. last result
0. exit
```

Selected settings use a colon before the architecture and clean-rebuild
descriptions. The selected customer shows only its name:

```text
1. architecture       ARM64: Marvell Prestera
2. customer           Amazon
3. clean rebuild      No: retain outputs, no caches
```

The selectors and pre-build review retain their detailed information,
including the customer's ZTP setting. Architecture and clean-rebuild
selector descriptions also use colons, as does the architecture in the
pre-build review. Build behavior is unchanged.

Architecture, customer, and clean-rebuild selectors use Up/Down and Enter.
Esc cancels a popup without changing its value. The number keys `1`-`7`
and `0` select a main-menu row; Enter activates it. `q`, Esc, or Ctrl+D exits
the main menu. Ctrl+C cancels the current selection and reopens the console.
The job and version editors support cursor keys, Backspace/Delete, and
Ctrl+U to clear.

Select `start build` to review the settings and full paths. Enter or `y`
confirms; Esc or `n` returns without starting a build. X86 / AMD64 remains a
visible placeholder and cannot start a build. Long review and result cards
can be scrolled with Up/Down or PageUp/PageDown. The source information card
groups `Source` and `Build` on adjacent lines above the Git fields. Compact
layouts keep the two paths together when there is room and hide both when
space is limited. The card also shows `Git branch`, the short `Git commit`,
and `Source status` from the
original source checkout. `Clean / Modified` reflects tracked changes,
non-ignored untracked files, and submodule changes. A single background reader
refreshes Git metadata and the automatic version every five seconds after
each completed read. Navigation, selectors, and text editors use the cached
values without waiting for Git or Bash. Initial values show `Loading...`;
failed refreshes retain the last valid values, or show `unavailable` if none
have been read. Each source query has a ten-second timeout. The console
redraws only when its selection, settings, size, or displayed metadata changes.
All Git fields also appear in the plain-text summary and pre-build review.
The review requests a new read after any ongoing background read, so it
does not rely on the display cache. The reader and any unfinished query are
stopped before leaving the console or entering the foreground build log.
The commit field includes the HEAD commit's subject after its short hash.
Narrow layouts may truncate or omit the subject; the pre-build review
retains the full title.
Detached checkouts show `detached HEAD (<short commit>)`, and unreadable Git
metadata shows `unavailable`. Compact layouts prioritize Git information
and may combine branch, commit, and status on one line.
Long branches and source paths may be clipped in the main view; the review
shows their full text.

All menu-owned prompts, progress messages, errors, results, and CLI help are
in English. The console restores normal terminal input before a build, then
shows the complete foreground log unchanged. After completion or failure,
the result card opens; Enter or Esc returns to the menu.
During a build, `Ctrl+C` stops this run's make process group and its labeled
slave containers, preserving logs and failed outputs. Other containers are
not stopped.

After updating the menu, exit with `0` and reopen it to load the new code.
Let any active build finish before restarting the menu.

## Terminal Theme and Compatibility

The console uses the selected Power Stripe banner: a blue panel, matching
green title and footer bands, and stripe ornaments. Its palette
uses the supplied Wistron blue `#0B6894` and lime green `#8ADF4C` as the
reference, with nearest 256-color shades. All main-menu labels use the
same blue color and their values use the default foreground, including
the X86 placeholder and start-build item. The
five-row block wordmark uses white strokes and a green lowercase `i` stem.
The selected `i` has a two-column cap and straight stem, separated by one
blank row, with no horizontal foot. The `w` keeps its full five-row height.
The 52-column wordmark is centered inside a nine-row banner, with a blank
row above and below. Unicode block strokes fall back to ASCII `#` when
needed, and colors have basic-color fallbacks. Selected rows use black text
on green; failure and warning states remain red and yellow. Paths and
checksums keep the terminal's default foreground color.

The standard-library `curses` module provides the console. No extra
packages or CLI flags are required. Resize the terminal to at least 48
columns by 20 rows. The full block banner appears at 62 columns by 24 rows
or larger; smaller supported terminals use the compact wordmark.

`NO_COLOR` preserves a monochrome arrow-key console, which still uses
terminal screen-control sequences. Non-TTY input/output, `TERM=dumb`, or
unavailable curses support falls back to the existing numeric menu.
Redirected output uses plain text without menu color codes. CLI automation
with `--run`, `--dry-run`, and `--help` does not open the arrow-key console.

```bash
# Disable the menu's colors explicitly
NO_COLOR=1 ./build-menu.sh
```

Color codes added by the menu are not stored in build logs, JSON, or
checksums. Upstream build output, including any colors it already contains,
passes through unchanged.

## Build Options

- **Architecture:** ARM64 builds the Marvell Prestera ONIE installer. X86 /
  AMD64 is a placeholder and cannot start a build.
- **Customer:** Amazon uses `ENABLE_ZTP=y` and applies the regular Wistron
  patches and optional ZTP workaround stack. Wistron uses `ENABLE_ZTP=n`,
  reverts the ZTP workaround stack, and excludes ZTP packages, runtime
  services, configuration, and programs from the installer. Other source,
  SAI, and platform settings are the same.
- **Clean rebuild:** Choose `Yes` or `No` with Up/Down and Enter. The `y/n` keys
  also select a value, case-insensitively, followed by Enter to apply.
  Enter on the current choice keeps it. In the numeric fallback, Enter
  alone at the `Yes/No` prompt keeps the current value. The numeric fallback
  accepts `yes/no` or `y/n`, case-insensitively. `Yes` backs up the old target,
  safely clears generated
  rootfs and untracked Python build/dist/egg-info directories, and rebuilds.
  `No` retains target and intermediate outputs and runs the existing SONiC
  build rules. Packages may still all rebuild; this is not a package-cache
  mode and does not promise to rebuild only manually deleted packages.
- **Build job:** Controls only `SONIC_BUILD_JOBS`. The default is `8`, and
  the value must be a positive integer. Enter alone resets it to `8`.
- **Build version:** Choose `Default: <source version>` to remove the
  custom override, or `Custom: enter a version` to open the editor. The
  selector supports Up/Down, number keys, and `d/c` shortcuts followed by
  Enter. Esc cancels without changing the version. The numeric fallback
  offers the same choices plus `0. back`; Enter alone keeps the setting.
  Default uses `sonic_get_version` from `functions.sh` in the current
  source checkout, so it follows source updates after a Git pull.
  Entering a blank custom version also restores Default. The menu shows
  the current default version as a preview; tagged dirty builds may receive
  a build-time timestamp suffix.
  Custom versions allow 1-128 ASCII letters/digits, dots, underscores, and
  hyphens, starting with a letter or digit. All four stages pass
  `SONIC_IMAGE_VERSION`; Default passes an empty value to retain the native
  fallback. The original and snapshot `functions.sh` files are preserved.
  The selected override is recorded in the run result, and the installer
  version is read from `etc/sonic/sonic_version.yml`. A custom-version
  mismatch fails verification.
- Both clean-rebuild values explicitly set `SONIC_DPKG_CACHE_METHOD=none` and
  `SONIC_VERSION_CACHE_METHOD=none`. No additional test-skip options are set.

Defaults remain ARM64, Amazon, clean rebuild `No`, build job `8`, and
automatic build version.
`SONIC_CONFIG_MAKE_JOBS` is not overridden; it follows the source
configuration, currently `$(shell nproc)` in `rules/config`. It is not
shown or configurable in the menu.

The generic YANG package may contain static ZTP schemas and test data. These
do not provide runtime ZTP functionality and are recorded separately during
installer validation.

## Source and Outputs

The snapshot includes current tracked-file modifications, untracked files
that are not ignored by Git, and initialized submodules or nested
repositories. Ignored files and generated target/rootfs/Python build
outputs are excluded. Source merge conflicts, uninitialized submodules,
and untracked external symlinks stop the build. Tracked runtime symlinks are
preserved as links without reading their external targets.

Amazon and Wistron build in separate directories outside the source
checkout. Resets, cleanup, and patches operate on those snapshots. Each
repository has independent Git metadata, with no `.git` link back to the
original source. Branches, tags, remote refs, and symbolic refs are refreshed
from the local source even when HEAD is unchanged, so packages such as FRR
can use `origin/<branch>`. Source files or refs changing during the snapshot
stop the build and require a retry.

The default state directory is `/home/otis/kyoto-sai1.17-build-menu`.
Customer workspaces are `workspaces/arm64/amazon` and
`workspaces/arm64/wistron`. The actual paths are shown before starting.
Each run stores its installer, checksum, full and component logs, source
manifest, result JSON, and backups in `runs/<run-id>/`. Backups are retained.

Starting another build refreshes that customer's source snapshot.
Synchronization removes stale files and their empty parent directories
before copying current source, allowing a directory to become a file.
Unrelated directories, Git metadata, and nonempty generated directories are
preserved; a remaining file/directory conflict stops the build safely.

Before normalizing ZTP, source refresh also removes files left by earlier
ZTP patches when they are absent from the current source and exactly match
the patch-created content. Modified files are preserved and stop the build;
source-owned files and unrelated generated outputs are retained.

To manually remove package outputs, use the customer build directory displayed
by the menu. Removing outputs from the original checkout's target does not
affect the separate build workspace.

## Existing sonic-slave Images

Bookworm uses `sonic-slave-bookworm-otis:043096ac032`; Trixie uses
`sonic-slave-trixie-otis:cae085605ab`. Missing existing ARM64 images stop the
build. Slave images are never pulled or rebuilt. Each stage specifies its
tag, and image IDs are checked before and after building. Service Docker
images still use the existing `--no-cache` build rules.

## Checks and Automation

Existing CLI arguments are unchanged: `--flavor` selects the customer and
`--mode clean|normal` corresponds to clean rebuild `Yes|No`.

```bash
# Check the environment and print commands without copying, cleaning, or building
./build-menu.sh --dry-run --flavor wistron
./build-menu.sh --dry-run --flavor amazon --mode normal
./build-menu.sh --dry-run --flavor wistron --build-version Wistron_M.1.0.3

# Run with pinned slave images, isolated source, and installer validation
./build-menu.sh --run --yes --flavor wistron --mode clean --jobs 8

# Select another source checkout and an external state directory
./build-menu.sh --source /path/to/sonic-buildimage --state-dir /path/to/build-state
```

Requirements: Python 3.10+, Git, make, patch, Docker, and unsquashfs. Only one
menu build may run for this source at a time. An existing sonic-slave build
also prevents a new build from starting.

Success requires all four build stages to exit with code 0, a new ONIE
installer, a valid shell header, payload size and SHA-1 checksum, and the
expected ZTP package and runtime-file state
inside the final squashfs, matching SHA-256 after copying, and unchanged
slave image IDs. These checks do not constitute DUT installation or hardware
functional validation.

The embedded ZIP never bypasses ONIE validation. Its enclosing tar is read
only within the checksummed payload size. An invalid installer is retained
in the build workspace for diagnosis, but no downloadable installer or
SHA-256 file is published for the failed run.
