#!/usr/bin/env python3
"""Codex CLI backend dispatcher for prompt-check lens runners (v0.10.0).

Runs a VoicePromptKit lens runner (`static-lens-runner`, `drift-runner`, or
`tr-phonetic-runner`) through OpenAI's Codex CLI (`codex exec`) instead of
Claude Code's `Agent` tool, so the `/prompt-check` background analysis can be
driven by Codex when `backend: codex` is configured.

Why this works — contract parity:
- A lens runner spec (`agents/<runner>.md`) is a SELF-CONTAINED worker spec:
  its entire "system prompt" is the markdown body, and its "user message" is a
  JSON payload of `{inputs: {<file paths>}, output_paths|output_path: {...}}`.
- The runner's whole I/O contract is FILE-BASED — it reads the absolute paths
  under `inputs` and writes the absolute paths under `output_paths`. Nothing
  about that contract is Claude-specific.
- So driving it via Codex is mechanical: assemble `(spec body + payload)` into
  one prompt, pipe it to `codex exec` on stdin (avoids arg-length limits),
  point Codex's working root at the repo, and verify the runner wrote the
  expected JSON artefact(s). The Agent path and the Codex path produce the same
  on-disk artefacts; only the execution engine differs.

The simulated "model under test" for the drift lens becomes Codex's own model
(the runner acts as that model in-context), which is the intended behaviour
when the operator opts into the Codex backend.

Usage:
  python3 codex-lens.py \\
      --agent-spec <path to agents/<runner>.md> \\
      --payload    <path to a JSON file holding the {inputs, output_paths}
                    object the skill would otherwise pass to Agent> \\
      --repo-root  <repo root; used as Codex's working root> \\
      [--model     <codex model; omit to use Codex's configured default>] \\
      [--lens      <label, for logging>] \\
      [--timeout   <seconds; default 900>] \\
      [--log       <path to capture Codex's JSONL event stream>] \\
      [--dry-run]  print the assembled command + prompt size, then exit 0
                    without invoking Codex (for testing / inspection)

Exit codes:
  0  success — every expected output file exists and parses as JSON
  2  `codex` CLI not found on PATH
  3  `codex exec` failed (non-zero exit) or timed out
  4  Codex finished but an expected output file is missing / invalid JSON
  5  bad arguments (unreadable spec / payload, malformed payload JSON)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile


# Allow operators to point at a non-PATH codex binary (mirrors the
# VOICEPROMPTKIT_CLAUDE_CLI escape hatch used by prompt-chat-runner.py).
def _resolve_codex_cli():
    explicit = os.environ.get("VOICEPROMPTKIT_CODEX_CLI")
    if explicit:
        return explicit
    # shutil.which finds codex / codex.cmd / codex.exe per-platform.
    found = shutil.which("codex")
    if found:
        return found
    # Last-resort literal — lets --dry-run still show a meaningful command and
    # lets the OS report a clear "not found" if the operator's PATH is unusual.
    return "codex"


def _strip_frontmatter(text):
    """Drop a leading YAML frontmatter block (--- ... ---) from an agent spec.

    The runner's `name` / `description` / `tools` frontmatter is Claude-Code
    plumbing irrelevant to Codex; the instructions body is the worker spec.
    Handles both LF and CRLF. Returns the body unchanged if no frontmatter.
    """
    if not text.startswith("---"):
        return text
    lines = text.splitlines(keepends=True)
    # First line is the opening fence; find the closing fence.
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            return "".join(lines[i + 1:]).lstrip("\n")
    # No closing fence — not real frontmatter, leave as-is.
    return text


def _expected_outputs(payload):
    """Return the list of absolute output paths the runner must produce.

    Mirrors the two dispatch shapes the skill uses:
    - static-lens-runner / tr-phonetic-runner → `output_paths` (a map)
    - drift-runner                            → `output_path` (a single string)
    """
    paths = []
    op_map = payload.get("output_paths")
    if isinstance(op_map, dict):
        paths.extend(str(p) for p in op_map.values() if p)
    single = payload.get("output_path")
    if isinstance(single, str) and single:
        paths.append(single)
    return paths


def _build_prompt(spec_body, payload_text):
    """Assemble the one-shot Codex prompt: framing + spec + user-message JSON."""
    preamble = (
        "You are running as a ONE-SHOT, NON-INTERACTIVE worker for "
        "VoicePromptKit's /prompt-check. Follow the RUNNER SPEC below exactly. "
        "Treat the JSON under 'YOUR USER MESSAGE' as the runner's input "
        "message.\n\n"
        "Operating rules for this run:\n"
        "- Read every file referenced by absolute path under `inputs`.\n"
        "- Perform the analysis the RUNNER SPEC prescribes.\n"
        "- WRITE your result to the absolute path(s) under `output_paths` / "
        "`output_path`. The output files MUST exist and contain valid JSON "
        "when you finish.\n"
        "- Do NOT ask the user questions, do NOT request approval, do NOT stop "
        "early. There is no human to answer.\n"
        "- When finished, print ONLY the single status line the RUNNER SPEC "
        "tells you to return. No other commentary.\n"
    )
    return (
        preamble
        + "\n===== RUNNER SPEC =====\n"
        + spec_body.rstrip()
        + "\n\n===== YOUR USER MESSAGE (JSON) =====\n"
        + payload_text.strip()
        + "\n"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Dispatch a prompt-check lens runner through Codex CLI."
    )
    parser.add_argument("--agent-spec", required=True,
                        help="Path to the runner spec (agents/<runner>.md).")
    parser.add_argument("--payload", required=True,
                        help="Path to a JSON file: the {inputs, output_paths} "
                             "object the skill would pass to Agent.")
    parser.add_argument("--repo-root", required=True,
                        help="Repo root; used as Codex's working root (-C).")
    parser.add_argument("--model", default=None,
                        help="Codex model (-m). Omit to use Codex's default.")
    parser.add_argument("--lens", default=None,
                        help="Lens label, for log lines only.")
    parser.add_argument("--timeout", type=int, default=900,
                        help="Hard timeout in seconds (default 900).")
    parser.add_argument("--log", default=None,
                        help="Optional path to capture Codex's JSONL events.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the command + prompt size and exit without "
                             "invoking Codex.")
    args = parser.parse_args(argv)

    label = args.lens or os.path.basename(args.agent_spec)

    # --- Read + validate inputs (exit 5 on any malformed argument) ----------
    try:
        with open(args.agent_spec, encoding="utf-8") as f:
            spec_text = f.read()
    except OSError as e:
        print(f"error: cannot read agent spec {args.agent_spec!r}: {e}",
              file=sys.stderr)
        return 5
    try:
        with open(args.payload, encoding="utf-8") as f:
            payload_text = f.read()
        payload = json.loads(payload_text)
    except (OSError, ValueError) as e:
        print(f"error: cannot read/parse payload {args.payload!r}: {e}",
              file=sys.stderr)
        return 5

    expected = _expected_outputs(payload)
    if not expected:
        print("error: payload has neither `output_paths` nor `output_path` — "
              "nothing for the runner to produce.", file=sys.stderr)
        return 5

    spec_body = _strip_frontmatter(spec_text)
    prompt = _build_prompt(spec_body, payload_text)

    codex_cli = _resolve_codex_cli()

    # Assemble the codex exec command. stdin carries the prompt ("-" forces it).
    # workspace-write + repo-root cwd lets the runner write the output JSON,
    # which lands inside the workspace (.voicepromptkit/.../run-NNN/).
    out_fd, last_msg_path = tempfile.mkstemp(suffix=".codex-last.txt",
                                             prefix="vpk-codex-")
    os.close(out_fd)
    cmd = [
        codex_cli, "exec",
        "--cd", args.repo_root,
        "--sandbox", "workspace-write",
        "--skip-git-repo-check",
        "--color", "never",
        "-c", "approval_policy=\"never\"",
        "-o", last_msg_path,
    ]
    if args.model:
        cmd += ["-m", args.model]
    if args.log:
        cmd += ["--json"]
    # Operator escape hatch: extra raw flags appended verbatim.
    extra = os.environ.get("VOICEPROMPTKIT_CODEX_EXEC_FLAGS", "").strip()
    if extra:
        cmd += extra.split()
    cmd += ["-"]  # read the prompt from stdin

    if args.dry_run:
        print(f"[dry-run] lens={label}")
        print(f"[dry-run] codex_cli={codex_cli}")
        print(f"[dry-run] command={' '.join(cmd)}")
        print(f"[dry-run] prompt_chars={len(prompt)} "
              f"spec_chars={len(spec_body)} payload_chars={len(payload_text)}")
        print(f"[dry-run] expected_outputs={expected}")
        _safe_unlink(last_msg_path)
        return 0

    # --- Preflight: codex must be runnable (exit 2) -------------------------
    if not (os.path.exists(codex_cli) or shutil.which("codex")):
        print("error: 'codex' CLI not found on PATH. Install Codex first "
              "(https://github.com/openai/codex) or set VOICEPROMPTKIT_CODEX_CLI.",
              file=sys.stderr)
        _safe_unlink(last_msg_path)
        return 2

    # --- Run codex exec (exit 3 on failure / timeout) -----------------------
    log_handle = open(args.log, "w", encoding="utf-8") if args.log else None
    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=args.timeout,
        )
    except FileNotFoundError:
        print(f"error: could not execute {codex_cli!r} — is Codex installed?",
              file=sys.stderr)
        _safe_unlink(last_msg_path)
        if log_handle:
            log_handle.close()
        return 2
    except subprocess.TimeoutExpired:
        print(f"error: codex exec for {label} timed out after {args.timeout}s.",
              file=sys.stderr)
        _safe_unlink(last_msg_path)
        if log_handle:
            log_handle.close()
        return 3

    if log_handle:
        log_handle.write(result.stdout or "")
        log_handle.close()

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-800:]
        print(f"error: codex exec for {label} exited {result.returncode}: {tail}",
              file=sys.stderr)
        _safe_unlink(last_msg_path)
        return 3

    # --- Validate the runner produced valid JSON (exit 4 on miss) -----------
    missing = []
    for path in expected:
        if not os.path.exists(path):
            missing.append(f"{path} (not written)")
            continue
        try:
            with open(path, encoding="utf-8") as f:
                json.load(f)
        except (OSError, ValueError) as e:
            missing.append(f"{path} (invalid JSON: {e})")
    if missing:
        print(f"error: codex finished but expected output(s) for {label} are "
              f"missing/invalid:\n  " + "\n  ".join(missing), file=sys.stderr)
        _safe_unlink(last_msg_path)
        return 4

    # Surface the runner's status line (its single final message), if captured.
    status = ""
    try:
        with open(last_msg_path, encoding="utf-8") as f:
            status = f.read().strip()
    except OSError:
        pass
    _safe_unlink(last_msg_path)
    print(status or f"codex lens complete: {label} ({len(expected)} output(s))")
    return 0


def _safe_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
