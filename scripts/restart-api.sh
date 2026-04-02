#!/usr/bin/env bash
set -euo pipefail

# Restart the Kiso API. Three modes:
#
#   --reload  (default) Graceful reload via HUP signal. Zero downtime.
#             Re-forks workers from existing master. Does NOT reimport
#             modules when preload_app=True (use --hard for code changes).
#
#   --hard    Blue/green restart via USR2. Zero downtime, fresh code.
#             Spawns a new master (fresh imports), verifies health,
#             then gracefully drains and kills the old master.
#             Use after any code change.
#
#   --cold    Full stop/start. Downtime. Use only when blue/green fails
#             or after dependency changes (new packages, etc).
#
# Usage (local on Mac Mini):
#   ./scripts/restart-api.sh           # graceful reload (default)
#   ./scripts/restart-api.sh --hard    # blue/green (code changes)
#   ./scripts/restart-api.sh --cold    # full restart (deps/emergency)
#
# Usage (remote from laptop):
#   ssh mac-mini 'cd ~/src/health-engine && bash scripts/restart-api.sh --hard'

SERVICE="com.baseline.gateway"
PLIST="$HOME/Library/LaunchAgents/${SERVICE}.plist"
PORT=18800
LOG="/tmp/baseline-gateway.log"
MODE="${1:---reload}"
PIDFILE="/tmp/kiso-gunicorn.pid"

if [ "$MODE" = "--reload" ]; then
    # Graceful reload: send HUP to gunicorn master
    if [ -f "$PIDFILE" ]; then
        MASTER_PID=$(cat "$PIDFILE")
        if kill -0 "$MASTER_PID" 2>/dev/null; then
            echo "Graceful reload: sending HUP to gunicorn master (PID $MASTER_PID)..."

            # Clear bytecode cache so new workers pick up fresh code
            find ~/src/health-engine/engine/gateway/__pycache__ -name "*.pyc" -delete 2>/dev/null || true
            find ~/src/health-engine/mcp_server/__pycache__ -name "*.pyc" -delete 2>/dev/null || true

            kill -HUP "$MASTER_PID"

            # Wait for new workers to come up
            sleep 2
            if curl -sf http://localhost:$PORT/health >/dev/null 2>&1; then
                echo "Reload complete. API healthy on port $PORT ($(date +%H:%M:%S))"
                exit 0
            else
                echo "WARN: Health check failed after reload. Falling back to cold restart."
                MODE="--cold"
            fi
        else
            echo "PID file exists but process $MASTER_PID is dead. Doing cold start."
            MODE="--cold"
        fi
    else
        echo "No PID file at $PIDFILE. Doing cold start."
        MODE="--cold"
    fi
fi

if [ "$MODE" = "--hard" ]; then
    # Blue/green: USR2 spawns a new master while old one keeps serving
    if [ ! -f "$PIDFILE" ]; then
        echo "No PID file at $PIDFILE. Falling back to cold start."
        MODE="--cold"
    else
        OLD_PID=$(cat "$PIDFILE")
        if ! kill -0 "$OLD_PID" 2>/dev/null; then
            echo "PID file exists but process $OLD_PID is dead. Falling back to cold start."
            MODE="--cold"
        else
            echo "Blue/green restart: spawning new master via USR2 (old master PID $OLD_PID)..."

            # Clear bytecode cache before new master loads
            find ~/src/health-engine/engine/gateway/__pycache__ -name "*.pyc" -delete 2>/dev/null || true
            find ~/src/health-engine/mcp_server/__pycache__ -name "*.pyc" -delete 2>/dev/null || true

            # USR2: gunicorn forks a new master, writes PID to $PIDFILE.2
            kill -USR2 "$OLD_PID"

            # Wait for new master to come up and start serving
            NEW_PIDFILE="${PIDFILE}.2"
            for i in {1..15}; do
                if [ -f "$NEW_PIDFILE" ]; then
                    NEW_PID=$(cat "$NEW_PIDFILE")
                    if kill -0 "$NEW_PID" 2>/dev/null; then
                        break
                    fi
                fi
                sleep 1
            done

            if [ ! -f "$NEW_PIDFILE" ] || ! kill -0 "$(cat "$NEW_PIDFILE")" 2>/dev/null; then
                echo "ERROR: New master did not start within 15s. Old master still serving."
                echo "Falling back to cold restart."
                MODE="--cold"
            else
                NEW_PID=$(cat "$NEW_PIDFILE")
                echo "New master running (PID $NEW_PID). Verifying health..."

                # Give new workers time to boot
                sleep 2

                if curl -sf http://localhost:$PORT/health >/dev/null 2>&1; then
                    echo "New master healthy. Draining old master (PID $OLD_PID)..."

                    # WINCH: gracefully stop old master's workers
                    kill -WINCH "$OLD_PID" 2>/dev/null || true
                    sleep 2

                    # QUIT: shut down old master
                    kill -QUIT "$OLD_PID" 2>/dev/null || true

                    # Wait for old master to exit
                    for i in {1..10}; do
                        if ! kill -0 "$OLD_PID" 2>/dev/null; then
                            break
                        fi
                        sleep 1
                    done

                    echo "Blue/green complete. API serving on port $PORT ($(date +%H:%M:%S))"
                    exit 0
                else
                    echo "ERROR: New master failed health check. Rolling back."
                    # Kill the new master, old one is still serving
                    kill -QUIT "$NEW_PID" 2>/dev/null || true
                    echo "Rolled back to old master (PID $OLD_PID). API still serving."
                    exit 1
                fi
            fi
        fi
    fi
fi

if [ "$MODE" = "--cold" ]; then
    echo "Cold restart: stopping $SERVICE..."

    # Step 1: Unload launchd service
    launchctl bootout "gui/$(id -u)/$SERVICE" 2>/dev/null || true
    sleep 1

    # Step 2: Kill any remaining process on the port
    for attempt in 1 2 3; do
        PID=$(lsof -ti :$PORT 2>/dev/null || true)
        if [ -z "$PID" ]; then
            break
        fi
        echo "Killing process $PID on port $PORT (attempt $attempt)"
        kill -9 $PID 2>/dev/null || true
        sleep 1
    done

    # Step 3: Verify port is free
    if lsof -ti :$PORT >/dev/null 2>&1; then
        echo "ERROR: Port $PORT still in use after kill. Aborting."
        lsof -i :$PORT
        exit 1
    fi

    # Step 4: Clear bytecode cache
    find ~/src/health-engine/engine/gateway/__pycache__ -name "*.pyc" -delete 2>/dev/null || true
    find ~/src/health-engine/mcp_server/__pycache__ -name "*.pyc" -delete 2>/dev/null || true

    # Step 5: Re-bootstrap the service
    echo "Starting $SERVICE..."
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || {
        echo "Bootstrap failed. Starting manually..."
        cd ~/src/health-engine
        nohup .venv/bin/gunicorn -c gunicorn.conf.py \
            "engine.gateway.server:create_app()" \
            --pid "$PIDFILE" \
            > "$LOG" 2>&1 &
    }

    # Step 6: Wait for API to come up
    for i in {1..15}; do
        if curl -sf http://localhost:$PORT/health >/dev/null 2>&1; then
            echo "API is up on port $PORT ($(date +%H:%M:%S))"
            exit 0
        fi
        sleep 1
    done

    echo "ERROR: API did not start within 15 seconds"
    tail -10 "$LOG" 2>/dev/null
    exit 1
fi
