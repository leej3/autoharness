"""Run the pinned AutoHarness core without depending on its source checkout."""
import shutil
import subprocess
import sys

RUNTIME = (
    "autoharness-native @ git+https://github.com/leej3/autoharness.git"
    "@b6ba9173cf85e654250b0f6302fa71859dbc27e3"
)


def main():
    uv = shutil.which("uv")
    if uv is None:
        sys.exit("autoharness-reflect requires uv on PATH; install uv before retrying.")
    return subprocess.call([
        uv, "tool", "run", "--python", "3.11", "--from", RUNTIME,
        "autoharness-native", *sys.argv[1:],
    ])


if __name__ == "__main__":
    sys.exit(main())
