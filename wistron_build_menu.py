#!/usr/bin/env python3
"""Foreground SONiC builds in one permanently bound source checkout."""

import argparse
import dataclasses
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import zipfile


IMAGES = {
    "bookworm": "sonic-slave-bookworm-otis:043096ac032",
    "trixie": "sonic-slave-trixie-otis:cae085605ab",
}
INSTALLER = "sonic-marvell-prestera-arm64.bin"
ACTIONS = {"standard": "Standard build", "make": "Incremental build", "installer-only": "Installer only"}
GUI_ACTIONS = {"make": "Incremental build", "clean": "Clean build"}
RFS = INSTALLER + "__marvell-prestera__rfs.squashfs"
ZTP_PATCHES = [
    "wistron_patches/ztp_workaround/0002-Add-dhcp_l2-dhcpv6_l2-to-copp-supported-list.patch",
    "wistron_patches/ztp_workaround/0002-ztp-workaround-mac-table-added.patch",
    "wistron_patches/ztp_workaround/0003-ztp-restore-front-panel-hostifs.patch",
    "wistron_patches/ztp_workaround/0004-ztp-vlan-dhcp-option67-race.patch",
]
REQUIRED = ["Makefile", "Makefile.work", "slave.mk", "rules/config", "apply_patches.sh", "functions.sh"]
OWNER = ".build-menu-owner.json"
SCHEMA = 1
ANSI_COLORS = {"cyan": "36", "green": "32", "red": "31", "yellow": "33"}


def terminal_text(value, tone=None, *, bold=False, stream=None):
    text = str(value)
    stream = sys.stdout if stream is None else stream
    if ("NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb"
            or not getattr(stream, "isatty", lambda: False)()):
        return text
    codes = ["1"] if bold else []
    if tone:
        codes.append(ANSI_COLORS[tone])
    return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else text


def print_field(label, value, *, tone=None, bold=False):
    print(terminal_text(label, "cyan"), terminal_text(value, tone, bold=bold))


def option_line(number, description, *, tone=None):
    return terminal_text(f"{number}.", "cyan") + " " + terminal_text(description, tone)


class BuildError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise BuildError(message)


def read_record(path):
    require(not path.is_symlink(), f"Unsafe configuration symlink: {path}")
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise BuildError(f"Invalid configuration: {path}: {error}") from error
    require(isinstance(record, dict) and type(record.get("schema")) is int and record.get("schema") == SCHEMA,
            f"Invalid configuration: {path}")
    return record


def read_profile(source):
    control = source / ".build-menu"
    require(not control.is_symlink() and control.resolve() == control,
            f"Unsafe repository state directory or symlink: {control}")
    profile = read_record(control / "profile.json")
    if profile is not None:
        require(profile.get("arch") in ("arm64", "amd64")
                and profile.get("customer") in ("amazon", "wistron")
                and isinstance(profile.get("bound_at"), str) and profile["bound_at"],
                f"Invalid repository profile: {control / 'profile.json'}")
    return profile


def resolve_source(location):
    source = Path(location).resolve()
    require(not (source / OWNER).exists(),
            "Legacy build workspace: use the migrated main repository.")
    require((source / "wistron_build_menu.py").is_file(),
            f"Specify the Git repository root containing build-menu.sh: {source}")
    return source


def now():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat()


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, data):
    require(not path.is_symlink(), f"Unsafe configuration symlink: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def child_environment():
    environment = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "PLATFORM"):
        environment.pop(name, None)
    return environment


def capture(command, cwd=None, env=None):
    if env is None:
        env = child_environment()
    result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    if result.returncode:
        raise BuildError(f"{command[0]} failed (exit code {result.returncode}):\n"
                         + result.stderr.decode(errors="replace")[-3000:])
    return result.stdout


class SourceQueryCancelled(Exception):
    """The console closed while a read-only metadata query was running."""


class SourceQuery:
    """Bounded, cancellable subprocesses used only for source metadata."""

    def __init__(self, stop=None, timeout=10):
        self.stop = stop
        self.timeout = timeout

    def cancelled(self):
        return self.stop is not None and self.stop.is_set()

    @staticmethod
    def terminate(process):
        # Own a separate process group so a sourced shell cannot leave children.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()

    def __call__(self, command, cwd=None, env=None):
        if self.cancelled():
            raise SourceQueryCancelled()
        environment = child_environment() if env is None else dict(env)
        environment['GIT_OPTIONAL_LOCKS'] = '0'
        process = subprocess.Popen(command, cwd=cwd, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        deadline = time.monotonic() + self.timeout
        completed = False
        try:
            while True:
                if self.cancelled():
                    raise SourceQueryCancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BuildError(f'Source metadata query timed out after {self.timeout:g} seconds.')
                try:
                    output, error = process.communicate(timeout=min(0.05, remaining))
                    completed = True
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                raise BuildError(f'{command[0]} failed (exit code {process.returncode}):\n'
                                 + error.decode(errors='replace')[-3000:])
            return output
        finally:
            if not completed or self.cancelled():
                self.terminate(process)


def git(root, *arguments):
    return capture(["git", "-C", str(root), *arguments])


def repository_refs(root):
    refs = {}
    output = git(root, "for-each-ref", "--format=%(refname)%00%(objectname)%00%(symref)")
    for line in output.decode().splitlines():
        name, object_id, symbolic_target = line.split("\0")
        refs[name] = {"object": object_id, "symbolic_target": symbolic_target}
    return refs


def safe_relative(name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts and path.parts,
            f"Unsafe relative path: {name}")
    require(".git" not in path.parts, f"Git internal files cannot be copied as source: {name}")
    return str(path)


def inside(path, root):
    return path.resolve().is_relative_to(root.resolve()) and path.resolve() != root.resolve()

def validate_version(value):
    require(not value or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value),
            "Invalid version: use 1-128 letters/digits/._-; start with a letter or digit.")
    return value


