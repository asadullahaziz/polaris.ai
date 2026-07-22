"""
Polaris agent evals — an on-demand, offline LangFuse eval suite over the two
LLM agents (away-responder + copilot).

This package is LLM-ful by design and lives OUTSIDE the pytest suite, which stays
LLM-free (see CLAUDE.md). Evals are run through the `run_evals` management command,
gated on a live provider key exactly like the responder smokes.

Layout:
  datasets/   code-defined, ground-truth-labeled dataset items (source of truth)
  registry.py dataset name -> {items, kind}; pushed to Langfuse by sync_eval_datasets
  seeding.py  reconstruct ephemeral DB state for a responder scenario
  scorers.py  deterministic Evaluation-returning scorers (reuse disclosure.py gates)
  judges.py   LLM-as-judge Evaluation scorers (voice, helpfulness) — the only judges
  runners.py  per-surface task fns + run_experiment orchestration

The deterministic guardrails (disclosure.output_check / style_check / policy_gate)
are the exact scorers for the responder's safety properties — the same code the
graph enforces at runtime is the code that grades the eval.
"""
