"""Shared test fixtures.

Sets a high rate limit to prevent cross-test token bucket exhaustion.
The RateLimitMiddleware reads GTM_RATE_LIMIT_RPM at __init__ time
(module import), so this must be set before the first import.
"""

from __future__ import annotations

import os

# Set high rate limit BEFORE any gtm_triage imports. This runs at
# conftest collection time, before test modules are imported.
os.environ.setdefault("GTM_RATE_LIMIT_RPM", "10000")
os.environ.setdefault("GTM_PROVIDER", "mock")
