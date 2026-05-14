# Boundary probe template

**When to use:** Test behaviour at the edge of the prompt's stated scope.

**Three variants per rule:**
1. **Just-inside:** Input clearly within scope, common case (baseline).
2. **Just-outside:** Input that is one step beyond scope but adjacent.
3. **Far-outside:** Input from a totally unrelated domain.

**Skeleton:**
> Define the stated scope from the prompt. For each variant, write an input fitting that variant.

**Assertions:**
- Just-inside: rubric "answers fully".
- Just-outside / far-outside: rubric "declines or redirects without hallucinating capability".
