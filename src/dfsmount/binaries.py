"""Fetch prebuilt dwarfs binaries from GitHub releases, or resolve from PATH."""

from __future__ import annotations

import json
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from functools import cache, lru_cache
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
BUNDLED_BIN_DIR = _PACKAGE_ROOT / "bin"
_CURRENT_LINK = BUNDLED_BIN_DIR / "current"

_RELEASES_API = "https://api.github.com/repos/mhx/dwarfs/releases/latest"
_ARCH_MAP = {"x86_64": "x86_64", "aarch64": "aarch64"}
DWARFS_EXECUTABLES = ("mkdwarfs", "dwarfs", "dwarfsck", "dwarfsextract")


class BinaryFetchError(RuntimeError):
    pass


def _dwarfs_arch() -> str:
    machine = platform.machine()
    try:
        return _ARCH_MAP[machine]
    except KeyError:
        raise BinaryFetchError(
            f"no dwarfs release binaries published for architecture {machine!r}"
        ) from None


@lru_cache(maxsize=1)
def latest_release_metadata() -> dict:
    request = urllib.request.Request(
        _RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "dfsmount"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (OSError, json.JSONDecodeError) as exc:
        raise BinaryFetchError(f"could not reach {_RELEASES_API}: {exc}") from exc


def _release_asset(release: dict) -> tuple[str, str]:
    version = release.get("tag_name", "").removeprefix("v")
    arch = _dwarfs_arch()
    name = f"dwarfs-{version}-Linux-{arch}.tar.xz"
    for asset in release.get("assets", ()):
        if asset.get("name") == name:
            return asset["browser_download_url"], version
    raise BinaryFetchError(f"release {release.get('tag_name')!r} has no asset {name!r}")


def _has_all_executables(version_dir: Path) -> bool:
    return version_dir.is_dir() and all(
        (version_dir / name).is_file() for name in DWARFS_EXECUTABLES
    )


def _extract_bin_dir(archive_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    wanted = {f"bin/{name}" for name in DWARFS_EXECUTABLES}
    with tarfile.open(archive_path, mode="r:xz") as archive:
        for member in archive.getmembers():
            relative = "/".join(member.name.split("/")[1:])
            if relative not in wanted or not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            target = dest / Path(relative).name
            target.write_bytes(extracted.read())
            target.chmod(0o755)


def fetch_release(force: bool = False) -> Path:
    url, version = _release_asset(latest_release_metadata())
    version_dir = BUNDLED_BIN_DIR / version

    if force or not _has_all_executables(version_dir):
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_path = Path(tmp_dir) / "dwarfs.tar.xz"
            try:
                urllib.request.urlretrieve(url, archive_path)
            except OSError as exc:
                raise BinaryFetchError(f"download failed for {url}: {exc}") from exc
            _extract_bin_dir(archive_path, version_dir)

    _CURRENT_LINK.unlink(missing_ok=True)
    _CURRENT_LINK.symlink_to(version_dir.name)
    dwarfs_executable.cache_clear()
    return version_dir


@cache
def dwarfs_executable(name: str) -> str:
    if name not in DWARFS_EXECUTABLES:
        raise ValueError(f"not a dwarfs release executable: {name!r}")

    bundled = _CURRENT_LINK / name
    if bundled.is_file():
        return str(bundled)

    on_path = shutil.which(name)
    if on_path:
        return on_path

    raise FileNotFoundError(
        f"{name!r} not found: not bundled (run `dfsmount fetch-binaries`) "
        "and not on PATH"
    )
