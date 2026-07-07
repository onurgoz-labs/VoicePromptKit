# Dominance lens

Read alongside `_shared.md` (which carries the output invariant, fix_strategy, severity heuristics, compact mode, section_ref, compact writing, and language switching every lens depends on).

A dominance is **not** a conflict: it is the relationship where one rule will silently override another in practice, even without a logical contradiction. The dominated rule still applies in theory, but the dominant rule wins under the model's recency / length / role-override biases.

Mechanisms:

- **position** — later instruction overrides earlier (LLMs are recency-biased).
- **length** — a long, repeated rule overshadows a single-line counter-rule.
- **specificity** — a specific exception beats a general rule (this one is often intentional and benign — flag only when the specific rule is too narrow).
- **recency** — rules near the end of the prompt anchor model behaviour.
- **role-override** — phrases like "ignore previous instructions", "your new role is", "actually you are", "from now on you are".

Rules that **contradict** but where neither dominates are a *conflict*, not a dominance — emit them in the conflict lens instead.

Schema:

```json
{ "dominances": [{ "id": "D1", "dominant_rule_id": "R12", "dominated_rule_id": "R3", "mechanism": "position|length|specificity|recency|role-override", "severity": "low|medium|high", "reasoning": "<≤ 300 chars>", "suggested_fix": "<concrete one-sentence rewrite or structural action>", "fix_strategy": "substring | structural", "current_excerpt": "<5-line body excerpt for script lines>", "section_ref": { "section": "7", "subsection": "7.2", "section_title": "RESPONSE GUIDELINES", "subsection_title": "TONE & STYLE" } }] }
```

Severity heuristic for dominance:
- **high** — `role-override` mechanism (the dominant rule is an explicit override pattern), or `recency` on a safety-critical rule.
- **medium** — `position` / `length` where the dominated rule is consequential, or `specificity` where the specific rule is too narrow.
- **low** — `specificity` where the dominant rule is a benign intentional exception.

For `suggested_fix`: **always populate `suggested_fix` with a concrete one-sentence action** — e.g. "Move R3 below R12 and merge their content", "Remove R6 (subsumed by R8)", or "Replace R12 with: \"After the announcement completes, immediately trigger end-call-tool unless an interruption is in progress; in that case, finish the remainder first.\"". If the dominance is intentional/benign, write `suggested_fix: 'Intentional — dismiss this finding'` so the author can see the runner reached that conclusion. Empty `suggested_fix` is no longer allowed.

## Dialog state preservation (mandatory for substring fixes on script lines)

When the dominated rule appears inside a state-machine step, script utterance, or scripted dialog line (anything where the prompt prescribes a verbatim phrase the agent will say), the replacement text MUST preserve the **dialog state intent** of the original utterance, not just remove the banned phrase.

Reasoning: the prompt's surrounding section names the state (e.g. `Step 3 — Simulate busy queue`, `STATE 17 — handoff`, `Closing`, `AI disclosure`). Replacing a script line with a string that drops out of that state — e.g. swapping a queue-status utterance for an open-ended routing question — silently breaks the flow even though the banned phrase is gone.

Checklist before emitting a substring `suggested_fix` for a script line:

1. **Read the surrounding state.** The runner already has `current_excerpt` (± 2 body lines around the finding); read it. Identify which dialog state the line belongs to from its section/subsection name and from neighbouring step labels.
2. **Preserve actor and presence.** The voice agent is the *only* active speaker; it does not "disconnect", "call you back", "reconnect later", or hand off to a human in the middle of its own utterance. Replacements must read as something the agent itself can say *next turn*. Phrases like "tekrar bağlanacağım", "I'll get back to you", "transferring you now" are forbidden unless the surrounding state is an *explicit handoff* state.
3. **Preserve wait semantics.** A "busy queue" / "transfer wait" state implies the user should *wait* and the agent will *continue*. Replacements should reflect that — e.g. `"Lütfen kısa bir süre bekleyiniz; ardından kaldığımız yerden devam edeceğim."` rather than open-ended routing like `"Konuyla ilgili nasıl ilerlemek istersiniz?"` (which silently exits the queue state).
4. **Do not overclaim resources.** Avoid implying real human agents, instant transfers, callbacks, or external systems that the prompt doesn't authorise (cf. R59 / R29 in voice prompts that restrict handoff conditions). When unsure, fall back to the safest in-state utterance (acknowledge wait, restate the user's name if known, do not promise a third party).

When the dominated rule is **not** a script line (it's a global enforcement / variable definition / structural rule), the dialog-state checklist does not apply — use the standard rewrite guidance above. The check fires only when `fix_strategy: "substring"` and the target line is inside a `Step N`, `STATE N`, dialog block, or labelled utterance.

## Metadata invariant

Every dominance finding MUST have `dominant_rule_id != dominated_rule_id`. A rule cannot dominate itself. If your analysis suggests they are the same:

- (a) it's a conflict (use the conflict lens),
- (b) the same rule is cited under two roles (re-examine — the dominant rule is some OTHER rule that overrides this one),
- (c) it's an internal inconsistency in a single rule (skip — not a dominance).

Self-correct before emitting. Phase 7 of the skill validates this invariant and downgrades violating findings to `severity: low` with a warning.
