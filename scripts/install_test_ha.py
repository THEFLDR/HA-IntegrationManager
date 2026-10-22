"""Resolve latest stable Core independently of Python/dependency compatibility."""

import json
import os
import subprocess
import sys
from importlib.metadata import distribution, version
from pathlib import Path
from urllib.request import urlopen

from packaging.version import Version


def latest_stable(releases):
    return str(
        max(
            parsed
            for name, files in releases.items()
            if not (parsed := Version(name)).is_prerelease
            and not parsed.is_devrelease
            and any(not file.get("yanked", False) for file in files)
        )
    )


def report(message):
    print(message, flush=True)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as output:
            output.write(message + "\n\n")


def install():
    # Never ask pip to choose a compatible older Core. Resolve first, then use
    # that exact version for both installs; unsupported Python also fails CI.
    with urlopen("https://pypi.org/pypi/homeassistant/json", timeout=30) as response:  # noqa: S310
        resolved = latest_stable(json.load(response)["releases"])
    report(f"Resolved latest stable Home Assistant Core: {resolved}")
    core = f"homeassistant=={resolved}"
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", core], check=True)  # noqa: S603
    manifest = distribution("homeassistant").locate_file(
        "homeassistant/components/frontend/manifest.json"
    )
    # HA declares its matching frontend here rather than in Core dependencies.
    frontend = json.loads(manifest.read_text())["requirements"]
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pip", "install", core, *frontend], check=True
    )
    installed = version("homeassistant")
    if installed != resolved:
        raise RuntimeError(f"Expected Home Assistant Core {resolved}, installed {installed}")
    report(f"Installed Home Assistant Core under test: {installed}")


if __name__ == "__main__":
    install()
