"""
polaris_agent — the agent package, import-isolated from Django views/consumers.
It reaches the ORM only through the DAL and never touches request handling.
Invoked from exactly two surfaces: the copilot WS consumer and Inngest functions
(away-responder, outreach send).
"""
