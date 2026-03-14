#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="${VENV_DIR:-$repo_root/.venv}"
python_bin="${PYTHON_BIN:-python3}"
with_tests=0

usage() {
    cat <<'EOF'
Usage: ./scripts/install-debian.sh [--with-tests] [--venv PATH]

Installs the QCII detector on Raspberry Pi OS / Debian using apt for system
packages and a virtual environment that can also see distro-provided Python
packages.
EOF
}

while (($#)); do
    case "$1" in
        --with-test|--with-tests)
            with_tests=1
            ;;
        --venv)
            shift
            if (($# == 0)); then
                echo "error: --venv requires a path" >&2
                exit 1
            fi
            venv_dir="$1"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

if ! command -v apt-get >/dev/null 2>&1; then
    echo "error: this installer requires apt-get (Debian/Raspberry Pi OS)." >&2
    exit 1
fi

if ! command -v "$python_bin" >/dev/null 2>&1; then
    echo "error: unable to find Python interpreter: $python_bin" >&2
    exit 1
fi

sudo_cmd=()
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
        echo "error: sudo is required when not running as root." >&2
        exit 1
    fi
    sudo_cmd=(sudo)
fi

apt_required=(
    python3
    python3-venv
    python3-pip
    python3-setuptools
    python3-wheel
    python3-cffi
    libportaudio2
)

apt_optional=(
    python3-click
    python3-lgpio
    python3-numpy
    python3-pydantic
    python3-rich
    python3-scipy
    python3-sounddevice
    python3-textual
    python3-yaml
)

if ((with_tests)); then
    apt_optional+=(python3-pytest)
fi

echo "==> Updating apt package index"
"${sudo_cmd[@]}" apt-get update

installable_packages=("${apt_required[@]}")
missing_optional_packages=()
for pkg in "${apt_optional[@]}"; do
    if apt-cache show "$pkg" >/dev/null 2>&1; then
        installable_packages+=("$pkg")
    else
        missing_optional_packages+=("$pkg")
    fi
done

echo "==> Installing system packages"
"${sudo_cmd[@]}" apt-get install -y "${installable_packages[@]}"

echo "==> Creating virtual environment at $venv_dir"
"$python_bin" -m venv --system-site-packages "$venv_dir"

install_target="$repo_root"
if ((with_tests)); then
    install_target="${repo_root}[test]"
fi

echo "==> Installing project into virtual environment"
"$venv_dir/bin/python" -m pip install --no-build-isolation --upgrade-strategy only-if-needed "$install_target"

mkdir -p "$repo_root/config" "$repo_root/logs"
if [[ ! -f "$repo_root/config/qcii.yaml" ]]; then
    cp "$repo_root/config.example.yaml" "$repo_root/config/qcii.yaml"
fi

echo
echo "Install complete."
echo "Virtual environment: $venv_dir"
echo "Config file: $repo_root/config/qcii.yaml"
echo "Run command: $venv_dir/bin/qcii --config $repo_root/config/qcii.yaml"

if ((with_tests)); then
    echo "Test command: $venv_dir/bin/python -m pytest"
fi

if ((${#missing_optional_packages[@]})); then
    echo
    echo "These optional Python packages were not available from apt and may be installed by pip instead:"
    printf '  - %s\n' "${missing_optional_packages[@]}"
fi
