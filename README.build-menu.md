# Wistron SONiC Build Menu

Requires Python 3.8 or newer on the Linux/WSL build host. The launcher uses
the `python3` interpreter on PATH; older versions stop with an upgrade message.

Run `./build-menu.sh` in the SONiC Git repository, or invoke its absolute path
from another working directory. Opening either console does not start a build.

Each clone permanently binds to one architecture and one customer when Start
is confirmed. The first build starts with **Clean**. After cleanup completes, failed or
interrupted builds default to **Incremental** and retain existing products.
Both architecture and customer
rows then display **Locked**; Enter explains how to clone another repository.
There is no reset or customer-switch operation. AMD64 remains a visible,
unsupported option and cannot start a build.

On a fresh clone, confirmed builds initialize missing common-source and
Marvell Prestera submodules before recording the source manifest or running
configure. This may download Git sources. Other platforms can remain
uninitialized. Existing submodule HEADs and local edits are preserved; the
menu does not update those checkouts. Dry-run lists missing modules without
initializing them.

The source and build directory are the same repository:

```text
kyoto-sai1.17-fresh/
  .git/
  src/  platform/  rules/
  build-menu.sh
  target/
  runs/<run-id>/
  .build-menu/
```

The ignored `.build-menu/profile.json` stores the immutable binding. It is
written atomically before preflight and survives failed builds. A new clone
does not inherit it. The ignored `.build-menu/progress.json` records successful
cleanup and each configure stage separately; interrupted cleanup must be retried.
Existing run records and legacy configuration records provide compatible resume
evidence. Read-only startup and dry-run never write these checkpoints; adoption
happens under the repository build lock when starting the next build.
Invalid profile, configuration or checkpoint data stops the menu.

## Actions

- **Clean build** retains source files, staged/unstaged changes and untracked
  source. It preserves existing BIN files with SHA-256 checksums and historical
  run logs, then removes explicitly identified generated outputs, including
  target packages and rootfs directories. It does not archive the whole target
  directory. An identical verified historical BIN can be referenced directly.
- **Incremental build** retains outputs and builds this repository directly.
  It is available as soon as the initial cleanup completes. Before compiling,
  it reapplies the current regular patches and customer-specific ZTP patches,
  verifies their ZTP state, and finishes any missing Bookworm or Trixie configure
  stages. Completed configure stages are retained. Patch preparation preserves
  configuration contents and timestamps; conflicts stop before existing products
  are removed or compilation starts. Architecture and platform must match the binding.
  Configure, package and installer failures retain completed products for retry.
  Only explicitly choosing Clean removes them again.
  Missing distro output directories are recreated; configuration files and their
  timestamps are preserved.

Builds use this checkout directly, without a separate customer source copy or
snapshot synchronization. Missing submodules download their pinned sources;
initialized submodule checkouts are not fetched or reset. Incremental prepares
the current patch stack on every run, including after source or overlay updates.
Already applied patches are skipped, missing patches are applied forward, and conflicts stop
with logs. No patches are reversed to switch customers.

The arrow-key and numeric consoles retain architecture, customer, action,
jobs, version, Start and last-result rows. Jobs and version remain editable.
The version defaults to `functions.sh`; custom versions reach both stages.

## CLI

```bash
./build-menu.sh --dry-run
./build-menu.sh --mode clean --run --yes
./build-menu.sh --build-action make --run --yes
./build-menu.sh --source /path/to/another-clone --dry-run
./build-menu.sh --state-dir /path/to/records --dry-run
```

`--run --yes` confirms Start and permanently binds a fresh clone.
`--dry-run` validates and prints commands without creating a binding, run,
build directory or source copy. Its command list includes Incremental patch
preparation without executing it. Explicit architecture/customer options that
conflict with the target clone's profile are rejected.

`--source` selects a Git repository root and uses that repository's binding.
`--state-dir` changes only the location of `runs/<run-id>/` logs, manifests,
BIN files and backups. Profile, readiness record, last-result pointer and build
lock always stay in the target repository's `.build-menu/`.

Advanced `--build-action installer-only` requires valid configuration and an
existing first-stage rootfs. Its GNU Make guard freezes existing package and
Docker inputs and rejects missing products rather than compiling components.

## Build safeguards and results

By default, Bookworm and Trixie builders are selected by SONiC's native
`Makefile.work` rules using the host architecture, current user, checkout
path and generated build inputs. Missing builders are prepared by the normal
build process; no developer-specific image name or tag is required. The
native rules also handle ARM64 builds on supported non-ARM64 hosts. Host
prerequisites and any required emulation must be installed as for a normal
SONiC build. Confirmed builds may download base images and build dependencies.
Dry-run does not invoke Makefile.work, pull images or build builders.

For a machine with prepared ARM64 slave images, explicitly pin either distro:

```bash
./build-menu.sh --bookworm-image registry.example.com/sonic-bookworm:user-tag \
                --trixie-image registry.example.com/sonic-trixie:user-tag --dry-run
```

Each supplied image must already exist locally and use `repository:tag` syntax.
Its ID is checked throughout the run, and builder rebuilds and pulls for that
distro are disabled. An unspecified distro continues using native selection.
These options apply to the current invocation; they do not change the clone's
architecture/customer binding. The menus and run records show the selection.

Clean uses the supplied Bookworm image when pinned. Otherwise its cleanup
helper uses the multi-architecture `debian:bookworm` image, pulled if missing
only during a confirmed Clean. Source protection and mount checks still apply.
Package/version caches remain disabled. Installer checks validate the ONIE
payload, ZTP package/runtime paths and requested version before publishing a
BIN and SHA-256 sidecar. Unchanged-image checks apply only to explicit pins.
Published files use `sonic-marvell-prestera-arm64-<customer>-<version>.bin`,
for example `sonic-marvell-prestera-arm64-amazon-Wistron_M.1.0.2.bin`.
The filename uses the verified image's `build_version`, whether default or
custom, with the same character and length limits as custom versions.
The `.bin.sha256` sidecar names that BIN, and result records and the console
show its published path. Make's `target/sonic-marvell-prestera-arm64.bin`
and existing historical files and records retain their names.
Ctrl+C stops only the current run's labelled containers and saves diagnostics.

Migration archives live in `.build-menu/migrations/`. Old customer source
differences are archived for recovery and are not merged into the PR checkout.
The migrated ARM64/Amazon checkout requires a first Clean build.
