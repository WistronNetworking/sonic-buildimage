# Wistron SONiC Build Menu

Open the console in WSL:

```bash
cd /home/otis/kyoto-sai1.17
./build-menu.sh
```

The interactive console offers two build actions: **Incremental build** and
**Clean build**. Incremental build is the interactive default. There is no
separate clean-rebuild Yes/No setting.

```text
1. architecture       ARM64: Marvell Prestera
2. customer           Amazon
3. build action       Incremental build
4. build job          8 concurrent jobs
5. build version      Default: <effective source version>
6. start build        make -> installer -> verify
7. last result        saved logs, checksum, status
0. exit
```

## Choose a Build Action

The action selector has exactly two choices, one per line:

```text
1. Incremental build
2. Clean build
```

**Incremental build** uses the existing customer Build directory directly.
It preserves package and intermediate outputs and skips source synchronization,
configure, and patch changes. It runs Bookworm make followed by the Trixie
installer. Edit `platform/` or `src/` and remove the relevant `.deb` or wheel
inside the displayed **Build** directory, then start this action. Edits made
only in the original Source checkout are not synchronized or compiled.
SONiC dependency and build-flag rules determine what must rebuild; related
packages, Docker images, and rootfs may also rebuild. This action does not
promise to rebuild only manually deleted files or to perform installer-only
assembly. The existing customer workspace must have valid menu ownership,
independent Git metadata, ARM64 architecture, Marvell Prestera platform, and
matching ZTP patch state. Missing or incompatible workspaces stop with an
instruction to choose Clean build.

After those checks, Incremental build treats `.platform` as an unchanged
configuration input by passing `--old-file=.platform` to the make inside each
slave container. A previous configure can rewrite this file with identical
contents and a newer timestamp; that timestamp alone must not invalidate every
retained package, wheel and Docker image. The file and its timestamp are not
modified. Other dependencies keep their normal make behavior: missing products
and newer declared source/dependency files can still rebuild components and
their dependents. Component products are not frozen, and no force-rebuild flag
is added. Use Clean build for global configuration changes or a fresh source
snapshot. Interrupting builds can leave missing products that still need to
be rebuilt; this timestamp handling does not restore deleted files.

**Clean build** refreshes the isolated customer snapshot from the original
Source checkout, including tracked edits, non-ignored untracked files and
initialized submodule changes. It backs up the entire existing `target`,
safely removes generated rootfs and untracked Python build/dist/egg-info
outputs, normalizes patches, then runs Bookworm configure/build and Trixie
configure/installer. Use it for the first build, after refreshing the original
checkout, or whenever a fresh rebuild is required. Cleanup preserves tracked
files and rejects unsafe paths or mounted generated directories. The
original Source checkout is preserved.

Selecting either action sets the complete behavior. Clean build always uses
clean mode; Incremental build always retains outputs. The review shows the
action, output handling, source-refresh state, effective source path, version,
ZTP state, and pinned images before a build starts. The previous final bin is
backed up for every action. Backups are retained under the run directory.

## Keyboard and Terminal

Use Up/Down to navigate and Enter to select. Number keys `1`-`7` and `0`
highlight main-menu rows; Enter activates them. In selectors, number keys
highlight the choice and Enter applies it. Esc cancels a popup, preserving
the current value. `q`, Esc, or Ctrl+D exits the main menu. Ctrl+C cancels a
selection and reopens the console. Job/version editors support cursor keys,
Backspace/Delete, and Ctrl+U to clear. Long review/result cards scroll with
Up/Down or PageUp/PageDown. Enter or `y` starts a reviewed build; Esc or `n`
returns without starting it.

The Wistron Power Stripe banner uses blue `#0B6894`, green `#8ADF4C`, matching
green title/footer bands, a full-height lowercase wordmark, and the selected
bold green i stem. Main-menu labels use uniform blue; values remain in the
default foreground, and the selected row uses a green background. Success,
failure, and warning messages use green, red, and yellow. Paths/checksums keep
the default foreground. The 52-column expanded wordmark appears at 62x24 or
larger; smaller supported terminals use the compact wordmark. The minimum
window size is 48x20. Resizing preserves the current selection and settings.