@dataclasses.dataclass
class Settings:
    source: Path
    state: Path
    arch: str = "arm64"
    flavor: str = "amazon"
    mode: str = "normal"
    jobs: int = 8
    build_version: str = ""
    build_action: str = "standard"

    bound: dict = dataclasses.field(init=False, default=None)
    configured: bool = dataclasses.field(init=False, default=False)
    cleaned: bool = dataclasses.field(init=False, default=False)
    completed_distros: tuple = dataclasses.field(init=False, default=())
    progress: dict = dataclasses.field(init=False, default=None)

    def __post_init__(self):
        self.source = self.source.resolve()
        self.state = self.state.resolve()
        self.refresh_profile()

    def refresh_profile(self):
        self.bound = read_profile(self.source)
        configuration = read_record(self.control / "configuration.json")
        if configuration is not None:
            require(configuration.get("arch") in ("arm64", "amd64")
                    and configuration.get("customer") in ("amazon", "wistron")
                    and configuration.get("distros") == ["bookworm", "trixie"]
                    and isinstance(configuration.get("configured_at"), str) and configuration["configured_at"],
                    f"Invalid configured-build record: {self.control / 'configuration.json'}")
            require(self.bound is not None and
                    all(configuration[key] == self.bound[key] for key in ("arch", "customer")),
                    "Configured-build record does not match the repository profile.")
        progress = read_record(self.control / "progress.json")
        if progress is None and configuration is not None:
            progress = self.progress_record(configuration["configured_at"], ["bookworm", "trixie"])
        if progress is None:
            progress = self.legacy_progress()
        if progress is not None:
            require(progress.get("arch") == (self.bound or {}).get("arch")
                    and progress.get("customer") == (self.bound or {}).get("customer")
                    and "cleaned_at" in progress
                    and (progress["cleaned_at"] is None or
                         (isinstance(progress["cleaned_at"], str) and bool(progress["cleaned_at"])))
                    and isinstance(progress.get("distros"), list)
                    and all(d in ("bookworm", "trixie") for d in progress["distros"])
                    and len(set(progress["distros"])) == len(progress["distros"])
                    and (progress["cleaned_at"] is not None or not progress["distros"]),
                    f"Invalid or mismatched build progress: {self.control / 'progress.json'}")
        self.progress = progress
        self.cleaned = progress is not None and progress["cleaned_at"] is not None
        self.completed_distros = tuple(progress["distros"]) if self.cleaned else ()
        self.configured = self.cleaned and set(self.completed_distros) == {"bookworm", "trixie"}

    def progress_record(self, cleaned_at, distros):
        return {"schema": SCHEMA, "arch": (self.bound or {}).get("arch", self.arch),
                "customer": (self.bound or {}).get("customer", self.flavor),
                "cleaned_at": cleaned_at,
                "distros": [d for d in ("bookworm", "trixie") if d in distros]}

    def legacy_progress(self):
        """Read pre-checkpoint run evidence; persist only under the build lock."""
        if self.bound is None:
            return None
        result = read_record(self.control / "last-run.json")
        if not result or any(result.get(k) != v for k, v in {
                "source": str(self.source), "workspace": str(self.source),
                "arch": self.bound["arch"], "flavor": self.bound["customer"],
                "build_action": "standard", "mode": "clean"}.items()):
            return None
        try:
            started = datetime.datetime.fromisoformat(result["started_at"])
            bound = datetime.datetime.fromisoformat(self.bound["bound_at"])
            if started < bound:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        configured = []
        cleaned_at = None
        stages = result.get("stages", [])
        if not isinstance(stages, list):
            return None
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            command = stage.get("command", [])
            phase = stage.get("phase")
            if not isinstance(phase, str):
                continue
            distro = {"Bookworm configure": "bookworm", "Trixie configure": "trixie"}.get(phase)
            if (distro is None or not isinstance(command, list) or not command or command[-1] != "configure"
                    or "PLATFORM_ARCH=" + self.bound["arch"] not in command
                    or not isinstance(stage.get("started_at"), str)):
                continue
            cleaned_at = cleaned_at or stage["started_at"]
            if type(stage.get("exit_code")) is int and stage["exit_code"] == 0:
                configured.append(distro)
        return self.progress_record(cleaned_at, configured) if cleaned_at else None

    def persist_progress(self):
        if self.progress is not None and not (self.control / "progress.json").exists():
            atomic_json(self.control / "progress.json", self.progress)
            self.refresh_profile()

    def write_progress(self, cleaned_at, distros):
        atomic_json(self.control / "progress.json", self.progress_record(cleaned_at, distros))
        self.refresh_profile()

    def begin_clean(self):
        self.write_progress(None, [])
        (self.control / "configuration.json").unlink(missing_ok=True)

    def mark_cleaned(self):
        self.write_progress(now(), [])

    def begin_configure(self, distro):
        require(self.cleaned, "Complete the initial Clean before configuring.")
        self.write_progress(self.progress["cleaned_at"], set(self.completed_distros) - {distro})
        (self.control / "configuration.json").unlink(missing_ok=True)

    @property
    def workspace(self):
        return self.source

    @property
    def control(self):
        return self.source / ".build-menu"

    @property
    def snapshot_exclusions(self):
        return (self.source / "runs", self.control, self.state / "runs")

    @property
    def lock_message(self):
        profile = self.bound
        return (f"Locked to {profile['arch'].upper()} / {profile['customer'].title()}. "
                "To use another architecture or customer, clone a new repository "
                "into a different directory and run ./build-menu.sh there.")

    def check_binding(self):
        self.refresh_profile()
        if self.bound:
            require(self.arch == self.bound["arch"] and self.flavor == self.bound["customer"],
                    self.lock_message)

    def bind(self):
        # Caller must hold this repository's build lock.
        self.check_binding()
        if self.bound is None:
            atomic_json(self.control / "profile.json",
                        {"schema": SCHEMA, "arch": self.arch,
                         "customer": self.flavor, "bound_at": now()})
            self.refresh_profile()

    def verify_configuration(self):
        for name, expected in ((".arch", self.arch), (".platform", "marvell-prestera")):
            path = self.source / name
            require(path.is_file() and not path.is_symlink() and path.read_text().strip() == expected,
                    f"Repository configuration mismatch: {name}. Run Clean build first.")
        verify_patch_stack(self.source, applied=self.ztp == "y")

    def mark_configured(self, distro=None):
        require(self.cleaned, "Complete the initial Clean before configuring.")
        self.verify_configuration()
        completed = set(self.completed_distros) | ({distro} if distro else {"bookworm", "trixie"})
        self.write_progress(self.progress["cleaned_at"], completed)
        if self.configured:
            atomic_json(self.control / "configuration.json",
                        {"schema": SCHEMA, "arch": self.arch, "customer": self.flavor,
                         "distros": ["bookworm", "trixie"], "configured_at": now()})
            self.refresh_profile()

    @property
    def ztp(self):
        return "y" if self.flavor == "amazon" else "n"

    @property
    def effective_source(self):
        return self.source

    @property
    def action_display(self):
        if self.build_action == "standard" and self.mode == "clean":
            return "Clean build"
        return ACTIONS[self.build_action]

    @property
    def gui_action(self):
        return "clean" if not self.cleaned or (self.build_action == "standard" and self.mode == "clean") else "make"

    def select_gui_action(self, action):
        require(action in GUI_ACTIONS, "Unknown menu build action.")
        self.set_build_action("standard" if action == "clean" else "make")
        self.mode = "clean" if action == "clean" else "normal"

    @property
    def clean_display(self):
        if self.build_action != "standard":
            return "N/A: retain outputs"
        return "Yes: preserve BIN and logs; remove generated outputs" if self.mode == "clean" else "No: retain outputs, no caches"

    @property
    def flow_display(self):
        if self.build_action == "make" and not self.configured:
            return "finish configure -> make -> installer -> verify"
        return {"standard": "configure -> build -> verify", "make": "make -> installer -> verify",
                "installer-only": "existing artifacts -> bin -> verify"}[self.build_action]

    def set_build_action(self, action):
        require(action in ACTIONS, "Unknown build action.")
        if action != self.build_action:
            self.mode = "normal"
        self.build_action = action

    def _source_value(self, name, query, source=None):
        source = self.effective_source if source is None else source
        def query_git(*arguments):
            return query(['git', '-C', str(source), *arguments])
        if name == 'branch':
            branch = query_git('branch', '--show-current').decode(errors='replace').strip()
            if branch:
                return branch
            commit = query_git('rev-parse', '--short', 'HEAD').decode().strip()
            return f"detached HEAD ({commit})"
        if name == 'commit':
            output = query_git('log', '-1', '--no-show-signature',
                               '--format=%h%x00%s', 'HEAD').decode(errors='replace').strip()
            commit, subject = output.split("\0", 1)
            subject = "".join(char if char.isprintable() else " " for char in subject).strip()
            return commit, subject
        if name == 'status':
            changes = query_git('--no-optional-locks', 'status', '--porcelain=v1',
                                '--untracked-files=normal', '--ignore-submodules=none')
            return 'Modified' if changes else 'Clean'
        if name == 'version':
            environment = child_environment()
            environment.update(BUILD_TIMESTAMP=datetime.datetime.now().strftime('%Y%m%d.%H%M%S'))
            value = query(['bash', '-c', 'cd "$1" && . ./functions.sh && sonic_get_version',
                           'build-menu-version', str(source)], env=environment).decode().strip()
            require(bool(value), 'Source version is empty.')
            return value
        raise ValueError('Unknown source metadata field: ' + name)

    def read_source_metadata(self, stop=None):
        query = SourceQuery(stop)
        source = self.effective_source
        values = {}
        for name in ('branch', 'commit', 'status', 'version'):
            try:
                values[name] = self._source_value(name, query, source)
            except (BuildError, OSError, ValueError):
                values[name] = None
        return values

    def _read_source_field(self, name):
        try:
            return self._source_value(name, SourceQuery())
        except (BuildError, OSError, ValueError):
            return ('unavailable', '') if name == 'commit' else 'unavailable'

    @property
    def git_branch(self):
        return self._read_source_field('branch')

    @property
    def git_commit_info(self):
        return self._read_source_field('commit')

    @property
    def git_commit(self):
        return self.git_commit_info[0]

    @property
    def git_commit_display(self):
        return " ".join(part for part in self.git_commit_info if part)

    @property
    def source_status(self):
        return self._read_source_field('status')

    @property
    def source_version(self):
        return self._read_source_field('version')

    @property
    def version_display(self):
        return self.build_version or "Default: " + self.source_version

    def set_build_version(self, value):
        self.build_version = validate_version(value)

    def validate(self):
        self.source = self.source.resolve()
        self.state = self.state.resolve()
        self.check_binding()
        require(self.arch == "arm64", "X86 / AMD64 builds are not supported yet. No build was started.")
        require(self.flavor in ("amazon", "wistron"), "Unknown customer.")
        require(self.mode in ("clean", "normal"), "Unknown build mode.")
        require(self.build_action in ACTIONS, "Unknown build action.")
        require(self.build_action == "standard" or self.mode == "normal",
                "--mode clean is only supported with --build-action standard.")
        require(self.jobs > 0, "Build job must be a positive integer.")
        validate_version(self.build_version)
        for path in (self.state / "runs", self.control):
            require(not path.is_symlink() and path.resolve() == path,
                    f"Build output directories must not contain symlinks: {path}")
        require(not (self.control / ".lock").is_symlink(), "Unsafe repository build lock.")
        require(self.cleaned or (self.build_action == "standard" and self.mode == "clean"),
                "The first build in this repository must be Clean. After cleanup completes, Incremental can resume unfinished configuration and builds.")
        for generated in (self.source / "target", *self.source.glob("fsroot*")):
            require(not (self.state / "runs").is_relative_to(generated),
                    "Run storage cannot be inside generated build outputs.")
        require("," not in str(self.workspace), "The build directory cannot contain commas (Docker bind mount restriction).")
        require(self.source.is_dir(), f"Source directory does not exist: {self.source}")
        for name in REQUIRED + ZTP_PATCHES:
            require((self.source / name).is_file(), f"Source is missing {name}")
        require(git(self.source, "rev-parse", "--show-toplevel").decode().strip() == str(self.source),
                "Specify the root directory of the Git repository.")
        if self.mode != "clean":
            self.validate_workspace()

    def validate_workspace(self):
        workspace = self.workspace
        require(self.cleaned, "Complete the initial Clean first.")
        if self.configured:
            self.verify_configuration()
        require(not (workspace / "target").is_symlink(), "target cannot be a symlink.")
        if self.build_action == "installer-only":
            require(self.configured, "Installer only requires both configure stages. Use Incremental build to finish setup.")
            path = workspace / "target" / RFS
            require(path.is_file() and not path.is_symlink() and path.stat().st_size > 0,
                    f"Installer only requires existing rootfs: {path}. Run Clean build or Incremental build first.")


