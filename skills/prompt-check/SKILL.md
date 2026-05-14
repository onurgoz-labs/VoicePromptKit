---
name: prompt-check
description: Workflow contract for the prompt-check-orchestrator agent. Defines invariants and failure handling.
---

# Prompt-check workflow

## Invariants
- Never modify the prompt file directly except via the inline reporter.
- Always write intermediate artefacts under `.promptcheck/.tmp/`.
- Never call the same lens agent twice for the same prompt within one run.
- Phase 1 lens agents (conflict, priority, gap) MUST run after rule-extractor completes.
- Phase 1 lenses run in a single parallel Agent dispatch.

## Failure cascade
- rule-extractor fails → abort entire run, surface error.
- Any one Phase-1 lens fails → continue with other lenses, mark missing sections as `[unavailable]` in report.
- behavior-runner fails → skip judge, write report with empty `runs` and `verdicts`.
- judge fails → write report with mechanical-only verdicts.

## Cleanup
Leave `.promptcheck/.tmp/` after a run (debugging). Cleanup happens on next run when the orchestrator starts.
