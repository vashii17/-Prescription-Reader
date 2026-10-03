#!/usr/bin/env bash
# Starts Ollama, the Temporal dev server, the worker and the web app in one container.
# If any of them stops, the script exits so the host restarts the container.
set -euo pipefail

MODEL="${GEMMA_MODEL:-gemma4:e2b}"
PORT="${PORT:-10000}"
mkdir -p /var/data/app /var/data/ollama

wait_for() {  # wait_for <name> <url-or-host:port> <seconds>
    local name="$1" target="$2" seconds="$3"
    for _ in $(seq "$seconds"); do
        if [[ "$target" == http* ]]; then
            curl -fsS "$target" >/dev/null 2>&1 && return 0
        else
            (exec 3<>"/dev/tcp/${target%:*}/${target#*:}") 2>/dev/null && return 0
        fi
        sleep 1
    done
    echo "$name did not start within $seconds seconds" >&2
    return 1
}

echo "Starting Ollama"
ollama serve &
wait_for "Ollama" "http://127.0.0.1:11434/api/version" 60

# The first start downloads the model to the persistent disk (several GB). It runs in
# the background so the web app is reachable straight away; until it finishes, the
# review screen says the model could not be reached and schedules are typed by hand.
(
    until ollama pull "$MODEL"; do
        echo "Model download failed, retrying in 20 seconds" >&2
        sleep 20
    done
    echo "Model $MODEL is ready"
) &

echo "Starting Temporal"
temporal server start-dev --headless --ip 127.0.0.1 --db-filename /var/data/temporal.db --log-level warn &
wait_for "Temporal" "127.0.0.1:7233" 90

echo "Starting the worker and the web app on port $PORT"
python -m app.worker &
python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" &

# Exit as soon as any long-running process stops (the model download ending is fine).
while true; do
    for name in "ollama serve" "temporal server" "app.worker" "uvicorn"; do
        if ! pgrep -f "$name" >/dev/null; then
            echo "$name has stopped; shutting down" >&2
            exit 1
        fi
    done
    sleep 5
done
