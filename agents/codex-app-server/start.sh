#!/bin/sh
# Run from any directory: sh /path/to/codex-app-server/start.sh
set -eu
umask 077
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
LOCAL_DIR="$PROJECT_DIR/.local"
mkdir -p "$LOCAL_DIR"
exec 9>"$LOCAL_DIR/start.lock"
flock -n 9 || { echo "Another startup is already in progress." >&2; exit 1; }

if [ -n "${CONDA_EXE:-}" ] && [ -x "$CONDA_EXE" ]; then
    CONDA_CMD=$CONDA_EXE
elif command -v conda >/dev/null 2>&1; then
    CONDA_CMD=$(command -v conda)
else
    echo "Install Conda first, or set CONDA_EXE to its executable." >&2
    exit 1
fi
CONDA_ROOT=$("$CONDA_CMD" info --base)
ENV_DIR="$LOCAL_DIR/conda"
if [ ! -x "$ENV_DIR/bin/python" ]; then
    rm -f "$LOCAL_DIR/dependencies.stamp"
    echo "Creating project Conda environment..."
    "$CONDA_CMD" create --yes --prefix "$ENV_DIR" python=3.12 nodejs=24 pip
fi
export PATH="$ENV_DIR/bin:$PATH"
PYTHON="$ENV_DIR/bin/python"
DEPS_HASH=$(cat "$PROJECT_DIR/pyproject.toml" \
    "$PROJECT_DIR/deploy/requirements-linux-x86_64-py312.lock" | cksum)
if [ ! -f "$LOCAL_DIR/dependencies.stamp" ] || [ "$(cat "$LOCAL_DIR/dependencies.stamp")" != "$DEPS_HASH" ]; then
    echo "Installing project dependencies..."
    "$PYTHON" -m pip install --require-hashes --only-binary=:all: \
        -r "$PROJECT_DIR/deploy/requirements-linux-x86_64-py312.lock"
    "$PYTHON" -m pip install --no-deps -e "$PROJECT_DIR"
    "$PYTHON" -m pip check
    printf '%s\n' "$DEPS_HASH" > "$LOCAL_DIR/dependencies.stamp"
fi

export PYTHONPATH="$PROJECT_DIR/src"
RUNTIME_VERSION=$("$PYTHON" -c 'from codex_agent.runtime_version import VERSION; print(VERSION)')
case "$(uname -s):$(uname -m)" in
    Linux:x86_64) TARGET=x86_64-unknown-linux-musl; PACKAGE_VERSION=$RUNTIME_VERSION-linux-x64 ;;
    *) echo "This startup script currently supports Linux x86_64." >&2; exit 1 ;;
esac
RUNTIME_PREFIX="$LOCAL_DIR/npm-$RUNTIME_VERSION"
RUNTIME="$RUNTIME_PREFIX/node_modules/@openai/codex/vendor/$TARGET/bin/codex"
if [ ! -x "$RUNTIME" ] || [ "$("$RUNTIME" --version)" != "codex-cli $RUNTIME_VERSION" ]; then
    echo "Installing Codex dependency..."
    npm install --prefix "$RUNTIME_PREFIX" --ignore-scripts --no-audit --no-fund --no-save --package-lock=false \
        "@openai/codex@$PACKAGE_VERSION"
fi

if [ -f "$PROJECT_DIR/frontend/package-lock.json" ]; then
    FRONTEND_HASH=$(cat "$PROJECT_DIR/frontend/package-lock.json" | cksum)
    if [ ! -f "$LOCAL_DIR/frontend.stamp" ] || [ "$(cat "$LOCAL_DIR/frontend.stamp")" != "$FRONTEND_HASH" ]; then
        (cd "$PROJECT_DIR/frontend" && npm ci --no-audit --no-fund)
        printf '%s\n' "$FRONTEND_HASH" > "$LOCAL_DIR/frontend.stamp"
    fi
    (cd "$PROJECT_DIR/frontend" && npm run build)
fi

export PYTHONPATH="$PROJECT_DIR/src"
export PYTHONNOUSERSITE=1
"$PYTHON" -m codex_agent.local "$PROJECT_DIR" "$RUNTIME"
"$PYTHON" "$PROJECT_DIR/scripts/local_process.py" stop "$PROJECT_DIR"
echo "Starting: http://${CODEX_AGENT_HOST:-0.0.0.0}:${CODEX_AGENT_PORT:-18880} (Ctrl+C to stop)"
exec "$PYTHON" "$PROJECT_DIR/scripts/local_process.py" run "$PROJECT_DIR" \
    "$PYTHON" -m codex_agent.cli --config "$LOCAL_DIR/agent.json" serve --dev \
    --host "${CODEX_AGENT_HOST:-0.0.0.0}" --port "${CODEX_AGENT_PORT:-18880}"
