#!/usr/bin/env python3
"""Read anchors for a prompt file.

Source precedence (v0.5.1):
  1. `<prompt>.anchors.yaml` sidecar — preferred.
  2. `frontmatter.anchors[]` in the prompt itself — legacy, emits deprecation warning.

Usage:
  python3 bin/read-anchors.py <prompt_path>

Output:
  Single JSON document on stdout:
    {
      "anchors":  [<validated + expanded anchor objects>],
      "source":   "sidecar" | "frontmatter" | "none",
      "warnings": [<string>, ...]
    }

  Exit code is always 0 unless the prompt file itself cannot be opened
  (exit 2). Parse / schema errors emit a warning and return an empty
  anchor list with exit 0 — the caller decides how to surface that.

Sidecar schema (v0.5.1):
  schema_version: 1
  anchors:
    - input: "..."                  # single-turn (kind omitted → implicit "single")
      expect_contains: [...]
      expect_not_contains: [...]
      rubric: "..."
      context: [{role, content}, ...]   # optional prior conversation
    - kind: flow
      name: "..."                  # optional, surfaced in /prompt-test table
      turns:
        - kind: user_input
          content: "..."
        - kind: silence_input      # sugar: expands to user_input with "[silence for N seconds]"
          duration_seconds: 6
        - kind: assistant_expect
          expect_contains: [...]
          expect_not_contains: [...]
          rubric: "..."
        - kind: end_call_expect    # terminal step
          rubric: "..."
"""
import sys
import os
import re
import json


def main():
    if len(sys.argv) != 2:
        print("usage: read-anchors.py <prompt_path>", file=sys.stderr)
        sys.exit(2)
    prompt_path = sys.argv[1]
    if not os.path.exists(prompt_path):
        print(f"error: prompt not found: {prompt_path}", file=sys.stderr)
        sys.exit(2)

    try:
        import yaml
    except ImportError:
        # PyYAML missing — emit a warning, return empty.
        json.dump({
            "anchors": [],
            "source": "none",
            "warnings": ["PyYAML not available; cannot parse anchors"],
        }, sys.stdout, ensure_ascii=False)
        sys.exit(0)

    warnings = []
    sidecar_path = prompt_path + ".anchors.yaml"

    if os.path.exists(sidecar_path):
        anchors, source_warnings, parse_ok = _read_sidecar(sidecar_path, yaml)
        warnings.extend(source_warnings)
        # If sidecar parses but is empty (parse_ok && len 0), still report
        # "sidecar" as the source — the user explicitly created an empty file.
        if parse_ok:
            # Also check if frontmatter has anchors → dual-existence warning.
            fm_anchors = _read_frontmatter_anchors(prompt_path, yaml)
            if fm_anchors:
                warnings.append(
                    "both sidecar and frontmatter.anchors are present — sidecar wins; "
                    "remove the anchors block from frontmatter to silence this warning"
                )
            json.dump({
                "anchors": anchors,
                "source": "sidecar",
                "warnings": warnings,
            }, sys.stdout, ensure_ascii=False)
            return
        # Sidecar exists but failed to parse / wrong schema_version — fall through
        # to frontmatter as a recovery path (rather than giving the user nothing).

    # Fall back to frontmatter.
    fm_anchors = _read_frontmatter_anchors(prompt_path, yaml)
    if fm_anchors:
        warnings.append(
            f"frontmatter.anchors is deprecated in v0.5.1 — migrate to {os.path.basename(sidecar_path)}"
        )
        validated = _validate_and_expand(fm_anchors, warnings)
        json.dump({
            "anchors": validated,
            "source": "frontmatter",
            "warnings": warnings,
        }, sys.stdout, ensure_ascii=False)
        return

    json.dump({
        "anchors": [],
        "source": "none",
        "warnings": warnings,
    }, sys.stdout, ensure_ascii=False)


def _read_sidecar(sidecar_path, yaml):
    warnings = []
    try:
        with open(sidecar_path, encoding='utf-8') as f:
            sidecar = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        warnings.append(f"sidecar parse error in {os.path.basename(sidecar_path)}: {e}")
        return [], warnings, False
    except OSError as e:
        warnings.append(f"sidecar read error in {os.path.basename(sidecar_path)}: {e}")
        return [], warnings, False

    if sidecar.get('schema_version') != 1:
        warnings.append(
            f"sidecar schema_version mismatch in {os.path.basename(sidecar_path)}: "
            f"got {sidecar.get('schema_version')!r}, expected 1"
        )
        return [], warnings, False

    raw = sidecar.get('anchors') or []
    if not isinstance(raw, list):
        warnings.append(
            f"sidecar 'anchors' field in {os.path.basename(sidecar_path)} is not a list"
        )
        return [], warnings, False

    validated = _validate_and_expand(raw, warnings)
    return validated, warnings, True


