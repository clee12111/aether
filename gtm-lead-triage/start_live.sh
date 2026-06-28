#!/bin/bash
# Start the GTM API with ALL providers from ../.env
# Sources the .env file so every provider uses the real config.
cd "$(dirname "$0")"

# Load all vars from .env (parent dir)
set -a
source ../.env 2>/dev/null
set +a

# SQLite fallbacks (only used when DATABASE_URL is unset)
export GTM_CRM_DB="${GTM_CRM_DB:-gtm_crm.db}"
export GTM_TRACE_DB="${GTM_TRACE_DB:-gtm_trace.db}"

python -m uvicorn gtm_triage.api:app --port 8000
