"""
polaris_agent — the agent package (import-isolated from Django views/consumers,
extractable later). Depends on Django models via a thin DAL, never on request
handling. Invoked from exactly two surfaces: the ASGI WS consumer (copilot) and
Inngest functions (auto-responder, outreach send). See implementation_plan §2.

P0 contains only the spike scaffolding: the shared checkpointer, a trivial
1-node graph, the provider-agnostic model wiring, and the DAL.
"""
