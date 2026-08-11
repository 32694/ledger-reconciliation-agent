#!/bin/bash
set -euo pipefail

python -m reconciliation_agent.cli migrate
python -m reconciliation_agent.cli seed
python -m reconciliation_agent.cli ingest-rules

exec "$@"
