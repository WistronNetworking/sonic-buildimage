#!/usr/bin/env python3
"""Foreground Wistron SONiC builds, without changing the source checkout."""

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


def now():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat()


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


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
    mode: str = "clean"
    jobs: int = 8
    build_version: str = ""

    @property
    def workspace(self):
        return self.state / "workspaces" / self.arch / self.flavor

    @property
    def ztp(self):
        return "y" if self.flavor == "amazon" else "n"

    def _source_value(self, name, query):
        def query_git(*arguments):
            return query(['git', '-C', str(self.source), *arguments])
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
                           'build-menu-version', str(self.source)], env=environment).decode().strip()
            require(bool(value), 'Source version is empty.')
            return value
        raise ValueError('Unknown source metadata field: ' + name)

    def read_source_metadata(self, stop=None):
        query = SourceQuery(stop)
        values = {}
        for name in ('branch', 'commit', 'status', 'version'):
            try:
                values[name] = self._source_value(name, query)
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
        require(self.arch == "arm64", "X86 / AMD64 builds are not supported yet. No build was started.")
        require(self.flavor in ("amazon", "wistron"), "Unknown customer.")
        require(self.mode in ("clean", "normal"), "Unknown build mode.")
        require(self.jobs > 0, "Build job must be a positive integer.")
        validate_version(self.build_version)
        require(not self.state.is_relative_to(self.source)
                and not self.source.is_relative_to(self.state),
                "The build state and source directories must not contain each other.")
        require("," not in str(self.workspace), "The build directory cannot contain commas (Docker bind mount restriction).")
        require(self.source.is_dir(), f"Source directory does not exist: {self.source}")
        for name in REQUIRED + ZTP_PATCHES:
            require((self.source / name).is_file(), f"Source is missing {name}")
        require(git(self.source, "rev-parse", "--show-toplevel").decode().strip() == str(self.source),
                "Specify the root directory of the Git repository.")


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
    return arguments + [target]


def stages(settings, run_id="preview"):
    return [("Bookworm configure", "bookworm", "configure"),
            ("Bookworm build", "bookworm", "bookworm"),
            ("Trixie configure", "trixie", "configure"),
            ("Trixie installer", "trixie", "target/" + INSTALLER)]


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


