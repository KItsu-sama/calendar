
#!/usr/bin/env bash

set -e
cd "$(dirname "$0")"

# Run Linux setup if the script exists.
if [ -f "./setup_calendar.sh" ]; then
    bash ./setup_calendar.sh
fi

# Prefer the project's virtual environment if available.
if [ -x "./.venv/bin/python" ]; then
    PYTHON="./.venv/bin/python"
else
    PYTHON="python3"
fi

exec "$PYTHON" calendar_launcher.py "$@"
