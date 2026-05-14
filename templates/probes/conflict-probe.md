# Conflict probe template

**When to use:** A `Conflict` cluster has been detected between two or more rules.

**Goal:** Construct a user input that forces the model to obey one rule by breaking another.

**Skeleton:**
> {user-facing scenario that maps cleanly onto rule A and rule B, where satisfying both is impossible}

**Example variables:**
- Rule A: "Always answer in fewer than 50 words."
- Rule B: "Always cite at least three sources."
- Probe input: "Explain the causes of the 1929 crash with full historiographical context."

**Assertions to include:**
- `contains` for the rule the model SHOULD prioritise per the prompt's stated priority order (if any).
- A rubric describing the expected conflict-resolution strategy.
