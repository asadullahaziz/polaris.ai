"""
Inngest client singleton (P0.9).

Dev mode: `is_production=False` + the `INNGEST_DEV` env var pointing at the
compose dev server (`http://inngest:8288`) — the SDK both serves in dev mode
(no signature verification) and sends events there. Production keys
(INNGEST_EVENT_KEY / INNGEST_SIGNING_KEY) are read from env when
`INNGEST_IS_PRODUCTION=true`.
"""

from __future__ import annotations

import logging

import inngest
from django.conf import settings

inngest_client = inngest.Inngest(
    app_id=settings.INNGEST_APP_ID,
    is_production=settings.INNGEST_IS_PRODUCTION,
    logger=logging.getLogger("inngest"),
)