`NO_COLOR` keeps the arrow-key interface with monochrome styling. Non-TTY
input/output, `TERM=dumb`, or unavailable curses uses a line-separated numeric
menu. Redirected output has no menu color codes. Upstream build output passes
through unchanged. Menu color codes are not saved in logs, JSON or checksums.

```bash
NO_COLOR=1 ./build-menu.sh
```

The terminal returns to normal foreground log output during a build. Ctrl+C
stops this run's process group and labeled slave containers, retaining logs
and failed outputs. Other containers are not stopped. A result card appears
after completion. Exit and reopen the console after updates; allow any active
build to finish before restarting it.

## Settings and Source Information

- **Architecture:** ARM64 builds `sonic-marvell-prestera-arm64.bin`. X86/AMD64
  remains a visible placeholder and cannot start a build.
- **Customer:** Amazon includes ZTP and its optional workaround patches.
  Wistron excludes the runtime ZTP packages, services, configuration and
  programs. Customer workspaces are separate. Clean build normalizes patch
  state; Incremental build validates it without applying or reverting patches.
  Static YANG schemas/test data do not constitute runtime ZTP functionality.
- **Build job:** Controls only `SONIC_BUILD_JOBS`, defaults to `8`, and accepts
  positive integers. Enter alone in its editor restores `8`.
  `SONIC_CONFIG_MAKE_JOBS` is not overridden and follows the source default
  (`nproc` in the current `rules/config`); it is not shown in the interface.
- **Build version:** Choose Default or Custom. Default invokes
  `sonic_get_version` from the effective source's `functions.sh`: the original
  checkout for Clean build, the existing workspace for Incremental build.
  A source Git pull affects Clean build; Incremental build continues to use
  the workspace version. Custom overrides use 1-128 ASCII letters/digits,
  dots, underscores and hyphens, starting with a letter or digit. Blank custom
  input restores Default. Default passes an empty `SONIC_IMAGE_VERSION` to
  preserve native fallback behavior; dirty tagged builds can gain a build-time
  timestamp. Custom-version mismatches fail installer verification.
- **Caches:** Both actions explicitly disable package and version caches.
  Retaining build outputs is separate from those caches. Existing test settings
  remain in force; no test-skip switches are added. Service Docker rebuilds use
  the existing `--no-cache` rules.

The source card groups Source and Build paths together and shows Git branch,
commit plus subject, and Clean/Modified status. Clean build reads metadata
from the original Source checkout. Incremental build reads the actual Build
workspace and labels the card `BUILD WORKSPACE / NO SOURCE SYNC`. Compact
layouts truncate long fields or hide both paths together while preserving Git
information. Detached HEAD and unavailable metadata have explicit labels.

A single background worker refreshes Git and Default version every five
seconds without overlapping queries. Navigation and text entry use cached
values and never query Git/Bash while rendering. Initial/context-switched
values show `Loading...`; failed reads retain the last valid values or show
`unavailable` when none exist. One metadata read binds one source directory,
so quick action/customer changes do not mix fields from two sources. Queries
have ten-second timeouts and are cancelled when leaving the console. The
review requests fresh metadata; the worker stops before entering a build.
The console redraws only when input, settings, window size or metadata changes.

## Workspaces, Outputs and Safety

The default state directory is `/home/otis/kyoto-sai1.17-build-menu`.
Customer workspaces are `workspaces/arm64/amazon` and
`workspaces/arm64/wistron`. The actual paths appear in the console/review.
Each run retains its installer, SHA-256, full/component logs, source manifest,
result JSON and backups in `runs/<run-id>/`.

Clean build snapshots include initialized submodules/nested repositories with
independent Git metadata. Ignored/generated outputs are excluded. Unresolved
merges, uninitialized submodules and unsafe external untracked symlinks stop
the snapshot. Tracked runtime symlinks are preserved without reading their
external targets. Branches, tags and remote/symbolic refs are refreshed from
the local source even if HEAD is unchanged, supporting FRR's remote refs.
Source files or refs changing during a snapshot stop the run for a retry.