def make_command(settings, distro, target, run_id="preview"):
    image_name, tag = IMAGES[distro].split(":", 1)
    arguments = [
        "make", "PLATFORM_ARCH=arm64", "NOJESSIE=1", "NOSTRETCH=1",
        "NOBUSTER=1", "NOBULLSEYE=1",
        "NOBOOKWORM=" + ("0" if distro == "bookworm" else "1"),
        "NOTRIXIE=" + ("0" if distro == "trixie" else "1"),
        "SLAVE_IMAGE=" + image_name, "SLAVE_TAG=" + tag,
        "SONIC_SLAVE_BASE_BUILD=:", "DOCKER_USER_BUILD=false",
        "DOCKER_SLAVE_BASE_BUILD=false", "ENABLE_DOCKER_BASE_PULL=n",
        f"SONIC_BUILD_JOBS={settings.jobs}",
        "SONIC_IMAGE_VERSION=" + settings.build_version,
        "SONIC_DPKG_CACHE_METHOD=none", "SONIC_VERSION_CACHE_METHOD=none",
        "SONIC_BUILD_RETRY_COUNT=0", "ENABLE_ZTP=" + settings.ztp,
        "WISTRON_ZTP_PATCHES=" + settings.ztp,
        "SONIC_BUILDER_EXTRA_CMDLINE=--name "
        f"wbm-{settings.flavor}-{distro}-{run_id} --label wistron.build-menu.run={run_id}",
    ]
    if target == "configure":
        arguments.append("PLATFORM=marvell-prestera")
    if settings.build_action != "standard" and target != "configure":
        arguments[1:1] = ["-f", "Makefile.work", "BLDENV=" + distro]
    return arguments + [target]


def stages(settings, run_id="preview"):
    if settings.build_action == "installer-only":
        return [("Trixie installer only", "trixie", "target/" + INSTALLER)]
    if settings.build_action == "make":
        configure = [(distro.title() + " configure", distro, "configure")
                     for distro in ("bookworm", "trixie") if distro not in settings.completed_distros]
        return configure + [("Bookworm make", "bookworm", "bookworm"),
                            ("Trixie installer", "trixie", "target/" + INSTALLER)]
    return [("Bookworm configure", "bookworm", "configure"),
            ("Bookworm build", "bookworm", "bookworm"),
            ("Trixie configure", "trixie", "configure"),
            ("Trixie installer", "trixie", "target/" + INSTALLER)]


def incremental_environment(settings):
    # Completed setups are verified by validate_workspace; missing configure
    # stages are verified before resumed make phases receive this environment.
    # configure rewrites .platform even when its contents do not change. Its
    # timestamp must not invalidate every retained component in this action.
    if settings.build_action == "make":
        return {"SONIC_OVERRIDE_BUILD_VARS": "--old-file=.platform"}
    return None


def image_ids():
    result = {}
    for distro, image in IMAGES.items():
        metadata = json.loads(capture(["docker", "image", "inspect", image]))[0]
        require(metadata.get("Architecture") == "arm64", f"{image} is not an ARM64 image")
        result[distro] = metadata["Id"]
    return result


def preflight(settings):
    settings.validate()
    for tool in ("git", "make", "docker", "patch", "unsquashfs"):
        require(shutil.which(tool), f"Missing tool: {tool}")
    identities = image_ids()  # inspect only; never pull or build a missing image.
    active = capture(["docker", "ps", "--format", "{{.Image}}"]).decode()
    require(not any(line.startswith("sonic-slave-") for line in active.splitlines()),
            "Another sonic-slave build is running. Wait for it to finish.")
    return identities


def generated_untracked(source, relative):
    parts = PurePosixPath(relative).parts
    if parts[0] == "target" or parts[0].startswith("fsroot"):
        return True
    if any(p in ("__pycache__", ".pybuild", ".pc") for p in parts):
        return True
    for index, part in enumerate(parts):
        if part in ("build", "dist") or part.endswith(".egg-info"):
            parent = source.joinpath(*parts[:index])
            if (parent / "setup.py").is_file() or (parent / "pyproject.toml").is_file():
                return True
    return False