def inventory(source, progress=None):
    """Record working files, including populated submodules and nested repos.

    Each cloned repository will have its own Git metadata, never a .git link
    into the original checkout. Tracked files take priority over exclusions.
    """
    repositories = []
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
            if mode == "160000":
                pending.append(repo / safe_relative(name))
                continue
            tracked.add(name)
        names = {name: True for name in tracked}
        for raw_name in git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
            if not raw_name:
                continue
            name = os.fsdecode(raw_name)
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
    data = {"schema": SCHEMA, "repositories": repositories, "files": files}
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

    def say(self, message, *, tone="cyan", bold=False):
        print(terminal_text(message, tone, bold=bold), flush=True)
        if self.log:
            self.log.write((message + "\n").encode())
            self.log.flush()

    def save(self):
        atomic_json(self.folder / "result.json", self.result)
        atomic_json(self.settings.state / "last-run.json", self.result)

    def command(self, command, cwd=None):
        env = child_environment()
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

    def prepare_workspace(self, manifest):
        workspace = self.settings.workspace
        owner = {"schema": SCHEMA, "source": str(self.settings.source),
                 "arch": self.settings.arch, "flavor": self.settings.flavor}
        workspace.parent.mkdir(parents=True, exist_ok=True)
        require(not workspace.is_symlink(), "The build directory cannot be a symlink.")
        marker = workspace / OWNER
        if workspace.exists():
            require(marker.is_file() and json.loads(marker.read_text()) == owner,
                    f"Build directory is not owned by this menu; stopping: {workspace}")
        else:
            workspace.mkdir()
            atomic_json(marker, owner)
        previous_path = workspace / ".build-menu-source.json"
        previous = json.loads(previous_path.read_text()) if previous_path.exists() else {"files": {}}
        repositories = manifest["repositories"]
        for repo in repositories:
            self.prepare_repository(repo)
        for relative in set(previous["files"]) - set(manifest["files"]):
            self.remove_file(workspace / safe_relative(relative))
        for relative, record in sorted(manifest["files"].items()):
            dest = workspace / relative
            original = self.settings.source / relative
            require(dest.parent.resolve().is_relative_to(workspace), f"Destination parent path is outside the build directory: {relative}")
            if record["kind"] == "missing":
                self.remove_file(dest)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if record["kind"] == "link":
                if dest.is_symlink() and os.readlink(dest) == record["link"]:
                    continue
                self.remove_file(dest)
                dest.symlink_to(record["link"])
            else:
                if dest.is_symlink():
                    dest.unlink()
                require(not dest.exists() or dest.is_file(), f"File and directory conflict: {relative}")
                if not dest.exists() or digest_file(dest) != record["sha256"]:
                    shutil.copy2(original, dest)
                dest.chmod(record["mode"])
                require(digest_file(dest) == record["sha256"], f"Source changed while copying: {relative}")
        for repo in repositories:
            dest = workspace / repo["path"]
            if (dest / ".gitmodules").is_file():
                self.command(["git", "-C", str(dest), "submodule", "init"])
        for repo in repositories:
            require(git(self.settings.source / repo["path"], "rev-parse", "HEAD").decode().strip()
                    == repo["head"], "Source HEAD changed while taking the snapshot. Retry the build.")
        require(inventory(self.settings.source)["sha256"] == manifest["sha256"],
                "Source changed while taking the snapshot. Retry the build.")
        atomic_json(previous_path, manifest)

    def prepare_repository(self, repo):
        workspace = self.settings.workspace
        dest = workspace / safe_relative(repo["path"]) if repo["path"] else workspace
        original = self.settings.source / repo["path"]
        require(dest.resolve().is_relative_to(workspace.resolve()), "Repository is outside the build directory.")
        if not (dest / ".git").exists():
            dest.mkdir(parents=True, exist_ok=True)
            self.command(["git", "clone", "--quiet", "--local", "--no-hardlinks",
                          "--mirror", str(original), str(dest / ".git")])
            self.command(["git", "-C", str(dest), "config", "core.bare", "false"])
        require(not (dest / ".git").is_symlink() and (dest / ".git").is_dir(),
                f"Build repository Git metadata must be independent: {dest}")
        common = Path(git(dest, "rev-parse", "--git-common-dir").decode().strip())
        require((dest / common).resolve() == (dest / ".git").resolve()
                and git(dest, "rev-parse", "--show-toplevel").decode().strip() == str(dest),
                f"Build Git metadata points to another directory; stopping: {dest}")
        # Mirror only from the local source, including remote refs that --bare
        # clones omit. Refresh even when HEAD has not changed. The destination
        # is an independent snapshot, so its build-created refs can be pruned.
        self.command(["git", "-C", str(dest), "fetch", "--quiet", "--no-tags",
                      "--prune", "--no-write-fetch-head", "--recurse-submodules=no",
                      "--update-head-ok", str(original), "+refs/*:refs/*", repo["head"]])
        for name, record in repo["refs"].items():
            if record["symbolic_target"]:
                self.command(["git", "-C", str(dest), "symbolic-ref", name, record["symbolic_target"]])
        if repo["head_ref"] == "HEAD":
            self.command(["git", "-C", str(dest), "update-ref", "--no-deref", "HEAD", repo["head"]])
        else:
            self.command(["git", "-C", str(dest), "symbolic-ref", "HEAD", repo["head_ref"]])
        require(repository_refs(dest) == repo["refs"],
                f"Source Git refs changed or were not fully copied; stopping: {original}")
        # Reset only the independent snapshot index; never the source repo.
        self.command(["git", "-C", str(dest), "reset", "--mixed", "--quiet", repo["head"]])

    def remove_file(self, path):
        require(inside(path.parent, self.settings.workspace) or path.parent == self.settings.workspace,
                f"Cannot remove a file outside the build directory: {path}")
        require(not path.is_dir() or path.is_symlink(), f"Cannot remove a source directory as a file: {path}")
        if path.exists() or path.is_symlink():
            path.unlink()

    def clean(self, manifest):
        workspace = self.settings.workspace
        tracked = {workspace / name for name, record in manifest["files"].items() if record["tracked"]}
        target = workspace / "target"
        backup = self.folder / "backup"
        backup.mkdir()
        require(not target.is_symlink(), "target cannot be a symlink.")
        if self.settings.mode == "clean" and target.exists():
            require(not any(p == target or p.is_relative_to(target) for p in tracked),
                    "target contains source files. Cleanup stopped.")
            target.rename(backup / "target")
        elif (target / INSTALLER).exists():
            require(not (target / INSTALLER).is_symlink(), "The installer cannot be a symlink.")
            (target / INSTALLER).rename(backup / INSTALLER)
        target.mkdir(exist_ok=True)
        if self.settings.mode != "clean":
            return
        candidates = [p for p in workspace.glob("fsroot*") if p.is_dir()]
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

    def normalize_ztp(self):
        self.command(["bash", "./apply_patches.sh", "ZTP=no"], cwd=self.settings.workspace)
        verify_patch_stack(self.settings.workspace, applied=False)

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
        settings.state.mkdir(parents=True, exist_ok=True)
        state_owner = settings.state / OWNER
        expected_owner = {"schema": SCHEMA, "source": str(settings.source)}
        if state_owner.exists():
            require(json.loads(state_owner.read_text()) == expected_owner, "The state directory belongs to another source checkout.")
        else:
            require(not any(settings.state.iterdir()), "The state directory contains unmanaged entries. Stopping.")
            atomic_json(state_owner, expected_owner)
        with (settings.state / ".lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BuildError("A menu build is already running for this source checkout.")
            self.folder.mkdir(parents=True)
            self.result["log"] = str(self.folder / "build.log")
            with (self.folder / "build.log").open("wb") as self.log:
                self.save()
                try:
                    self.result["slave_ids_before"] = preflight(settings)
                    self.say("Creating a snapshot of the current working source...")
                    manifest = inventory(settings.source, self.say)
                    self.result["source_sha256"] = manifest["sha256"]
                    atomic_json(self.folder / "source-manifest.json", manifest)
                    self.prepare_workspace(manifest)
                    self.clean(manifest)
                    self.say("Preparing the selected customer's ZTP state...")
                    self.normalize_ztp()
                    for name, distro, target in stages(settings, self.run_id):
                        require(image_ids() == self.result["slave_ids_before"], "A slave image ID changed.")
                        record = {"phase": name, "started_at": now(), "command": make_command(settings, distro, target, self.run_id)}
                        self.result["stages"].append(record)
                        self.result.update(status="running", phase=name)
                        self.say(f"\n=== {name} ===", bold=True)
                        self.save()
                        try:
                            record["exit_code"] = self.command(record["command"], cwd=settings.workspace)
                        except BuildError:
                            record["exit_code"] = self.last_exit_code
                            raise
                        finally:
                            record["finished_at"] = now()
                            self.save()
                        if target == "configure":
                            verify_patch_stack(settings.workspace, applied=settings.ztp == "y")
                    self.say("Verifying the installer ZTP state...")
                    artifact = settings.workspace / "target" / INSTALLER
                    require(artifact.is_file() and not artifact.is_symlink() and artifact.stat().st_size > 0,
                            "The build did not produce a new installer.")
                    self.result["ztp_validation"] = verify_installer(artifact, settings.ztp == "y",
                                                                   settings.build_version or None)
                    self.result["image_version"] = self.result["ztp_validation"]["build_version"]
                    self.result["slave_ids_after"] = image_ids()
                    require(self.result["slave_ids_before"] == self.result["slave_ids_after"], "The slave images were replaced.")
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
            self.log = None
        return self.result


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


def verify_installer(installer, enabled, expected_version=None):
    # ONIE's shell launcher contains a bounded tar payload, whose fs.zip
    # contains the squashfs. Never execute the installer or extract paths
    # supplied by an archive; stream the two expected files to fixed names.
    with tempfile.TemporaryDirectory(prefix="wbm-image-check-", dir=installer.parent) as temp:
        temp = Path(temp)
        payload_zip = installer
        if not zipfile.is_zipfile(installer):
            payload_zip = temp / "fs.zip"
            with installer.open("rb") as source:
                header = source.read(256 * 1024)
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
                with tarfile.open(fileobj=source, mode="r|") as outer:
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
    print_field("Git branch:", settings.git_branch, bold=True)
    print_field("Git commit:", settings.git_commit_display, bold=True)
    status = settings.source_status
    print_field("Source status:", status, tone="green" if status == "Clean" else "yellow", bold=True)
    print_field("Architecture:", "ARM64: Marvell Prestera" if settings.arch == "arm64" else "X86: AMD64 (not supported yet)",
                bold=True)
    print_field("Customer:", "Amazon" if settings.flavor == "amazon" else "Wistron", bold=True)
    print_field("Clean rebuild:", "Yes: back up target and rebuild" if settings.mode == "clean" else "No: retain outputs, no caches", bold=True)
    print_field("Build job (SONIC_BUILD_JOBS):", settings.jobs, bold=True)
    print_field("Build version:", settings.version_display, bold=True)
    print_field("Package/version caches:", "disabled; sonic-slave: pinned existing images, required to start")


def menu(settings):
    while True:
        preview(settings)
        print("\n" + "\n".join([
            option_line(1, "architecture"), option_line(2, "customer"),
            option_line(3, "clean rebuild"), option_line(4, "build job"),
            option_line(5, "build version"), option_line(6, "start build"),
            option_line(7, "last result"),
            option_line(0, "exit"),
        ]))
        try:
            choice = input(terminal_text("Option: ", "cyan")).strip()
            if choice == "0":
                return 0
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
                current = "Yes" if settings.mode == "clean" else "No"
                value = input(terminal_text(f"Clean rebuild (Yes/No) [{current}]: ", "cyan")).strip().lower() or current.lower()
                if value in ("yes", "y", "no", "n"):
                    settings.mode = "clean" if value in ("yes", "y") else "normal"
                else:
                    print(terminal_text("Enter Yes or No.", "yellow"))
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
                path = settings.state / "last-run.json"
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Wistron SONiC WSL build console")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--arch", choices=("arm64", "amd64"), default="arm64")
    parser.add_argument("--flavor", choices=("amazon", "wistron"), default="amazon")
    parser.add_argument("--mode", choices=("clean", "normal"), default="clean")
    parser.add_argument("--jobs", type=int, default=8,
                        help="Concurrent build jobs, SONIC_BUILD_JOBS (default: 8)")
    parser.add_argument("--build-version", default="",
                        help="Override SONIC_IMAGE_VERSION; blank uses functions.sh")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--dry-run", action="store_true", help="Check the environment and print commands without copying, cleaning, or building")
    actions.add_argument("--run", action="store_true", help="Build with the supplied options; requires --yes")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    source = args.source.resolve()
    state = (args.state_dir or source.with_name(source.name + "-build-menu")).resolve()
    settings = Settings(source, state, args.arch, args.flavor, args.mode, args.jobs, args.build_version)
    try:
        if args.dry_run:
            ids = preflight(settings)
            preview(settings)
            print(json.dumps({"slave_images": ids, "commands": [make_command(settings, distro, target)
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