Snapshot synchronization removes stale source files and only their obsolete
empty parent directories before copying, supporting directory-to-file changes.
It preserves unrelated/nonempty directories and rejects escaping symlinks or
remaining conflicts. Before ZTP normalization, unchanged files created by old
ZTP patches are removed when absent from source; modified copies are retained
and stop the build. Incremental build does not perform this synchronization.

Bookworm uses `sonic-slave-bookworm-otis:043096ac032`; Trixie uses
`sonic-slave-trixie-otis:cae085605ab`. Missing existing ARM64 images stop the
run. Slaves are never pulled or rebuilt, and image IDs are checked throughout
and after the run. A shared build lock prevents concurrent menu builds; an
active sonic-slave build also blocks a new run.

Success requires every selected stage to exit zero, a new ONIE installer with
valid shell header, payload size and SHA-1, expected ZTP runtime state,
matching custom version when specified, a matching SHA-256 after copying, and
unchanged slave IDs. An embedded ZIP never skips ONIE validation. The enclosing
tar reader is bounded by the checked payload size. Failed installers remain
in the workspace for diagnosis but are not published with a success checksum.
These checks do not constitute DUT installation or hardware validation.

## CLI Compatibility and Advanced Actions

Existing CLI arguments remain supported. Without `--run` or `--dry-run`, the
interactive menu always offers only Incremental build and Clean build. Legacy
standard/normal or installer-only settings open as Incremental build;
standard/clean opens as Clean build. CLI build defaults remain unchanged.

```bash
# Inspect both GUI workflows without copying, cleaning or building
./build-menu.sh --dry-run --flavor amazon --build-action make
./build-menu.sh --dry-run --flavor wistron --build-action standard --mode clean

# Incremental build: changes/deletions must be inside the customer Build path
./build-menu.sh --run --yes --flavor amazon --build-action make --jobs 8

# Clean build: synchronize original Source, back up target, configure and build
./build-menu.sh --run --yes --flavor wistron --build-action standard --mode clean
```

The old **Standard build** with `--mode normal` remains CLI-only for callers
that need to refresh source/configure while retaining target outputs. It can
still rebuild components according to SONiC rules:

```bash
./build-menu.sh --run --yes --build-action standard --mode normal
```

Strict **Installer only** also remains CLI-only. It reuses existing packages,
wheels, Docker archives and first-stage SquashFS, skips synchronization,
configure and Bookworm build, and forces the final bin. A temporary make guard
blocks unfrozen SONiC products; missing required products stop with their path
instead of compiling. Existing products use `--old-file`, with `--always-make`
for installer assembly inside the pinned Trixie slave. The frozen SHA-256
manifest and guard are retained with run logs; workspace control files are
removed afterwards. Reused inputs are checked again before success is
published. Installer assembly can still access repositories and take minutes.

```bash
./build-menu.sh --dry-run --flavor amazon --build-action installer-only
./build-menu.sh --run --yes --flavor amazon --build-action installer-only --build-version Custom.123
```

Combining `--mode clean` with `make` or `installer-only` is rejected before
workspace changes. Results retain `build_action`, `mode`, `effective_source`
and `source_refresh`; standard/clean is displayed as Clean build. Historical
records without an action retain their Standard build interpretation.

Use `--source` and `--state-dir` to select a different checkout and an external
state directory. Python 3.10+, Git, make, patch, Docker and unsquashfs are
required. CLI help and all console-owned messages are English.

## Regression Checks

```bash
python3 -m unittest discover -s tests -v
```

Tests use temporary repositories, simulated make/Docker commands and GNU make
dependency/installer-guard fixtures. They do not build a SONiC image or rebuild
slave containers. Native PTY checks cover navigation, review cancellation,
window resizing and color/NO_COLOR at 110x32, 62x24 and 48x20.