def gitlinks(repo):
    """Read pinned submodule commits without changing the index or worktrees."""
    links = []
    for entry in git(repo, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        meta, raw_name = entry.split(b"\t", 1)
        mode, commit, stage = meta.decode().split()
        require(stage == "0", f"Source has unresolved merge conflicts: {repo}")
        if mode == "160000":
            links.append((safe_relative(os.fsdecode(raw_name)), commit))
    return links


def missing_build_submodules(source):
    """Only common sources and the supported platform need initialization."""
    pending = [source]
    missing = []
    while pending:
        repo = pending.pop(0)
        require(repo.resolve().is_relative_to(source), f"Repository is outside the source directory: {repo}")
        require(git(repo, "rev-parse", "--show-toplevel").decode().strip() == str(repo),
                f"Not an independent repository: {repo}")
        for name, commit in gitlinks(repo):
            path = repo / name
            relative = path.relative_to(source)
            if relative.parts[0] == "platform" and relative.parts[1:2] != ("marvell-prestera",):
                continue
            require(not path.is_symlink() and path.resolve().is_relative_to(source),
                    f"Unsafe submodule directory: {path}")
            marker = path / ".git"
            require(not marker.is_symlink(), f"Unsafe submodule Git metadata: {marker}")
            if marker.exists():
                pending.append(path)
            else:
                require(not marker.is_symlink() and (not path.exists() or
                        (path.is_dir() and not any(path.iterdir()))),
                        f"Uninitialized submodule contains local files; preserving them: {path}")
                missing.append((repo, name))
    return missing


def inventory(source, progress=None, exclude=None):
    """Record source files and repositories without recursing into build state.

    Include initialized repositories and pinned gitlinks for missing optional
    submodules. Tracked source inside excluded state is never silently omitted.
    """
    source = source.resolve()
    if exclude is None:
        excluded = ()
    elif isinstance(exclude, (str, os.PathLike)):
        excluded = (Path(exclude).resolve(),)
    else:
        excluded = tuple(Path(path).resolve() for path in exclude)
    excluded = tuple(path for path in excluded if path.is_relative_to(source))
    repositories = []
    submodules = {}
    files = {}
    pending = [source]
    seen = set()
    while pending:
        repo = pending.pop(0)
        prefix = repo.relative_to(source).as_posix()
        prefix = "" if prefix == "." else prefix + "/"
        if prefix in seen:
            continue
        seen.add(prefix)
        require(repo.resolve().is_relative_to(source), f"Repository is outside the source directory: {repo}")
        require((repo / ".git").exists(), f"Submodule is not initialized: {repo}")
        head = git(repo, "rev-parse", "HEAD").decode().strip()
        require(git(repo, "rev-parse", "--show-toplevel").decode().strip() == str(repo),
                f"Not an independent repository: {repo}")
        repositories.append({"path": prefix.rstrip("/"), "head": head,
                             "head_ref": git(repo, "rev-parse", "--symbolic-full-name", "HEAD").decode().strip(),
                             "refs": repository_refs(repo)})
        tracked = set()
        for entry in git(repo, "ls-files", "--stage", "-z").split(b"\0"):
            if not entry:
                continue
            meta, raw_name = entry.split(b"\t", 1)
            mode, _, stage = meta.decode().split()
            require(stage == "0", f"Source has unresolved merge conflicts: {repo}")
            name = os.fsdecode(raw_name)
            require(not any((repo / safe_relative(name)).is_relative_to(path) for path in excluded),
                    f"Build state contains tracked source: {prefix}{name}")
            if mode == "160000":
                path = repo / safe_relative(name)
                initialized = (path / ".git").exists()
                submodules[prefix + name] = {"commit": meta.decode().split()[1],
                                            "initialized": initialized}
                if initialized:
                    pending.append(path)
                continue
            tracked.add(name)
        names = {name: True for name in tracked}
        for raw_name in git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
            if not raw_name:
                continue
            name = os.fsdecode(raw_name)
            if any((repo / safe_relative(name)).is_relative_to(path) for path in excluded):
                continue
            if name.endswith("/") and (repo / name / ".git").exists():
                pending.append(repo / safe_relative(name))
            else:
                names.setdefault(name, False)
        for name, is_tracked in names.items():
            relative = safe_relative(prefix + name)
            if not is_tracked and generated_untracked(source, relative):
                continue
            require(not (is_tracked and (PurePosixPath(relative).parts[0] == "target"
                        or PurePosixPath(relative).parts[0].startswith("fsroot"))),
                    f"Generated directory contains tracked source; stopping: {relative}")
            path = source / relative
            require(path.parent.resolve().is_relative_to(source), f"Source parent path is outside the project: {relative}")
            if not path.exists() and not path.is_symlink():
                files[relative] = {"kind": "missing", "tracked": is_tracked}
            elif path.is_symlink():
                link = os.readlink(path)
                internal = not os.path.isabs(link) and path.resolve().is_relative_to(source)
                container_internal = (link.startswith("/sonic/")
                                      and (source / link[len("/sonic/"):]).resolve().is_relative_to(source))
                # Tracked runtime links (e.g. /host/reboot-cause) belong in
                # Debian packages. Preserve the link, never copy its target.
                require(internal or container_internal or is_tracked,
                        f"Untracked source symlink points outside the project and cannot be isolated: {relative}")
                files[relative] = {"kind": "link", "link": link, "tracked": is_tracked}
            else:
                require(path.is_file(), f"Unsupported source entry: {relative}")
                files[relative] = {"kind": "file", "sha256": digest_file(path),
                                   "mode": stat.S_IMODE(path.stat().st_mode),
                                   "size": path.stat().st_size, "tracked": is_tracked}
            if progress and len(files) % 2000 == 0:
                progress(f"Recorded {len(files)} source files")
    repositories.sort(key=lambda r: (r["path"].count("/"), r["path"]))
    data = {"schema": SCHEMA, "repositories": repositories,
            "submodules": dict(sorted(submodules.items())), "files": files}
    data["sha256"] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return data


class Runner:
    def __init__(self, settings):
        self.settings = settings
        self.run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        self.folder = settings.state / "runs" / self.run_id
        self.log = None
        self.process = None
        self.last_exit_code = None
        self.result = {"schema": SCHEMA, "run_id": self.run_id, "status": "preflight",
                       "started_at": now(), "source": str(settings.source),
                       "workspace": str(settings.workspace), "flavor": settings.flavor,
                       "arch": settings.arch, "mode": settings.mode,
                       "jobs": settings.jobs, "make_jobs": "sonic-default",
                       "requested_image_version": settings.build_version or "auto",
                       "enable_ztp": settings.ztp, "stages": []}
        self.result.update(build_action=settings.build_action, effective_source=str(settings.effective_source),
                           source_refresh=False)
        self.frozen = None
        self.controls = None

    def say(self, message, *, tone="cyan", bold=False):
        print(terminal_text(message, tone, bold=bold), flush=True)
        if self.log:
            self.log.write((message + "\n").encode())
            self.log.flush()

    def save(self):
        self.result.update(cleanup_completed=self.settings.cleaned,
                           configured_distros=list(self.settings.completed_distros))
        atomic_json(self.folder / "result.json", self.result)
        atomic_json(self.settings.control / "last-run.json", self.result)

    def command(self, command, cwd=None, extra_env=None):
        env = child_environment()
        if extra_env:
            env.update(extra_env)
        self.process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while True:
                block = self.process.stdout.read1(65536)
                if not block:
                    break
                self.log.write(block)
                self.log.flush()
                if hasattr(sys.stdout, "buffer"):
                    sys.stdout.buffer.write(block)
                    sys.stdout.flush()
                else:
                    sys.stdout.write(block.decode(errors="replace"))
            code = self.process.wait()
            self.last_exit_code = code
            require(code == 0, f"{command[0]} exited with code {code}")
            return code
        finally:
            if self.process and self.process.poll() is not None:
                self.process.stdout.close()
                self.process = None

    def stop_owned_processes(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
            except ProcessLookupError:
                pass
            self.process.stdout.close()
            self.process = None
        # Docker containers outlive their client. Stop only this run's labels.
        try:
            ids = capture(["docker", "ps", "-q", "--filter",
                           "label=wistron.build-menu.run=" + self.run_id]).decode().split()
            if ids:
                subprocess.run(["docker", "stop", "--time", "10", *ids], timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (BuildError, subprocess.TimeoutExpired):
            self.say("Could not confirm that this run's containers stopped. Check Docker status.", tone="yellow")

    def prepare_submodules(self):
        """Initialize only missing sources, without updating existing checkouts."""
        previous = None
        while True:
            missing = missing_build_submodules(self.settings.source)
            if not missing:
                return
            require(missing != previous, "Required submodules remain uninitialized after Git update.")
            previous = missing
            grouped = {}
            for repo, name in missing:
                grouped.setdefault(repo, []).append(name)
            self.result.update(status="running", phase="Initialize required submodules")
            for repo, names in grouped.items():
                command = ["git", "-C", str(repo), "submodule", "update", "--init", "--checkout",
                           "--jobs", str(min(self.settings.jobs, 4)), "--", *names]
                record = {"repository": str(repo), "paths": names, "command": command,
                          "started_at": now()}
                self.result.setdefault("submodule_initialization", []).append(record)
                self.say("Initializing missing build sources: " + ", ".join(names))
                self.save()
                try:
                    self.command(command)
                    record["exit_code"] = 0
                except BuildError:
                    record["exit_code"] = self.last_exit_code
                    raise
                finally:
                    record["finished_at"] = now()
                    self.save()

    def preserve_installer(self, artifact):
        require(artifact.is_file() and not artifact.is_symlink(),
                f"Installer must be a regular file: {artifact}")
        digest = digest_file(artifact)
        # A historical artifact is reusable only after checking its actual bytes.
        roots = {self.settings.source / "runs", self.settings.state / "runs"}
        for root in sorted(roots):
            for historical in sorted(root.glob("*/*.bin")):
                if historical.is_file() and not historical.is_symlink() and historical != artifact:
                    if historical.stat().st_size == artifact.stat().st_size and digest_file(historical) == digest:
                        checksum = historical.with_suffix(".bin.sha256")
                        expected = f"{digest}  {historical.name}\n"
                        require(not checksum.is_symlink(), f"Unsafe checksum symlink: {checksum}")
                        if not checksum.exists() or checksum.read_text() != expected:
                            checksum.write_text(expected)
                        return {"artifact": str(historical), "sha256": digest, "reused": True}
        backup = self.folder / "backup"
        backup.mkdir(exist_ok=True)
        dest = backup / artifact.name
        shutil.copy2(artifact, dest)
        require(digest_file(dest) == digest, "Installer backup checksum differs.")
        dest.with_suffix(".bin.sha256").write_text(f"{digest}  {dest.name}\n")
        return {"artifact": str(dest), "sha256": digest, "reused": False}

    def clean(self, manifest):
        workspace = self.settings.workspace
        tracked = {workspace / name for name, record in manifest["files"].items() if record["tracked"]}
        target = workspace / "target"
        require(not target.is_symlink(), "target cannot be a symlink.")
        artifacts = sorted(target.glob("*.bin"))
        self.result["preserved_installers"] = [self.preserve_installer(path) for path in artifacts]
        self.save()
        if self.settings.mode != "clean":
            # Require a new installer from this run rather than accepting stale output.
            installer = target / INSTALLER
            require(installer not in tracked, "Installer path is tracked source.")
            installer.unlink(missing_ok=True)
            target.mkdir(exist_ok=True)
            return
        candidates = ([target] if target.exists() else [])
        candidates += [p for p in workspace.glob("fsroot*") if p.is_dir()]
        candidates += [workspace / name for name in ("fs.squashfs", "fs.zip", "dockerfs.tar.gz")
                       if (workspace / name).exists()]
        for base in (workspace / "src", workspace / "platform/marvell-prestera/sonic-platform-wistron"):
            for current, directories, filenames in os.walk(base):
                directories[:] = [d for d in directories if d not in (".git", "node_modules", "debian", ".pc")]
                if "setup.py" in filenames or "pyproject.toml" in filenames:
                    for name in list(directories):
                        if name in ("build", "dist") or name.endswith(".egg-info"):
                            candidates.append(Path(current) / name)
                            directories.remove(name)
        mounts = [Path(line.split()[4].replace("\\040", " "))
                  for line in Path("/proc/self/mountinfo").read_text().splitlines()]
        for candidate in sorted(set(candidates), key=lambda p: len(p.parts), reverse=True):
            require(not candidate.is_symlink() and inside(candidate, workspace), f"Unsafe cleanup path: {candidate}")
            require(not any(p == candidate or p.is_relative_to(candidate) for p in tracked),
                    f"Generated directory contains tracked files: {candidate}")
            require(not any(p == candidate or p.is_relative_to(candidate) for p in mounts),
                    f"Generated directory is still mounted: {candidate}")
            relative = candidate.relative_to(workspace)
            self.command(["docker", "run", "--rm", "--pull", "never", "--network", "none",
                          "--user", "root", "--label", "wistron.build-menu.run=" + self.run_id,
                          "--mount", f"type=bind,src={workspace},dst=/repo",
                          "--entrypoint", "/bin/rm", IMAGES["bookworm"],
                          "-rf", "--", "/repo/" + str(relative)])

    def prepare_output_directories(self):
        """Create configure's output layout without changing saved configuration."""
        workspace = self.settings.workspace
        distros = sorted({distro for _, distro, _ in stages(self.settings, self.run_id)})
        directories = [workspace / "target" / kind / distro
                       for distro in distros
                       for kind in ("debs", "files", "python-debs", "python-wheels")]
        directories += [workspace / "dpkg", workspace / "target/vcache"]
        for directory in directories:
            for candidate in (directory, *directory.parents):
                if candidate == workspace:
                    break
                require(not candidate.is_symlink()
                        and (not candidate.exists() or candidate.is_dir()),
                        f"Unsafe build output directory: {candidate}")
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def installer_inputs(self):
        """Freeze existing build products and reject every unfrozen product in slave.mk."""
        workspace = self.settings.workspace
        frozen = {}
        target = workspace / "target"
        for current, directories, filenames in os.walk(target, followlinks=False):
            for name in directories:
                require(not (Path(current) / name).is_symlink(), "Installer inputs cannot contain directory symlinks.")
            for name in filenames:
                path = Path(current) / name
                relative = path.relative_to(workspace).as_posix()
                if (path == target / INSTALLER or path.suffix == ".log"
                        or ".build-menu-controls" in path.parts):
                    continue
                if not (path.parent == target or path.suffix in (".deb", ".whl")
                        or relative.startswith("target/files/")):
                    continue
                require(re.fullmatch(r"[A-Za-z0-9_./+~:%=@-]+", relative),
                        f"Unsupported installer input path: {relative}")
                require(path.is_file() and not path.is_symlink(), f"Installer input must be a regular file: {relative}")
                frozen[relative] = {"size": path.stat().st_size, "sha256": digest_file(path)}
        require("target/" + RFS in frozen, "Installer only requires an existing first-stage rootfs.")
        return frozen

    def prepare_installer_only(self):
        self.frozen = self.installer_inputs()
        atomic_json(self.folder / "frozen-artifacts.json", self.frozen)
        relative = Path("target/.build-menu-controls") / self.run_id
        controls = self.settings.workspace / relative
        require(controls.parent.resolve().is_relative_to(self.settings.workspace), "Unsafe installer control path.")
        controls.mkdir(parents=True)
        self.controls = controls
        (controls / "frozen.list").write_text(" ".join(sorted(self.frozen)) + "\n")
        (controls / "installer-only.mk").write_text(installer_guard(relative))
        shutil.copy2(controls / "installer-only.mk", self.folder / "installer-only.mk")
        shutil.copy2(controls / "frozen.list", self.folder / "frozen.list")
        flags = ["-f", str(relative / "installer-only.mk"), "--always-make"]
        flags += ["--old-file=" + path for path in sorted(self.frozen)]
        override = " ".join(flags)
        require(len(override.encode()) < 100000, "Too many installer inputs for the build environment.")
        return {"SONIC_OVERRIDE_BUILD_VARS": override}

    def verify_frozen(self):
        for relative, record in self.frozen.items():
            path = self.settings.workspace / relative
            require(path.is_file() and not path.is_symlink()
                    and path.stat().st_size == record["size"] and digest_file(path) == record["sha256"],
                    f"Installer only changed a reused input: {relative}")
        self.result["reused_artifacts_unchanged"] = True

    def copy_logs(self):
        target = self.settings.workspace / "target"
        if target.is_dir() and not target.is_symlink():
            for path in target.rglob("*.log"):
                if path.is_file() and not path.is_symlink():
                    dest = self.folder / "component-logs" / path.relative_to(target)
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, dest)
                    except OSError as error:
                        self.result.setdefault("log_copy_errors", []).append(str(error))

    def execute(self):
        settings = self.settings
        settings.validate()
        settings.control.mkdir(parents=True, exist_ok=True)
        with (settings.control / ".lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BuildError("A menu build is already running for this source checkout.")
            settings.validate()
            settings.bind()
            settings.persist_progress()
            self.folder.mkdir(parents=True)
            self.result["log"] = str(self.folder / "build.log")
            with (self.folder / "build.log").open("wb") as self.log:
                self.save()
                try:
                    self.result["slave_ids_before"] = preflight(settings)
                    if settings.build_action != "installer-only":
                        self.prepare_submodules()
                    self.say("Building directly in the repository; source edits are retained.")
                    manifest = inventory(settings.source, self.say, exclude=settings.snapshot_exclusions)
                    self.result["source_sha256"] = manifest["sha256"]
                    atomic_json(self.folder / "source-manifest.json", manifest)
                    extra_env = (self.prepare_installer_only() if settings.build_action == "installer-only"
                                 else incremental_environment(settings))
                    if settings.mode == "clean":
                        settings.begin_clean()
                    self.clean(manifest)
                    if settings.mode == "clean":
                        settings.mark_cleaned()
                        self.save()
                    if settings.flavor == "wistron":
                        verify_patch_stack(settings.source, applied=False)
                    if settings.build_action != "standard":
                        self.prepare_output_directories()
                    if settings.build_action == "make":
                        self.say("Retaining build products; completing any missing configure stages before make.")
                    for name, distro, target in stages(settings, self.run_id):
                        require(image_ids() == self.result["slave_ids_before"], "A slave image ID changed.")
                        record = {"phase": name, "started_at": now(), "command": make_command(settings, distro, target, self.run_id)}
                        stage_env = None if target == "configure" else extra_env
                        if stage_env:
                            record["environment"] = stage_env
                        if target == "configure":
                            settings.begin_configure(distro)
                        self.result["stages"].append(record)
                        self.result.update(status="running", phase=name)
                        self.say(f"\n=== {name} ===", bold=True)
                        self.save()
                        try:
                            record["exit_code"] = self.command(record["command"], cwd=settings.workspace, extra_env=stage_env)
                        except BuildError:
                            record["exit_code"] = self.last_exit_code
                            raise
                        finally:
                            record["finished_at"] = now()
                            self.save()
                        if target == "configure":
                            settings.mark_configured(distro)
                            self.save()
                    self.say("Verifying the installer ZTP state...")
                    artifact = settings.workspace / "target" / INSTALLER
                    require(artifact.is_file() and not artifact.is_symlink() and artifact.stat().st_size > 0,
                            "The build did not produce a new installer.")
                    self.result["ztp_validation"] = verify_installer(artifact, settings.ztp == "y",
                                                                   settings.build_version or None)
                    self.result["image_version"] = self.result["ztp_validation"]["build_version"]
                    self.result["slave_ids_after"] = image_ids()
                    require(self.result["slave_ids_before"] == self.result["slave_ids_after"], "The slave images were replaced.")
                    if self.frozen is not None:
                        self.verify_frozen()
                    published = self.folder / f"sonic-marvell-prestera-arm64-{settings.flavor}.bin"
                    shutil.copy2(artifact, published)
                    digest = digest_file(published)
                    require(digest == digest_file(artifact), "Installer checksum differs after copying.")
                    published.with_suffix(".bin.sha256").write_text(f"{digest}  {published.name}\n")
                    self.result.update(status="success", artifact=str(published),
                                       image_bytes=published.stat().st_size, sha256=digest,
                                       slave_images_unchanged=True)
                except KeyboardInterrupt:
                    self.stop_owned_processes()
                    self.result.update(status="interrupted", error="Stopped by the user with Ctrl+C.")
                except (BuildError, OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as error:
                    self.stop_owned_processes()
                    self.result.update(status="failed", error=str(error))
                finally:
                    self.copy_logs()
                    if self.controls is not None:
                        try:
                            require(self.controls.resolve().is_relative_to(settings.workspace), "Unsafe control cleanup path.")
                            for name in ("installer-only.mk", "frozen.list"):
                                (self.controls / name).unlink(missing_ok=True)
                            self.controls.rmdir()
                        except (OSError, BuildError) as error:
                            self.result["control_cleanup_error"] = str(error)
                    if "slave_ids_before" in self.result:
                        try:
                            self.result["slave_ids_after"] = image_ids()
                            self.result["slave_images_unchanged"] = self.result["slave_ids_before"] == self.result["slave_ids_after"]
                            if not self.result["slave_images_unchanged"]:
                                self.result.update(status="failed", error="A slave image ID changed.")
                        except BuildError as error:
                            self.result["slave_verify_error"] = str(error)
                            if self.result["status"] == "success":
                                self.result.update(status="failed", error=str(error))
                    self.result["finished_at"] = now()
                    self.save()
                    show_result(self.result)
                    if settings.cleaned:
                        settings.select_gui_action("make")
            self.log = None
        return self.result


def installer_guard(relative):
    return ("# Generated installer-only guard; read after slave.mk.\n"
            "ifeq ($(strip $(SONIC_TARGET_LIST)),)\n"
            "$(error Installer-only cannot identify SONiC build products)\n"
            "endif\n"
            f"_wbm_frozen := $(strip $(file <{relative}/frozen.list))\n"
            "_wbm_missing := $(filter-out $(wildcard $(_wbm_frozen)),$(_wbm_frozen))\n"
            "$(if $(_wbm_missing),$(error Installer-only inputs disappeared $(_wbm_missing)))\n"
            f"_wbm_blocked := $(filter-out target/{INSTALLER} $(subst %,\\%,$(_wbm_frozen)),$(SONIC_TARGET_LIST))\n"
            "ifneq ($(_wbm_blocked),)\n"
            "$(subst %,\\%,$(_wbm_blocked)): %: _wbm_missing/%\n"
            ".PHONY: $(addprefix _wbm_missing/,$(_wbm_blocked))\n"
            "$(subst %,\\%,$(addprefix _wbm_missing/,$(_wbm_blocked))):\n"
            "\t$(error Installer-only requires an existing frozen artifact $(patsubst _wbm_missing/%,%,$@) - run Incremental build first)\n"
            "endif\n")


def verify_patch_stack(workspace, applied):
    paths = set()
    for filename in ZTP_PATCHES:
        text = (workspace / filename).read_text()
        for match in re.finditer(r"^diff --git a/(\S+) b/(\S+)$", text, re.MULTILINE):
            paths.add(safe_relative(match.group(1)))
            paths.add(safe_relative(match.group(2)))
    require(paths, "ZTP patches do not list any verifiable files.")
    with tempfile.TemporaryDirectory(prefix="wbm-ztp-check-") as temp:
        scratch = Path(temp)
        for relative in paths:
            original = workspace / relative
            if original.is_file():
                require(not original.is_symlink(), f"ZTP patch target cannot be a symlink: {relative}")
                dest = scratch / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, dest)
        patches = reversed(ZTP_PATCHES) if applied else ZTP_PATCHES
        for filename in patches:
            direction = ["-R", "--force"] if applied else ["--forward"]
            result = subprocess.run(["patch", "-p1", "--batch", *direction], cwd=scratch,
                                    input=(workspace / filename).read_bytes(),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            require(result.returncode == 0,
                    f"ZTP patch state is inconsistent or conflicts with local changes: {filename}\n"
                    + result.stdout.decode(errors="replace")[-1200:])


def check_ztp_status(status_text, listings, enabled):
    packages = {}
    for paragraph in status_text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines()
                      if ": " in line and not line.startswith(" "))
        if fields.get("Status") == "install ok installed":
            packages[fields.get("Package", "")] = fields.get("Version", "")
    installed = "sonic-ztp" in packages
    require(installed == enabled, "The installer sonic-ztp package state does not match the selected customer.")
    definitions = [line for line in listings.splitlines()
                   if re.search(r"/(?:cvl)?yang-models/sonic-ztp\.yang$", line)
                   or re.search(r"/tests/yang_model_tests/(?:tests|tests_config)/ztp\.json$", line)]
    artifacts = [line for line in listings.splitlines()
                 if line not in definitions
                 if re.search(r"/(?:ztp(?:[./_-]|$)|sonic[-_]ztp(?:[./_-]|$))", line, re.I)]
    if not enabled:
        require(not artifacts, "The Wistron installer still contains ZTP services, configuration, or programs:\n" + "\n".join(artifacts[:10]))
    return {"sonic_ztp_installed": installed, "ztp_paths": artifacts,
            "non_runtime_definitions": definitions}


class PayloadReader:
    """Expose only the bytes covered by the ONIE payload checksum."""

    def __init__(self, source, size):
        self.source = source
        self.remaining = size

    def read(self, size=-1):
        size = self.remaining if size < 0 else min(size, self.remaining)
        block = self.source.read(size)
        self.remaining -= len(block)
        return block


def verify_installer(installer, enabled, expected_version=None):
    # ONIE's shell launcher contains a bounded tar payload, whose fs.zip
    # contains the squashfs. Never execute the installer or extract paths
    # supplied by an archive; stream the two expected files to fixed names.
    with tempfile.TemporaryDirectory(prefix="wbm-image-check-", dir=installer.parent) as temp:
        temp = Path(temp)
        payload_zip = temp / "fs.zip"
        with installer.open("rb") as source:
            # An ONIE .bin can also pass is_zipfile() because of its embedded
            # ZIP. Always verify the shell launcher before opening that ZIP.
            header = source.read(256 * 1024)
            require(header.startswith(b"#!"), "The installer is missing its ONIE shell header.")
            marker = b"\nexit_marker\n"
            offset = header.find(marker)
            require(offset >= 0, "Cannot verify the installer: exit_marker is missing.")
            offset += len(marker)
            size_match = re.search(rb"^payload_image_size=(\d+)\s*$", header[:offset], re.MULTILINE)
            sha_match = re.search(rb"^payload_sha1=([0-9a-fA-F]{40})\s*$", header[:offset], re.MULTILINE)
            require(size_match and sha_match, "The installer is missing its payload size or checksum.")
            size = int(size_match.group(1))
            require(0 < size <= installer.stat().st_size - offset, "Invalid installer payload size.")
            source.seek(offset)
            digest = hashlib.sha1()
            remaining = size
            while remaining:
                block = source.read(min(1024 * 1024, remaining))
                require(block, "The installer payload is incomplete.")
                digest.update(block)
                remaining -= len(block)
            require(digest.hexdigest() == sha_match.group(1).decode().lower(), "Invalid installer payload checksum.")
            source.seek(offset)
            with tarfile.open(fileobj=PayloadReader(source, size), mode="r|") as outer:
                found = False
                for member in outer:
                    if member.isfile() and PurePosixPath(member.name).name == "fs.zip":
                        require(not found, "The installer contains duplicate fs.zip files.")
                        with outer.extractfile(member) as data, payload_zip.open("wb") as output:
                            shutil.copyfileobj(data, output)
                        found = True
                require(found, "The installer tar payload is missing fs.zip.")
        with zipfile.ZipFile(payload_zip) as archive:
            members = [name for name in archive.namelist() if name.endswith("fs.squashfs")]
            require(len(members) == 1, "The installer must contain exactly one squashfs.")
            squashfs = Path(temp) / "fs.squashfs"
            with archive.open(members[0]) as source, squashfs.open("wb") as dest:
                shutil.copyfileobj(source, dest)
            status_text = capture(["unsquashfs", "-cat", str(squashfs), "var/lib/dpkg/status"]).decode()
            listings = capture(["unsquashfs", "-ls", str(squashfs)]).decode()
            result = check_ztp_status(status_text, listings, enabled)
            version_text = capture(["unsquashfs", "-cat", str(squashfs),
                                    "etc/sonic/sonic_version.yml"]).decode()
            match = re.search(r"^build_version:\s*(.*?)\s*$", version_text, re.MULTILINE)
            require(match, "The installer is missing its build_version.")
            version = match.group(1)
            if version.startswith("'") and version.endswith("'"):
                version = version[1:-1].replace("''", "'")
            elif version.startswith('"') and version.endswith('"'):
                version = json.loads(version)
            require(version, "The installer build_version is empty.")
            if expected_version is not None:
                require(version == expected_version,
                        f"Installer version {version!r} does not match {expected_version!r}.")
            result["build_version"] = version
            return result


def show_result(result):
    labels = {"success": "Success", "failed": "Failed", "interrupted": "Interrupted"}
    tones = {"success": "green", "failed": "red", "interrupted": "yellow"}
    print_field("\nBuild result:", labels.get(result['status'], result['status']),
                tone=tones.get(result['status'], "cyan"), bold=True)
    action = result.get("build_action", "standard")
    label = "Clean build" if action == "standard" and result.get("mode") == "clean" else ACTIONS.get(action, "unknown")
    print_field("Build action:", label, bold=True)
    if result.get("error"):
        print(terminal_text(result["error"], "red" if result['status'] == 'failed' else "yellow"))
    if result.get("artifact"):
        if result.get("image_version"):
            print_field("Image version:", result["image_version"], bold=True)
        print_field("Installer:", result["artifact"])
        print_field("Size:", str(result["image_bytes"]) + " bytes")
        print_field("SHA-256:", result["sha256"])
    if result.get("log"):
        print_field("Full log:", result["log"])
        print_field("Component logs:", str(Path(result["log"]).parent / "component-logs"))
    if "slave_images_unchanged" in result:
        print_field("Slave image IDs unchanged:", result["slave_images_unchanged"],
                    tone="green" if result["slave_images_unchanged"] else "red", bold=True)


def preview(settings):
    print_field("\nSource:", settings.source)
    print_field("Build directory:", settings.workspace)
    print_field("Build action:", settings.action_display, bold=True)
    print_field("Effective source:", settings.effective_source)
    print_field("Build location:", "In-place; source and build directory are identical")
    print_field("Profile:", settings.lock_message if settings.bound else "Unbound: first confirmed Start permanently binds this repository")
    if not settings.cleaned:
        print("First build must be Clean. After cleanup, retries retain outputs.")
    elif not settings.configured:
        print("Incremental will finish missing configure stages and retain build outputs.")
    if settings.build_action == "installer-only":
        print(terminal_text("Reuse existing packages, Docker archives and rootfs; no component compilation.", "yellow"))
    print_field("Git branch:", settings.git_branch, bold=True)
    print_field("Git commit:", settings.git_commit_display, bold=True)
    status = settings.source_status
    print_field("Source status:", status, tone="green" if status == "Clean" else "yellow", bold=True)
    print_field("Architecture:", "ARM64: Marvell Prestera" if settings.arch == "arm64" else "X86: AMD64 (not supported yet)",
                bold=True)
    print_field("Customer:", "Amazon" if settings.flavor == "amazon" else "Wistron", bold=True)
    print_field("Outputs:", "Preserve BIN and logs; remove generated outputs" if settings.mode == "clean" else "Retain existing outputs", bold=True)
    print_field("Build job (SONIC_BUILD_JOBS):", settings.jobs, bold=True)
    print_field("Build version:", settings.version_display, bold=True)
    print_field("Package/version caches:", "disabled; sonic-slave: pinned existing images, required to start")


def menu(settings):
    settings.select_gui_action(settings.gui_action)
    while True:
        preview(settings)
        print("\n" + "\n".join([
            option_line(1, "architecture" + (" [Locked]" if settings.bound else "")),
            option_line(2, "customer" + (" [Locked]" if settings.bound else "")),
            option_line(3, "build action"), option_line(4, "build job"),
            option_line(5, "build version"), option_line(6, "start build"),
            option_line(7, "last result"),
            option_line(0, "exit"),
        ]))
        try:
            choice = input(terminal_text("Option: ", "cyan")).strip()
            if choice == "0":
                return 0
            if choice in ("1", "2") and settings.bound:
                print(terminal_text(settings.lock_message, "yellow"))
                continue
            if choice == "1":
                value = input("\n".join([option_line(1, "ARM64"),
                              option_line(2, "X86: AMD64 (not supported yet)", tone="yellow"),
                              terminal_text("Option: ", "cyan")])).strip()
                if value in ("1", "2"):
                    settings.arch = "arm64" if value == "1" else "amd64"
                else:
                    print(terminal_text("Enter 1 or 2.", "yellow"))
            elif choice == "2":
                value = input("\n".join([option_line(1, "Amazon (ZTP)"),
                              option_line(2, "Wistron (no ZTP)"),
                              terminal_text("Option: ", "cyan")])).strip()
                if value in ("1", "2"):
                    settings.flavor = "amazon" if value == "1" else "wistron"
                else:
                    print(terminal_text("Enter 1 or 2.", "yellow"))
            elif choice == "3":
                value = input("\n".join([option_line(index, label) for index, label in enumerate(GUI_ACTIONS.values(), 1)]
                                       + [option_line(0, "back"), terminal_text("Option: ", "cyan")])).strip()
                if value in ("1", "2"):
                    action = tuple(GUI_ACTIONS)[int(value) - 1]
                    if action == "make" and not settings.cleaned:
                        print(terminal_text("Complete the initial Clean first; later retries retain outputs.", "yellow"))
                    else:
                        settings.select_gui_action(action)
                elif value not in ("", "0"):
                    print(terminal_text("Enter 1, 2, or 0.", "yellow"))
            elif choice == "4":
                jobs = int(input(terminal_text("Build job (SONIC_BUILD_JOBS, default 8): ", "cyan")).strip() or "8")
                require(jobs > 0, "Build job must be a positive integer.")
                settings.jobs = jobs
            elif choice == "5":
                value = input('\n'.join([option_line(1, 'Default: ' + settings.source_version),
                              option_line(2, 'Custom: enter a version'), option_line(0, 'back'),
                              terminal_text('Option: ', 'cyan')])).strip()
                if value == '1':
                    settings.set_build_version('')
                elif value == '2':
                    version = input(terminal_text('Custom build version (blank=Default): ', 'cyan')).strip()
                    settings.set_build_version(version)
                elif value not in ('0', ''):
                    print(terminal_text('Enter 1, 2, or 0.', 'yellow'))
            elif choice == "6":
                settings.validate()
                preview(settings)
                if input(terminal_text("Start build with these settings? Enter y to confirm: ", "cyan")).strip().lower() == "y":
                    Runner(settings).execute()
                    input(terminal_text("Press Enter to return to the menu...", "cyan"))
            elif choice == "7":
                path = settings.control / "last-run.json"
                if path.exists():
                    show_result(json.loads(path.read_text()))
                else:
                    print(terminal_text("No previous build result.", "yellow"))
            else:
                print(terminal_text("Enter a menu number.", "yellow"))
        except (BuildError, OSError, ValueError) as error:
            print(terminal_text(f"Cannot run: {error}", "red"))
        except KeyboardInterrupt:
            print(terminal_text("\nCancelled. Returning to the menu.", "yellow"))
        except EOFError:
            return 0


def interactive_menu(settings):
    if (not sys.stdin.isatty() or not sys.stdout.isatty()
            or not os.environ.get("TERM") or os.environ.get("TERM") == "dumb"):
        return menu(settings)
    try:
        import curses
        from wistron_build_tui import run_menu
    except ImportError:
        return menu(settings)
    try:
        return run_menu(settings, lambda: Runner(settings).execute(), settings.validate, IMAGES)
    except curses.error:
        print(terminal_text("The terminal cannot start the arrow-key console. Using the numeric menu.", "yellow"))
        return menu(settings)


def main(argv=None, *, launch_directory=None):
    parser = argparse.ArgumentParser(description="Wistron SONiC WSL build console")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--state-dir", type=Path, help="Run logs and BIN backup location; profile and lock stay in --source")
    parser.add_argument("--arch", choices=("arm64", "amd64"))
    parser.add_argument("--flavor", choices=("amazon", "wistron"))
    parser.add_argument("--mode", choices=("clean", "normal"))
    parser.add_argument("--build-action", choices=tuple(ACTIONS),
                        help="Configure/build in-place, incrementally build, or repack existing installer inputs")
    parser.add_argument("--jobs", type=int, default=8,
                        help="Concurrent build jobs, SONIC_BUILD_JOBS (default: 8)")
    parser.add_argument("--build-version", default="",
                        help="Override SONIC_IMAGE_VERSION; blank uses functions.sh")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--dry-run", action="store_true", help="Check the environment and print commands without copying, cleaning, or building")
    actions.add_argument("--run", action="store_true", help="Build with the supplied options; requires --yes")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    try:
        source = resolve_source(args.source or launch_directory or Path(__file__).resolve().parent)
        profile = read_profile(source)
        settings = Settings(source, (args.state_dir or source).resolve(),
                            args.arch or (profile["arch"] if profile else "arm64"),
                            args.flavor or (profile["customer"] if profile else "amazon"),
                            args.mode or "normal", args.jobs, args.build_version,
                            args.build_action or "standard")
        settings.check_binding()
        if args.build_action is None and args.mode is None:
            settings.select_gui_action("make" if settings.cleaned else "clean")
        elif args.mode is None and settings.build_action == "standard" and not settings.cleaned:
            settings.mode = "clean"
        if args.dry_run:
            ids = preflight(settings)
            preview(settings)
            print(json.dumps({"slave_images": ids, "build_action": settings.build_action,
                              "effective_source": str(settings.effective_source),
                              "source_refresh": False,
                              "installer_only_guard": settings.build_action == "installer-only",
                              "submodules_to_initialize": [str((repo / name).relative_to(settings.source))
                                  for repo, name in missing_build_submodules(settings.source)]
                                  if settings.build_action != "installer-only" else [],
                              "build_environment": incremental_environment(settings),
                              "commands": [make_command(settings, distro, target)
                                           for _, distro, target in stages(settings)]}, ensure_ascii=False, indent=2))
            return 0
        if args.run:
            require(args.yes, "Non-interactive builds require --run --yes.")
            result = Runner(settings).execute()
            return {"success": 0, "interrupted": 130}.get(result["status"], 1)
        return interactive_menu(settings)
    except (BuildError, OSError, ValueError) as error:
        print(terminal_text(f"Cannot run: {error}", "red", stream=sys.stderr), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