def _read_frontmatter_anchors(prompt_path, yaml):
    try:
        text = open(prompt_path, encoding='utf-8').read()
    except Exception:
        return []
    m = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n?(.*)$', text, re.DOTALL)
    if not m:
        return []
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return []
    return fm.get('anchors') or []


def _validate_and_expand(anchors, warnings):
    """Validate and expand each anchor. Silence_input sugar expands to user_input.

    Drops invalid anchors (with a warning) rather than failing the whole load.
    """
    out = []
    for idx, anchor in enumerate(anchors):
        anchor_label = f"anchor #{idx + 1}"
        if not isinstance(anchor, dict):
            warnings.append(f"{anchor_label}: not a mapping — skipped")
            continue

        kind = anchor.get('kind') or 'single'

        if kind == 'single':
            if 'input' not in anchor:
                warnings.append(f"{anchor_label}: single-turn anchor missing 'input' — skipped")
                continue
            out.append(dict(anchor))
        elif kind == 'flow':
            expanded = _validate_and_expand_flow(anchor, anchor_label, warnings)
            if expanded is not None:
                out.append(expanded)
        else:
            warnings.append(f"{anchor_label}: unknown kind {kind!r} — skipped")
    return out


def _validate_and_expand_flow(anchor, anchor_label, warnings):
    turns = anchor.get('turns') or []
    if not isinstance(turns, list) or not turns:
        warnings.append(f"{anchor_label}: flow anchor with no turns — skipped")
        return None

    expanded_turns = []
    prev_role = None  # 'user' | 'assistant' | 'end' | None

    for tidx, turn in enumerate(turns):
        turn_label = f"{anchor_label} turn #{tidx + 1}"
        if not isinstance(turn, dict):
            warnings.append(f"{turn_label}: not a mapping — anchor skipped")
            return None
        tk = turn.get('kind')

        if tk == 'silence_input':
            duration = turn.get('duration_seconds')
            if duration is None or not isinstance(duration, int) or duration <= 0:
                warnings.append(
                    f"{turn_label}: silence_input requires positive int duration_seconds — anchor skipped"
                )
                return None
            expanded_turns.append({
                'kind': 'user_input',
                'content': f'[silence for {duration} seconds]',
            })
            role = 'user'
        elif tk == 'user_input':
            if 'content' not in turn or not isinstance(turn.get('content'), str):
                warnings.append(
                    f"{turn_label}: user_input missing string content — anchor skipped"
                )
                return None
            expanded_turns.append(dict(turn))
            role = 'user'
        elif tk == 'assistant_expect':
            if not _has_any_assertion(turn):
                warnings.append(
                    f"{turn_label}: assistant_expect needs at least one of "
                    f"expect_contains / expect_not_contains / rubric — anchor skipped"
                )
                return None
            expanded_turns.append(dict(turn))
            role = 'assistant'
        elif tk == 'end_call_expect':
            # rubric optional; an implicit "session closed" rubric applies
            expanded_turns.append(dict(turn))
            role = 'end'
        else:
            warnings.append(f"{turn_label}: unknown turn kind {tk!r} — anchor skipped")
            return None

        # Opening enforcement: a flow must start with a user/silence turn —
        # there is nothing for the assistant to respond to otherwise.
        if tidx == 0 and role != 'user':
            warnings.append(
                f"{turn_label}: flow must start with user_input / silence_input — anchor skipped"
            )
            return None

        # Alternation enforcement: user → assistant → user → … → end
        if prev_role == 'user' and role == 'user':
            warnings.append(
                f"{turn_label}: two consecutive user turns (no assistant_expect between) — anchor skipped"
            )
            return None
        if prev_role == 'assistant' and role == 'assistant':
            warnings.append(
                f"{turn_label}: two consecutive assistant turns — anchor skipped"
            )
            return None
        if prev_role == 'end':
            warnings.append(
                f"{turn_label}: turns appear after end_call_expect — anchor skipped"
            )
            return None
        prev_role = role

    # Closing enforcement: a flow must end on an assertion of the assistant's
    # turn — ending on a user turn leaves the final assistant response untested.
    if prev_role not in ('assistant', 'end'):
        warnings.append(
            f"{anchor_label}: flow must end with assistant_expect / end_call_expect — anchor skipped"
        )
        return None

    new = dict(anchor)
    new['turns'] = expanded_turns
    return new


def _has_any_assertion(turn):
    if turn.get('expect_contains'):
        return True
    if turn.get('expect_not_contains'):
        return True
    if turn.get('rubric'):
        return True
    return False


if __name__ == '__main__':
    main()
