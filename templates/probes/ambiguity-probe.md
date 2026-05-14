# Ambiguity probe template

**When to use:** Gap-finder flagged an ambiguous term or undefined edge case.

**Skeleton:** Construct an input that exercises the ambiguous term in two opposing interpretations.

**Example:**
- Ambiguous rule: "Be appropriately formal."
- Probe A: User asks a casual question with slang.
- Probe B: User asks a formal question in a professional tone.
- Expectation: Model adapts tone consistently per the prompt's intent — both probes should produce internally consistent tone given context.

**Assertions:** rubric only ("output exhibits consistent interpretation of {ambiguous-term}").
