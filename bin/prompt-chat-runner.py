#!/usr/bin/env python3
"""Bare-context Claude session orchestrator for /prompt-chat (v0.5.6).

Invoked by skills/prompt-chat-session/SKILL.md after Phase 0 validation.
This Python script owns the interactive REPL: reads stdin, handles slash
commands inline, and delegates non-slash user turns to a SINGLE long-lived
`claude` subprocess via stream-json input/output.

Why one long-lived subprocess (v0.5.6 design):
- Spawning `claude` per turn means subprocess startup overhead AND a
  permission prompt on every call (Claude Code asks before running a Bash
  command). Both are unacceptable for an interactive chat.
- `claude --input-format stream-json --output-format stream-json --verbose
  --print` keeps the subprocess alive for as long as stdin is open. Each
  user turn is one JSON-line written to stdin; each model reply arrives as
  events on stdout, terminating in a `type:"result"` event we extract.
- The subprocess is spawned ONCE at the start of the chat session, lives
  for the entire conversation, and is closed only on /quit (or /reset,
  which re-spawns a fresh one).

Why a stripped-down context:
- `--system-prompt-file <body>` makes the prompt body the entire system
  prompt. Combined with cwd=/tmp (a neutral dir, no CLAUDE.md to auto-
  discover), `--disable-slash-commands`, and `--allowedTools ""`, the
  Claude session has only the body + the conversation. No SKILL.md, no
  tool definitions, no project context — the ~32k token overhead per
  turn that v0.5.4 hit disappears.
- `--permission-mode bypassPermissions` skips the runtime permission
  dialog (the subprocess can never execute tools anyway thanks to
  `--allowedTools ""`, so this is safe in practice).

Usage: python3 prompt-chat-runner.py <run-dir>
"""
import argparse
import atexit
import datetime
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import uuid
from queue import Empty, Queue

# v0.5.11: readline is stdlib on macOS/Linux but not bundled on Windows.
# Import for its side effect — enables arrow-key history + line editing on
# the cooked-mode input() fallback (used when idle-silence is OFF, on
# Windows, or on a non-TTY stream). The v0.5.16 raw-mode reader does not
# use readline; readline-style history is intentionally absent there.
try:
    import readline  # noqa: F401
except ImportError:
    pass

# v0.5.16: raw-mode reader needs `tty` alongside `select` + `termios`.
# Cooked-mode `select()` only signals stdin readable on Enter, so a user
# actively typing a long reply got killed by the idle-silence timer mid-
# word (their buffer was then tcflushed and discarded — see v0.5.14 bug).
# Raw (cbreak) mode delivers each keystroke immediately, letting us reset
# the deadline on every byte. On Windows / non-Unix we degrade to plain
# input() with no idle timeout.
if sys.platform != "win32":
    try:
        import select as _select_mod
        import termios as _termios_mod
        import tty as _tty_mod
        _IDLE_TIMEOUT_OK = True
    except ImportError:
        _IDLE_TIMEOUT_OK = False
else:
    _IDLE_TIMEOUT_OK = False

# v0.5.25: auto-silence is OFF by default and stays the user's call to
# enable. Earlier versions tried to be clever (10s default in v0.5.14;
# 30s default in v0.5.22; prompt auto-detect in v0.5.24), but real
# voice-agent scripts use *layered* silence policies — `1st silence
# ≥15s`, `2nd silence ≥10s`, `3rd silence ≥5s` etc. Any single auto-
# fire threshold mis-represents that layering. Testers exercise the
# layered policy more naturally with manual `/silence N` calls: e.g.
# `/silence 15 → /silence 10 → /silence 5` walks the persona through
# all three stages with the script's own values.
#
# _SILENCE_ON_DEFAULT_SECS stays as the threshold `/silence-auto on`
# uses when the user explicitly wants auto-fire (advanced use case).
_SILENCE_ON_DEFAULT_SECS = 30

# v0.5.11: ANSI escape sequences for color. Stdlib-only (no rich/colorama).
# We only emit these when stdout is a real TTY — piping to a file would
# pollute it with raw escape codes otherwise.
_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    """Wrap text in ANSI color codes when stdout is a TTY; otherwise no-op."""
    if not _TTY:
        return text
    return f"\033[{code}m{text}\033[0m"


_COL_BOT       = "38;5;82"   # green  — persona reply (v0.5.13: was orange)
_COL_USER      = "38;5;214"  # orange — user input + right-aligned echo (v0.5.13)
_COL_SYS       = "38;5;245"  # gray   — system / meta messages
_COL_DIM       = "2"         # dim    — secondary text (timestamps, hints)
_COL_BOLD      = "1"         # bold   — emphasis
_COL_ERR       = "38;5;196"  # red    — errors
_COL_SPIN      = "38;5;82"   # green  — thinking spinner (matches bot)
_COL_ENDCALL   = "38;5;208"  # orange — end-call banner accent (v0.5.19)
_COL_OK        = "38;5;82"   # green  — summary card accent (v0.5.19)


# v0.5.19 — Unicode box-drawing helpers. Used by the welcome banner, the
# end-call banner, and the final summary card so the chat lifecycle reads
# as one coherent UI instead of three separately styled prints.
_BOX_TL, _BOX_TR = "╭", "╮"
_BOX_BL, _BOX_BR = "╰", "╯"
_BOX_H,  _BOX_V  = "─", "│"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(text: str) -> int:
    """Length of text with ANSI escape codes stripped — needed because we
    embed colored fragments inside box rows and the right border must
    align with the visible character count, not the byte count."""
    return len(_ANSI_RE.sub("", text))


def _render_box(title: str, lines: list[str], width: int | None = None) -> int:
    """Print a Unicode-bordered frame around `lines` with `title` in the
    top edge. Returns the number of rows printed (top border + N content
    rows + bottom border) so callers that need to know where the box
    ended (e.g. main()'s DECSTBM scroll-region calc in v0.5.26) can
    plan accordingly.
    """
    cols = _term_size()[0]
    width = width if width else min(78, max(40, cols - 2))
    inner = max(20, width - 2)
    bar_v = _c(_COL_DIM, _BOX_V)

    if title:
        title_seg = f" {title} "
        seg_visible = _visible_len(title_seg)
        head_fill = max(0, inner - seg_visible - 1)
        top = (_c(_COL_DIM, _BOX_TL + _BOX_H) + title_seg
               + _c(_COL_DIM, _BOX_H * head_fill + _BOX_TR))
    else:
        top = _c(_COL_DIM, _BOX_TL + _BOX_H * inner + _BOX_TR)
    bot = _c(_COL_DIM, _BOX_BL + _BOX_H * inner + _BOX_BR)

    print(top)
    for line in lines:
        vis = _visible_len(line)
        if vis > inner - 2:
            line = line[: inner - 3] + "…"
            vis = _visible_len(line)
        pad = " " * max(0, inner - 2 - vis)
        print(f"{bar_v} {line}{pad} {bar_v}")
    print(bot)
    return 2 + len(lines)


# v0.5.9 — random Turkish caller name pool for filling [MÜŞTERİ_ADI] /
# [MÜŞTERİ_SOYADI] placeholders that voice-agent scripts inherit from
# Vapi's caller variables. In production Vapi fills these from the
# dialled number's CRM record; in our simulator we synthesise a plausible
# name so the bot doesn't have to ask the user.
_TR_FIRST_NAMES = [
    "Ahmet", "Mehmet", "Mustafa", "Ali", "Hüseyin", "Hasan", "İbrahim",
    "Ayşe", "Fatma", "Emine", "Hatice", "Zeynep", "Elif", "Merve",
    "Onur", "Burak", "Emre", "Can", "Selin", "Deniz", "Ece",
]
_TR_LAST_NAMES = [
    "Yılmaz", "Kaya", "Demir", "Şahin", "Çelik", "Yıldız", "Yıldırım",
    "Öztürk", "Aydın", "Özdemir", "Arslan", "Doğan", "Kılıç", "Aslan",
    "Çetin", "Kara", "Koç", "Kurt", "Özkan", "Şimşek",
]


NEUTRAL_CWD = "/tmp"  # subprocess cwd — no CLAUDE.md auto-discovery here
CLAUDE_CLI = shutil.which("claude") or "/Users/onur/.local/bin/claude"

# v0.5.9 — text-based stand-in for production Vapi's `end-call-tool`. The bare
# claude subprocess has no tools available (--allowedTools ""), so when the
# persona's script says "end the call now", the persona instead emits this
# marker on its own line as the tail of its final reply. Python detects it,
# strips it from the user-facing text, and auto-exits the REPL.
END_CALL_MARKER = "<<END_CALL>>"


# v0.5.8 — persona roleplay wrapper. body.txt is a voice-agent script (rules,
# STEPs, internal notes) but doesn't tell the model "you are now this persona,
# the user IS the caller". Without that framing, the model reads body as
# developer instructions and offers meta menus ("Which scenario do you want
# to test?") instead of greeting in character. The wrapper below makes the
# roleplay contract explicit. The full body content follows the framing
# verbatim — no body modification.
_ROLEPLAY_FRAMING = """You are now ROLEPLAYING as the persona described in the script below. The human chatting with you is your CALLER — a customer on the other end of a phone line. This is a TEXT SIMULATION of a real call, but as far as you are concerned, the call is LIVE and you are mid-conversation.

ABSOLUTE RULES (these OVERRIDE anything in the script that contradicts them):

1. **Stay in character at all times.** You are the persona. Never break frame. Never describe yourself as an AI, simulator, agent, or chatbot unless the script's own disclosure rules require it.

2. **Initiate the conversation.** When the user sends their first message — even just "merhaba", "hi", "evet", or any short greeting — treat it as the phone being answered. Respond AS the persona would on a real outbound call, following whatever opening / greeting the script prescribes. Do NOT ask the user what they want to do. Do NOT offer menus. The user is a caller, not a developer.

3. **No meta-menus, no scenario selection, no implementation talk.** The user is NEVER offered choices like "1. Test the flow 2. Explain the rules 3. Implement in code". The user is a customer. If the user's message sounds confused or off-script ("simüle edelim", "let's test", "what scenario should we run"), interpret it as the caller being momentarily distracted, and continue with your script's next natural beat — do NOT step out and explain.

4. **Honour every safety / scope / format rule in the script** — phrase bans, refund policies, what to disclose, what to NEVER mention (passwords, codes, credentials, sensitive data the script forbids the persona from requesting). When the user asks for something the script bans, refuse in character, the way the script's persona would.

5. **One response per turn.** Match the script's length / question-count / pacing rules. Do not provide alternatives, do not preamble, do not analyse — just say what the persona would say next.

6. **Language follows the script + the caller.** Speak whatever language the script prescribes; mirror the caller's language when the script allows.

7. **No emojis unless the script's tone explicitly calls for them.** Voice agents that get rendered into TTS cannot pronounce emojis — keep them out unless the script style guide says otherwise.

8. **Mid-call interruptions, silences, off-topic comments from the caller are EXPECTED.** Handle them per the script's interruption / silence / off-scope rules. Do not break character to comment on the disruption. When you receive a user message that matches `[silence for N seconds]` (case-insensitive), treat it as the caller saying NOTHING for N seconds.

   **STRICTLY enforce your script's silence thresholds.** If your script says "1st silence ≥15s" and N=9, the threshold is NOT met — DO NOT emit a silence-recovery line, just continue waiting (a brief continuation phrase is acceptable only if your script explicitly allows it). Apply the silence-recovery line(s) ONLY when N meets or exceeds the threshold your script prescribes. Use the EXACT wording your script gives for each silence beat; do not improvise the wording or invent new lines. Never respond as if "silence for 6 seconds" were spoken text.

9. **Internal call-state triggers.** Messages wrapped in `[SYSTEM: ...]` are NOT from the caller — they are internal call infrastructure events from the test harness. Process them silently and respond per the cue:
   - `[SYSTEM: call connected — caller is <Name Surname>]` — the phone just rang and the caller picked up. Deliver your script's opening greeting NOW, in character. Use the caller's name where your script asks for `[MÜŞTERİ_ADI]` / `[MÜŞTERİ_SOYADI]` / `<caller_name>` or similar placeholders. Do NOT ask the user for their name — you ALREADY have it. Do NOT echo the bracketed system text in your reply.
   - Any other `[SYSTEM: ...]` message — apply the literal cue (e.g. "caller hung up", "transfer completed") and continue.

10. **Caller variable placeholders in the script.** When your script contains tokens like `[MÜŞTERİ_ADI]`, `[MÜŞTERİ_SOYADI]`, `<customer_name>`, `{{caller_name}}`, etc., fill them with the caller name supplied by the `[SYSTEM: call connected]` cue. If no name was provided OR your script needs other personal data (date of birth, phone number, account ID) that wasn't supplied, INVENT plausible Turkish defaults — do not ask the user to provide them. Production Vapi fills these from CRM; you are simulating that.

11. **Ending the call — text-based end-call marker (MANDATORY on closing lines).** Production Vapi gives you an `end-call-tool` you can invoke to hang up. In this simulator that tool does not exist; instead, the contract is:

   **WHENEVER your reply contains a closing phrase** (e.g. "Hoşça kalın", "İyi günler", "Görüşürüz", "Aramamıza son veriyorum", "Goodbye", "Have a good day", "Thanks for calling, bye" — any farewell that signals the call is OVER, regardless of why: customer rescheduled, customer refused, customer completed the flow, customer hung up, transfer completed, etc.) — you MUST append the marker.

   Format (strict):
   - Your normal closing line (one sentence, in character).
   - A blank line OR newline.
   - The marker, exactly: `<<END_CALL>>`
   - Nothing after the marker. No explanation. No additional pleasantries.

   Example correct:
   ```
   Anladım. Sizi uygun bir zamanda tekrar arayacağız. Hoşça kalın.

   <<END_CALL>>
   ```

   **This is NOT optional.** If you write a closing phrase WITHOUT the marker, the chat session stays open and the user has to manually exit — that is a bug, not a graceful end. Production Vapi would have called end-call-tool here; in this simulator the marker is the equivalent. Forgetting it = forgetting to hang up.

   The only time you do NOT emit the marker is when your reply is a mid-conversation turn that does NOT end the call (asking the user a question, providing information, etc.). If you're saying any form of "goodbye", emit the marker.

   The harness watches for this marker, strips it before displaying your reply to the user, and closes the chat session automatically (equivalent to running /quit). DO NOT use the marker in non-ending replies — false positives hang up the call prematurely. ONLY at the point your script would have called end-call-tool.

12. **Persona-name metadata on your VERY FIRST reply.** On your opening turn (the response to `[SYSTEM: call connected — caller is ...]`) — and ONLY then — prefix your reply with a metadata line in this exact format:
   ```
   [PERSONA_NAME: <your name as the persona — first name is enough>]
   ```
   Put that bracketed line FIRST, then a newline, then your normal opening greeting. Example:
   ```
   [PERSONA_NAME: Aysel]
   Merhaba Zeynep! Aysel'im. ...
   ```
   The harness strips this line before showing the reply to the user, and uses the name to prefix subsequent assistant turns ("❝ Aysel: ..."). Do NOT include the bracketed line on any subsequent reply — only the first. If your script doesn't give you a name, invent one in keeping with the persona's tone.

YOUR PERSONA SCRIPT — internalise this as your voice, your rules, your scope. The call begins when you receive the `[SYSTEM: call connected — caller is ...]` cue (this happens automatically right after this prompt loads).

═══════════════════════════════════════════════════════════════════════════════

"""


def _safe_unlink(path: str) -> None:
    """Best-effort delete used by the atexit hook below; we never want
    cleanup to crash the interpreter shutdown if the file is already
    gone or its directory has been swept by /tmp's maid."""
    try:
        os.unlink(path)
    except OSError:
        pass


def _write_wrapped_body(body_path: str) -> str:
    """Write a persona-framed copy of body.txt that claude will receive
    as the --system-prompt-file payload. The original body.txt is never
    modified.

    v0.5.22: wrapped body lives in a per-process tempfile under /tmp
    (e.g. `/tmp/promptchat-XXXX.body-wrapped.txt`) instead of cluttering
    every chat run dir with a duplicate of body+framing. The framing
    block is shared across all chats; persisting a copy alongside body.
    txt added storage with no real benefit (framing edits propagate
    from the script anyway). atexit cleans the tempfile up on normal
    Python shutdown; OS /tmp policy handles ungraceful exits.
    """
    body = open(body_path, encoding="utf-8").read()
    wrapped = _ROLEPLAY_FRAMING + body
    fd, path = tempfile.mkstemp(suffix=".body-wrapped.txt", prefix="promptchat-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(wrapped)
    except Exception:
        _safe_unlink(path)
        raise
    atexit.register(_safe_unlink, path)
    return path


# --------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="Absolute path to .promptcheck/<basename>/chat-NNN/")
    args = parser.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        print(f"error: run_dir not found: {run_dir}", file=sys.stderr)
        return 2

    # State files (created by /prompt-chat Phase 0).
    body_path = os.path.join(run_dir, "body.txt")
    session_path = os.path.join(run_dir, "session.json")
    chat_jsonl_path = os.path.join(run_dir, "chat.jsonl")
    staged_path = os.path.join(run_dir, "saved_anchors.json")

    for f in ["body.txt", "frontmatter.json", "chat.jsonl", "saved_anchors.json", "session.json"]:
        if not os.path.exists(os.path.join(run_dir, f)):
            print(f"error: missing state file {f} — was Phase 0 skipped?", file=sys.stderr)
            return 2

    with open(os.path.join(run_dir, "frontmatter.json"), encoding="utf-8") as f:
        fm = json.load(f)
    with open(session_path, encoding="utf-8") as f:
        session = json.load(f)

    abs_prompt = session["prompt_path"]
    report_language = fm.get("report_language", "tr")
    chat_model = fm.get("chat_model_alias") or "haiku"

    # Persist the chat_session_uuid across runs of this script. /reset clears it.
    if not session.get("chat_session_uuid"):
        session["chat_session_uuid"] = str(uuid.uuid4())
        _atomic_write_json(session_path, session)
    chat_session_uuid = session["chat_session_uuid"]

    # v0.5.19: stamp the session start time the first time this run dir is
    # opened, so the final summary card can show a wall-clock duration.
    # /reset (and the post-call auto-restart path) refresh this stamp via
    # _start_fresh_call, scoping "duration" to the current call rather
    # than the original Phase 0 creation moment.
    if not session.get("session_started_at"):
        session["session_started_at"] = datetime.datetime.now(
            datetime.timezone.utc).isoformat()
        _atomic_write_json(session_path, session)

    # v0.5.25: auto-silence is off by default — see comment on
    # _SILENCE_ON_DEFAULT_SECS for the rationale (layered prompt policies
    # don't map cleanly to a single auto-fire threshold).
    if "idle_silence_secs" not in session:
        session["idle_silence_secs"] = 0
        _atomic_write_json(session_path, session)

    if not (os.path.exists(CLAUDE_CLI) or shutil.which("claude")):
        msg = ("error: 'claude' CLI not found on PATH. Install Claude Code first."
               if report_language == "en"
               else "hata: 'claude' CLI bulunamadı. Önce Claude Code kur.")
        print(msg, file=sys.stderr)
        return 2

    # v0.5.9: pick a random Turkish caller identity for this chat session.
    # Persisted in session.json so /reset keeps the same caller (until they
    # explicitly re-roll via a future command) — fills [MÜŞTERİ_ADI] etc.
    if not session.get("caller_name"):
        first = random.choice(_TR_FIRST_NAMES)
        last = random.choice(_TR_LAST_NAMES)
        session["caller_name"] = f"{first} {last}"
        _atomic_write_json(session_path, session)
    caller_name = session["caller_name"]

    # v0.5.27: alt-screen + DECSTBM removed (see _enter_alt_screen
    # docstring). Welcome banner prints inline in the user's terminal;
    # _print_welcome's return value (row count) is no longer used for
    # scroll-region setup but kept for non-TTY logging.
    _enter_alt_screen()  # no-op in v0.5.27
    _print_welcome(run_dir, abs_prompt, chat_model, report_language,
                   caller_name, session)
    _init_pin()  # no-op in v0.5.27

    # v0.5.22: clean up any pre-v0.5.22 .body-wrapped.txt left in this
    # run dir before we materialise the fresh tempfile copy. Silent;
    # missing file is the common case after the first upgrade.
    _safe_unlink(os.path.join(run_dir, ".body-wrapped.txt"))

    # v0.5.8: wrap body.txt with persona roleplay framing before passing to
    # claude. The original body.txt is left untouched.
    # v0.5.22: wrapped body now lives in /tmp (tempfile) instead of inside
    # the run dir — see _write_wrapped_body.
    wrapped_body_path = _write_wrapped_body(body_path)

    # Spawn the long-lived claude subprocess ONCE.
    ctx = _SubprocessCtx(wrapped_body_path, chat_session_uuid, chat_model)
    ctx.start()

    # v0.5.9: bot greets FIRST. After welcome, send a [SYSTEM: call connected]
    # trigger so the persona delivers its opening line before we hand the
    # terminal to the user — matching production Vapi behaviour where the
    # bot speaks first when the call connects.
    initial_post_call = False
    if session.get("turns", 0) == 0:
        try:
            print()  # blank line before the opening
            # We can't stream the opening directly to stdout because the bot
            # is required to emit [PERSONA_NAME: X] as its first line; that
            # bracketed marker would flash on screen if we passed
            # stream_to=sys.stdout. Buffer the opening, strip the marker,
            # THEN print the clean reply with the persona prefix.
            # v0.5.13: persona name unknown yet — spinner uses "Bot" label.
            opening_raw = _send_with_spinner(
                ctx,
                f"[SYSTEM: call connected — caller is {caller_name}]",
                None,
                report_language,
            )
            opening_no_end, opening_ended = _strip_end_call_marker(opening_raw)
            opening_clean, persona_name = _strip_persona_name_marker(opening_no_end)
            if persona_name and not session.get("persona_name"):
                session["persona_name"] = persona_name
            opening_clean = opening_clean.strip()
            _append_chat_entry(chat_jsonl_path, "assistant", opening_clean)
            session["turns"] = 1
            _atomic_write_json(session_path, session)
            _render_bot_reply(opening_clean, session.get("persona_name"))
            _render_footer(session["turns"], report_language,
                           post_call=opening_ended)
            if opening_ended:
                # v0.5.19: persona refused the call on opening (rare —
                # "wrong number / decline" branches). Used to auto-exit;
                # now we drop into post-call mode so the user can still
                # /save the opening turn or /quit explicitly.
                _render_end_call_box(report_language)
                _render_footer(session["turns"], report_language,
                               post_call=True)
                initial_post_call = True
        except RuntimeError as e:
            print(_c(_COL_ERR, f"\n[chat error during opening: {e}]\n"), file=sys.stderr)

    try:
        return _repl(ctx, run_dir, body_path, session_path, chat_jsonl_path,
                    staged_path, abs_prompt, report_language, session,
                    initial_post_call=initial_post_call)
    finally:
        ctx.close()
        # v0.5.26: _release_pin and _leave_alt_screen are idempotent —
        # _exit_cleanly may have already called them before printing
        # the summary card, in which case these are no-ops.
        _release_pin()
        _leave_alt_screen()


# v0.5.12: closing phrase heuristic backup for end-call detection.
# The model SHOULD emit <<END_CALL>> per framing rule 11, but in long
# conversations it occasionally forgets the marker after a closing line.
# This regex catches the most common goodbye phrases in TR + EN and
# treats the call as ended even when the marker is absent. False
# positives are unlikely — these phrases rarely appear mid-conversation.
_CLOSING_PHRASES_RE = re.compile(
    r"\b("
    r"ho[şs]ça?\s*kal(?:[ıi]n|abil|maca|ar)?"      # hoşça kal(ın), hoşçakal, etc.
    r"|iyi\s+g[üu]nler"                             # iyi günler
    r"|iyi\s+ak[şs]amlar"                           # iyi akşamlar
    r"|g[öo]r[üu][şs][üu]r[üu]z"                    # görüşürüz
    r"|kendinize\s+iyi\s+bak"                       # kendinize iyi bakın
    r"|g[üu]le\s+g[üu]le"                           # güle güle
    r"|teşekk[üu]rler[, ]+iyi\s+g[üu]nler"           # teşekkürler, iyi günler
    r"|aramam[ıi]za\s+son\s+veriyor"                # aramamıza son veriyorum
    r"|goodbye"
    r"|have\s+a\s+(?:good|nice|great)\s+(?:day|evening|night)"
    r"|talk\s+to\s+you\s+(?:soon|later)"
    r"|thanks?\s+for\s+calling"
    r"|bye[\.\s!]"
    r")",
    re.IGNORECASE,
)


def _strip_end_call_marker(reply: str) -> tuple[str, bool]:
    """Returns (clean_reply, end_call_signalled).

    Detection has two paths:
    1. Explicit marker `<<END_CALL>>` (per framing rule 11) — strict, primary.
    2. v0.5.12 heuristic: closing phrase regex on the reply text — backup
       for when the model forgets the marker after a closing line.

    Strip the marker (if any) and return whether the call should end.
    """
    if END_CALL_MARKER in reply:
        cleaned = reply.replace(END_CALL_MARKER, "").rstrip()
        return cleaned, True
    if _CLOSING_PHRASES_RE.search(reply):
        return reply.rstrip(), True
    return reply, False


# v0.5.11: persona-name metadata regex. Bot writes `[PERSONA_NAME: X]` as the
# first line of its opening reply; we extract X and strip the line.
_PERSONA_NAME_RE = re.compile(r"^\s*\[PERSONA_NAME:\s*([^\]\n]+?)\s*\]\s*\n?", re.MULTILINE)


def _strip_persona_name_marker(reply: str) -> tuple[str, str | None]:
    """Returns (clean_reply, persona_name_or_None)."""
    m = _PERSONA_NAME_RE.search(reply)
    if not m:
        return reply, None
    name = m.group(1).strip()
    cleaned = _PERSONA_NAME_RE.sub("", reply, count=1).lstrip("\n")
    return cleaned, name


# ------------------------------------------------------- subprocess context


class _SubprocessCtx:
    """Wraps the long-lived `claude --input-format stream-json` subprocess.

    Spawns once, talks via stdin/stdout (line-delimited JSON), shuts down on
    .close(). Reads stdout in a background thread to avoid blocking the
    REPL when the model is producing a reply.
    """

    def __init__(self, body_path: str, session_uuid: str, model: str):
        self.body_path = body_path
        self.session_uuid = session_uuid
        self.model = model
        self.proc: subprocess.Popen | None = None
        self.stdout_queue: Queue = Queue()
        self.stderr_buf: list[str] = []
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._first_send_done = False

    def start(self) -> None:
        cmd = [
            CLAUDE_CLI,
            "--system-prompt-file", self.body_path,
            "--session-id", self.session_uuid,
            "--print",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",                          # required for stream-json output
            "--include-partial-messages",          # v0.5.6: stream text deltas for low TTFT
            "--effort", "low",                     # v0.5.11: persona roleplay doesn't need extended thinking — ~%19 faster
            "--exclude-dynamic-system-prompt-sections",  # v0.5.11: better cross-turn cache reuse
            "--model", self.model,
            "--disable-slash-commands",
            "--allowedTools", "",
            "--permission-mode", "bypassPermissions",
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,                            # line-buffered
            cwd=NEUTRAL_CWD,
        )

        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            self.stdout_queue.put(line)
        self.stdout_queue.put(None)  # sentinel: eof

    def _read_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr_buf.append(line.rstrip())

    def send(self, user_input: str, timeout: int = 180,
             stream_to: "io.TextIOBase | None" = None) -> str:
        """Send one user turn; block until the matching result event.

        When `stream_to` is provided (typically sys.stdout), `text_delta`
        chunks are flushed there as they arrive — gives the user instant
        feedback even before the full reply is ready. `thinking_delta`
        chunks are silently discarded (internal model reasoning, not user-
        facing).
        """
        assert self.proc is not None and self.proc.stdin is not None
        if self.proc.poll() is not None:
            raise RuntimeError(f"claude subprocess died: {self._stderr_tail()}")

        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": user_input}],
            },
        }
        try:
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except BrokenPipeError:
            raise RuntimeError(f"claude subprocess broken pipe: {self._stderr_tail()}")

        chunks: list[str] = []
        deadline = _now_monotonic() + timeout
        while _now_monotonic() < deadline:
            try:
                line = self.stdout_queue.get(timeout=1.0)
            except Empty:
                continue
            if line is None:
                raise RuntimeError(f"claude subprocess EOF before result: {self._stderr_tail()}")
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "stream_event":
                inner = evt.get("event", {})
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        chunks.append(text)
                        if stream_to is not None:
                            stream_to.write(text)
                            stream_to.flush()
                # thinking_delta intentionally ignored (model internal reasoning)
            elif etype == "result":
                if evt.get("is_error"):
                    raise RuntimeError(f"claude result is_error: {evt.get('result', '')[:300]}")
                # Prefer the streamed chunks (incremental) over the result string;
                # they're identical content, but chunks reflect what the user saw.
                full = "".join(chunks).strip() if chunks else (evt.get("result") or "").strip()
                return full
        raise RuntimeError(f"claude timed out waiting for reply (>{timeout}s)")

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def _stderr_tail(self) -> str:
        return "\n".join(self.stderr_buf[-10:])


def _now_monotonic() -> float:
    import time
    return time.monotonic()


# -------------------------------------------------------------- REPL loop


def _repl(ctx: "_SubprocessCtx", run_dir: str, body_path: str, session_path: str,
          chat_jsonl_path: str, staged_path: str, abs_prompt: str,
          report_language: str, session: dict,
          initial_post_call: bool = False) -> int:
    # v0.5.14: idle-silence threshold. Persisted on session so /reset
    # keeps the user's preference. 0 / negative disables. Unix-only —
    # _read_with_idle_timeout transparently falls back to blocking
    # input() on Windows or non-TTY streams.
    # v0.5.24: the threshold is now set up-front in main() via
    # _detect_silence_threshold (reads frontmatter / body) instead of
    # being defaulted here.

    # v0.5.19: post-call mode. When the persona ends the call (END_CALL
    # marker or closing-phrase regex) we no longer auto-exit; this flag
    # stays True until either (a) the user types a non-slash message,
    # which restarts the call with that message as the first user turn,
    # or (b) the user explicitly /quits / Ctrl-D's out.
    # `initial_post_call` lets main() seed the loop already in post-call
    # mode when the persona ends the call on its opening reply.
    in_post_call = initial_post_call

    while True:
        # Idle-silence is meaningless in post-call mode (there's no call
        # to be silent on). Suppress the timer so an idle user lingering
        # on the post-call screen isn't ambushed by a silence event aimed
        # at a persona that already hung up.
        if in_post_call:
            idle_arg = None
        else:
            idle = session.get("idle_silence_secs", 0) or 0
            idle_arg = idle if idle > 0 and _IDLE_TIMEOUT_OK else None

        try:
            footer_line = _compute_footer_line(
                session.get("turns", 0), report_language,
                post_call=in_post_call,
            )
            user_input, timed_out = _input_prompt(idle_arg, report_language,
                                                   footer_line)
        except EOFError:
            print()
            _exit_cleanly(run_dir, session_path, chat_jsonl_path, staged_path,
                          abs_prompt, report_language, "eof", session)
            return 0
        except KeyboardInterrupt:
            print()
            _exit_cleanly(run_dir, session_path, chat_jsonl_path, staged_path,
                          abs_prompt, report_language, "interrupt", session)
            return 0

        if timed_out:
            # v0.5.14: auto-silence. The user sat idle past the threshold;
            # treat it as the caller staying quiet on the line and let the
            # persona's silence policy decide what to do (gentle prompt,
            # escalation, eventual hangup). v0.5.19: if the persona ends
            # the call from a silence event, enter post-call mode instead
            # of exiting so the user can still /save.
            duration = int(idle_arg or _DEFAULT_IDLE_SILENCE_SECS)
            banner = (f"  (otomatik sessizlik: {duration} sn — boş bekleme)"
                      if report_language == "tr"
                      else f"  (auto-silence: {duration}s — caller idle)")
            print(_c(_COL_SYS, banner))
            ended = _exchange_turn(
                ctx, f"[silence for {duration} seconds]",
                chat_jsonl_path, session_path, session, report_language,
            )
            if ended:
                _render_end_call_box(report_language)
                _render_footer(session["turns"], report_language, post_call=True)
                in_post_call = True
            continue

        user_input = (user_input or "").strip()
        if not user_input:
            continue

        if user_input.startswith("/"):
            # Parse the command name up-front so we can detect /reset
            # and clear post_call below — /reset starts a fresh call so
            # the user is no longer "post-call".
            parts = user_input.lstrip("/").strip().split(None, 1)
            cmd_word = parts[0].lower() if parts else ""

            should_quit, entered_post_call = _dispatch_slash(
                user_input, run_dir, session_path,
                chat_jsonl_path, staged_path,
                abs_prompt, report_language, session, ctx,
            )
            if should_quit:
                return 0
            if entered_post_call:
                # /silence just ended the call — flip into post-call mode.
                in_post_call = True
            elif in_post_call and cmd_word == "reset":
                in_post_call = False
            elif in_post_call:
                # Re-paint the post-call footer in case the slash command's
                # output scrolled past it (e.g., /history dumps many lines).
                _render_footer(session["turns"], report_language, post_call=True)
            continue

        if in_post_call:
            # v0.5.19: non-slash text after the previous call ended ==
            # "start a new call, and this is what I want to say first".
            # _start_fresh_call archives history, mints a new caller,
            # renders the persona's opening, then sends `user_input` as
            # the first user turn — preserving whatever the user typed
            # instead of discarding it.
            _archive, ended_again = _start_fresh_call(
                ctx, run_dir, chat_jsonl_path, session_path, session,
                report_language, pending_user_msg=user_input,
            )
            if ended_again:
                _render_end_call_box(report_language)
                _render_footer(session["turns"], report_language, post_call=True)
                # in_post_call stays True
            else:
                in_post_call = False
            continue

        # Normal turn — delegate to the shared exchange helper.
        print()  # blank line before the reply
        ended = _exchange_turn(ctx, user_input, chat_jsonl_path, session_path,
                               session, report_language)
        if ended:
            # v0.5.19: persona signalled end-of-call. Previously this
            # auto-exited via _exit_cleanly; that left no room to /save
            # or /commit. Now we drop into post-call mode — slash
            # commands still work, the footer advertises the options,
            # and a non-slash message restarts the call.
            _render_end_call_box(report_language)
            _render_footer(session["turns"], report_language, post_call=True)
            in_post_call = True


# ---------------------------------------------------------- slash commands


def _dispatch_slash(user_input: str, run_dir: str, session_path: str,
                    chat_jsonl_path: str, staged_path: str, abs_prompt: str,
                    report_language: str, session: dict,
                    ctx: "_SubprocessCtx") -> tuple[bool, bool]:
    """Returns (should_quit, entered_post_call).

    `entered_post_call` is True when a slash command produced a turn that
    ended the call — currently only /silence can do this (it sends a
    silence event to the persona, who may invoke the silence-policy
    hangup). REPL uses the flag to flip into post-call mode after the
    dispatch returns.
    """
    parts = user_input.lstrip("/").strip().split(None, 1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    entered_post_call = False
    if cmd == "save":
        _handle_save(chat_jsonl_path, staged_path, report_language, session_path, session)
    elif cmd == "history":
        _handle_history(chat_jsonl_path, report_language, session.get("persona_name"))
    elif cmd == "reset":
        _handle_reset(run_dir, chat_jsonl_path, session_path, session, report_language, ctx)
    elif cmd == "silence":
        entered_post_call = _handle_silence(
            arg, ctx, chat_jsonl_path, session_path, session, report_language,
        )
    elif cmd in ("silence-auto", "silenceauto", "autosilence"):
        _handle_silence_auto(arg, session_path, session, report_language)
    elif cmd == "commit":
        _handle_commit(abs_prompt, staged_path, session_path, session, report_language)
    elif cmd == "help":
        _handle_help(report_language)
    elif cmd == "quit":
        _exit_cleanly(run_dir, session_path, chat_jsonl_path, staged_path,
                      abs_prompt, report_language, "user_quit", session)
        return True, False
    else:
        msg = (f"Unknown command: /{cmd}. Try /help."
               if report_language == "en"
               else f"Bilinmeyen komut: /{cmd}. /help yaz.")
        print(_c(_COL_ERR, msg))
    return False, entered_post_call


def _handle_help(report_language: str) -> None:
    """v0.5.11: dump the command crib at any time during the chat."""
    if report_language == "tr":
        print()
        print(_c(_COL_BOLD, "Komutlar:"))
        print(f"  {_c(_COL_USER, '/save')}                 — son turu anchor olarak kaydet (staging)")
        print(f"  {_c(_COL_USER, '/history')}              — bu oturumdaki turları göster")
        print(f"  {_c(_COL_USER, '/reset')}                — geçmişi sil, baştan başla (fresh persona + arayan)")
        print(f"  {_c(_COL_USER, '/silence <N>')}          — N saniye sessizlik simüle et (manuel)")
        print(f"  {_c(_COL_USER, '/silence-auto [on|off|N]')} — boş bekleyince otomatik sessizlik (kapalı; açıldığında {_SILENCE_ON_DEFAULT_SECS} sn)")
        print(f"  {_c(_COL_USER, '/commit')}               — staged anchor'ları sidecar dosyaya yaz")
        print(f"  {_c(_COL_USER, '/help')}                 — bu listeyi göster")
        print(f"  {_c(_COL_USER, '/quit')}                 — çıkış + final summary")
        print()
    else:
        print()
        print(_c(_COL_BOLD, "Commands:"))
        print(f"  {_c(_COL_USER, '/save')}                 — capture last turn as a test anchor (staging)")
        print(f"  {_c(_COL_USER, '/history')}              — show this session's turns")
        print(f"  {_c(_COL_USER, '/reset')}                — discard history, fresh persona + caller")
        print(f"  {_c(_COL_USER, '/silence <N>')}          — simulate N seconds of caller silence (manual)")
        print(f"  {_c(_COL_USER, '/silence-auto [on|off|N]')} — fire silence after idle (off; when enabled {_SILENCE_ON_DEFAULT_SECS}s)")
        print(f"  {_c(_COL_USER, '/commit')}               — write staged anchors to the sidecar file")
        print(f"  {_c(_COL_USER, '/help')}                 — show this list")
        print(f"  {_c(_COL_USER, '/quit')}                 — exit with a final summary")
        print()


def _handle_silence(arg: str, ctx: "_SubprocessCtx", chat_jsonl_path: str,
                    session_path: str, session: dict,
                    report_language: str) -> bool:
    """v0.5.9: simulate N seconds of caller silence as a user turn.

    Sends the opaque-string convention `[silence for N seconds]` (the same
    pattern drift-runner uses when expanding silence_input sugar) to the
    bare subprocess. Framing rule 8 tells the persona how to react.
    v0.5.14: turn-execution refactored into _exchange_turn.

    v0.5.19: returns True when the persona ended the call from the
    silence event (used to be a hard SystemExit; now the REPL flips into
    post-call mode instead so the user can still /save / /commit).
    """
    try:
        duration = int(arg)
        if duration <= 0:
            raise ValueError
    except (ValueError, TypeError):
        msg = ("usage: /silence <positive integer>"
               if report_language == "en"
               else "kullanım: /silence <pozitif tamsayı>")
        print(msg)
        return False

    print()  # blank line before reply
    print(_c(_COL_SYS, f"  (sessizlik: {duration} saniye)" if report_language == "tr"
              else f"  (silence: {duration} seconds)"))
    ended = _exchange_turn(
        ctx, f"[silence for {duration} seconds]",
        chat_jsonl_path, session_path, session, report_language,
    )
    if ended:
        _render_end_call_box(report_language)
        _render_footer(session["turns"], report_language, post_call=True)
        return True
    return False


def _handle_silence_auto(arg: str, session_path: str, session: dict,
                         report_language: str) -> None:
    """v0.5.14: configure the idle-silence auto-timeout.

      /silence-auto              → show current setting
      /silence-auto off          → disable auto-silence (manual /silence only)
      /silence-auto on           → enable with the default threshold (10s)
      /silence-auto <N>          → enable with custom threshold N seconds
                                   (N=0 disables; alias for off)
    """
    arg_l = (arg or "").strip().lower()
    cur = int(session.get("idle_silence_secs", 0) or 0)

    if not arg_l:
        if not _IDLE_TIMEOUT_OK:
            msg = ("Otomatik sessizlik bu platformda desteklenmiyor (Windows). "
                   "Manuel /silence <N> kullan."
                   if report_language == "tr"
                   else "Auto-silence is not supported on this platform "
                        "(Windows). Use manual /silence <N>.")
        elif cur > 0:
            msg = (f"Otomatik sessizlik AÇIK · eşik: {cur} sn. "
                   f"Kapatmak için: /silence-auto off"
                   if report_language == "tr"
                   else f"Auto-silence ON · threshold: {cur}s. "
                        f"Disable: /silence-auto off")
        else:
            msg = (f"Otomatik sessizlik KAPALI. Açmak için: /silence-auto on "
                   f"(varsayılan {_SILENCE_ON_DEFAULT_SECS} sn)"
                   if report_language == "tr"
                   else f"Auto-silence OFF. Enable: /silence-auto on "
                        f"(default {_SILENCE_ON_DEFAULT_SECS}s)")
        print(msg)
        return

    if arg_l in ("off", "kapat", "kapali", "kapalı", "0"):
        session["idle_silence_secs"] = 0
        _atomic_write_json(session_path, session)
        print("Otomatik sessizlik kapatıldı." if report_language == "tr"
              else "Auto-silence disabled.")
        return

    if arg_l in ("on", "ac", "aç", "open"):
        session["idle_silence_secs"] = _SILENCE_ON_DEFAULT_SECS
        _atomic_write_json(session_path, session)
        msg = (f"Otomatik sessizlik açıldı · eşik: {_SILENCE_ON_DEFAULT_SECS} sn."
               if report_language == "tr"
               else f"Auto-silence enabled · threshold: {_SILENCE_ON_DEFAULT_SECS}s.")
        print(msg)
        return

    try:
        n = int(arg_l)
        if n < 0:
            raise ValueError
    except ValueError:
        msg = ("kullanım: /silence-auto [on|off|<saniye>]"
               if report_language == "tr"
               else "usage: /silence-auto [on|off|<seconds>]")
        print(msg)
        return

    session["idle_silence_secs"] = n
    _atomic_write_json(session_path, session)
    if n == 0:
        print("Otomatik sessizlik kapatıldı." if report_language == "tr"
              else "Auto-silence disabled.")
    else:
        msg = (f"Otomatik sessizlik eşiği: {n} sn."
               if report_language == "tr"
               else f"Auto-silence threshold: {n}s.")
        print(msg)


def _handle_save(chat_jsonl_path: str, staged_path: str, report_language: str,
                 session_path: str, session: dict) -> None:
    entries = _read_chat_jsonl(chat_jsonl_path)
    last_user_idx = last_assistant_idx = None
    for i in range(len(entries) - 1, -1, -1):
        if (entries[i].get("role") == "assistant" and i > 0
                and entries[i - 1].get("role") == "user"):
            last_assistant_idx = i
            last_user_idx = i - 1
            break
    if last_user_idx is None:
        msg = ("No complete user→assistant turn yet. Chat first, then /save."
               if report_language == "en"
               else "Henüz tam bir tur yok. Önce yaz, bot cevap versin, sonra /save.")
        print(msg)
        return

    user_turn = entries[last_user_idx]["content"]
    assistant_turn = entries[last_assistant_idx]["content"]

    print()
    if report_language == "tr":
        print("Anchor kaydet — son tur:")
        print(f"  user:      {_truncate(user_turn, 80)}")
        print(f"  assistant: {_truncate(assistant_turn, 80)}\n")
    else:
        print("Save anchor — last turn:")
        print(f"  user:      {_truncate(user_turn, 80)}")
        print(f"  assistant: {_truncate(assistant_turn, 80)}\n")

    expect_contains = _prompt_list(
        "Yanıtta hangi sözcükler bulunmalı? (virgülle ayır, boş için Enter): "
        if report_language == "tr"
        else "What words should be in the reply? (comma-separated, Enter for none): "
    )
    expect_not_contains = _prompt_list(
        "Yanıtta hangi sözcükler bulunmamalı? (virgülle ayır, boş için Enter): "
        if report_language == "tr"
        else "What words should NOT be in the reply? (comma-separated, Enter for none): "
    )
    rubric = input(
        "Rubric (opsiyonel, Enter geç): "
        if report_language == "tr"
        else "Rubric (optional, Enter to skip): "
    ).strip()

    anchor = {"input": user_turn}
    if expect_contains:
        anchor["expect_contains"] = expect_contains
    if expect_not_contains:
        anchor["expect_not_contains"] = expect_not_contains
    if rubric:
        anchor["rubric"] = rubric

    confirm = input(
        "\nKaydedeyim mi? [E/h]: " if report_language == "tr" else "\nSave? [Y/n]: "
    ).strip().lower()
    if confirm and confirm not in ("e", "evet", "y", "yes"):
        print("İptal." if report_language == "tr" else "Cancelled.")
        return

    staged = _read_staged(staged_path)
    staged.append(anchor)
    _atomic_write_json(staged_path, staged)
    session["saved_anchors"] = len(staged)
    _atomic_write_json(session_path, session)

    msg = (f"Anchor #{len(staged)} kaydedildi (staging). Toplam: {len(staged)}. /commit ile sidecar'a yazılır."
           if report_language == "tr"
           else f"Anchor #{len(staged)} staged. Total: {len(staged)}. Use /commit to write.")
    print(msg)


def _handle_history(chat_jsonl_path: str, report_language: str,
                    persona_name: str | None) -> None:
    """v0.5.19: history dump uses the same one-liner style as live
    rendering — '  N.  speaker [hh:mm] arrow text' with the speaker
    label padded to align the longer of {persona_name, "sen"/"you"}.
    Content is truncated at 90 chars (no wrap) so each turn stays on a
    single row for scrollable review."""
    entries = _read_chat_jsonl(chat_jsonl_path)
    if not entries:
        print(_c(_COL_DIM, "(boş)" if report_language == "tr" else "(empty)"))
        return
    user_label = "sen" if report_language == "tr" else "you"
    bot_label = persona_name if persona_name else "Bot"
    label_w = max(len(user_label), len(bot_label))
    print()
    for i, e in enumerate(entries, 1):
        role = e.get("role", "?")
        text = _truncate(e.get("content", ""), 90)
        ts_raw = e.get("ts", "")
        try:
            hhmm = datetime.datetime.fromisoformat(ts_raw).strftime("%H:%M")
        except (ValueError, TypeError):
            hhmm = "--:--"
        if role == "assistant":
            speaker = _c(_COL_BOLD, _c(_COL_BOT, bot_label.ljust(label_w)))
            arrow = _c(_COL_DIM, "»")
        else:
            speaker = _c(_COL_BOLD, _c(_COL_USER, user_label.ljust(label_w)))
            arrow = _c(_COL_DIM, "«")
        num = _c(_COL_DIM, f"{i:3d}.")
        ts_col = _c(_COL_DIM, f"[{hhmm}]")
        print(f"{num} {speaker} {ts_col} {arrow} {text}")
    print()


def _start_fresh_call(ctx: "_SubprocessCtx", run_dir: str,
                      chat_jsonl_path: str, session_path: str, session: dict,
                      report_language: str,
                      pending_user_msg: str | None = None
                      ) -> tuple[str | None, bool]:
    """v0.5.19: shared "start a new call" pipeline used by both /reset and
    the post-call auto-restart path (user types a non-slash message after
    the persona ended the previous call).

    Steps: archive any existing chat.jsonl, mint fresh session_uuid /
    caller_name / session_started_at, tear down the claude subprocess and
    spawn a new one bound to the new session id, send the SYSTEM call-
    connected cue so the persona delivers its opening line, render that
    opening and the turn-1 footer.

    When `pending_user_msg` is provided, that text is sent as the first
    user turn immediately after the opening — this is how the post-call
    auto-restart preserves whatever the user typed instead of throwing
    their message away.

    Returns (archive_basename_or_None, persona_ended_again_bool).
    archive_basename is the filename of the discarded chat.jsonl backup
    so callers (e.g., /reset's banner) can quote it; None when there was
    nothing to archive. The boolean is True if the persona ended the new
    call too — either on the opening (rare refusal branch) or via the
    pending user turn — so callers can re-enter post-call mode.
    """
    archive_name: str | None = None
    if os.path.exists(chat_jsonl_path) and os.path.getsize(chat_jsonl_path) > 0:
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = os.path.join(run_dir, f"chat-{ts}-discarded.jsonl")
        shutil.move(chat_jsonl_path, archive)
        archive_name = os.path.basename(archive)
    open(chat_jsonl_path, "w").close()

    first = random.choice(_TR_FIRST_NAMES)
    last = random.choice(_TR_LAST_NAMES)
    session["turns"] = 0
    session["chat_session_uuid"] = str(uuid.uuid4())
    session["caller_name"] = f"{first} {last}"
    session["persona_name"] = None
    session["session_started_at"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    _atomic_write_json(session_path, session)

    ctx.close()
    ctx.session_uuid = session["chat_session_uuid"]
    ctx.start()

    print()
    try:
        opening_raw = _send_with_spinner(
            ctx,
            f"[SYSTEM: call connected — caller is {session['caller_name']}]",
            None,
            report_language,
        )
    except RuntimeError as e:
        print(_c(_COL_ERR, f"\n[chat error during call start: {e}]\n"),
              file=sys.stderr)
        return archive_name, False

    opening, opening_ended = _strip_end_call_marker(opening_raw)
    opening, persona_name = _strip_persona_name_marker(opening)
    if persona_name:
        session["persona_name"] = persona_name
    opening = opening.strip()
    _append_chat_entry(chat_jsonl_path, "assistant", opening)
    session["turns"] = 1
    _atomic_write_json(session_path, session)
    _render_bot_reply(opening, session.get("persona_name"))
    _render_footer(session["turns"], report_language)

    if opening_ended:
        return archive_name, True

    if pending_user_msg:
        print()
        ended = _exchange_turn(ctx, pending_user_msg, chat_jsonl_path,
                               session_path, session, report_language)
        return archive_name, ended

    return archive_name, False


def _handle_reset(run_dir: str, chat_jsonl_path: str, session_path: str,
                  session: dict, report_language: str,
                  ctx: "_SubprocessCtx") -> None:
    if os.path.getsize(chat_jsonl_path) == 0 and session.get("turns", 0) == 0:
        print("Geçmiş zaten boş." if report_language == "tr" else "History already empty.")
        return
    archive_name, _ended = _start_fresh_call(
        ctx, run_dir, chat_jsonl_path, session_path, session, report_language,
    )
    if archive_name:
        msg = (f"Sıfırlandı. Geçmiş arşivlendi: {archive_name}. Yeni arayan: {session['caller_name']}."
               if report_language == "tr"
               else f"Reset. History archived to {archive_name}. New caller: {session['caller_name']}.")
    else:
        msg = (f"Sıfırlandı. Yeni arayan: {session['caller_name']}."
               if report_language == "tr"
               else f"Reset. New caller: {session['caller_name']}.")
    print(_c(_COL_SYS, msg))


def _handle_commit(abs_prompt: str, staged_path: str, session_path: str,
                   session: dict, report_language: str) -> None:
    try:
        import yaml
    except ImportError:
        print("PyYAML yok — pip install pyyaml" if report_language == "tr"
              else "PyYAML missing — pip install pyyaml")
        return

    staged = _read_staged(staged_path)
    if not staged:
        print("Staged anchor yok. Önce /save kullan." if report_language == "tr"
              else "No staged anchors. Use /save first.")
        return

    sidecar_path = abs_prompt + ".anchors.yaml"
    if os.path.exists(sidecar_path):
        try:
            sidecar = yaml.safe_load(open(sidecar_path, encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            print(f"error: sidecar unparseable, abort. {e}", file=sys.stderr)
            return
        if sidecar.get("schema_version") != 1:
            print(f"error: sidecar schema_version mismatch (got {sidecar.get('schema_version')!r}, want 1)",
                  file=sys.stderr)
            return
        existing = sidecar.get("anchors") or []
    else:
        sidecar = {"schema_version": 1, "anchors": []}
        existing = []

    sidecar["schema_version"] = 1
    sidecar["anchors"] = existing + staged

    new_text = yaml.safe_dump(sidecar, allow_unicode=True, sort_keys=False, default_flow_style=False)
    tmp = sidecar_path + ".chat-commit.tmp." + str(os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
    try:
        check = yaml.safe_load(open(tmp, encoding="utf-8")) or {}
        if check.get("schema_version") != 1:
            raise ValueError("schema_version drift")
        if len(check.get("anchors") or []) != len(existing) + len(staged):
            raise ValueError("anchor count mismatch")
    except Exception as e:
        os.remove(tmp)
        print(f"error: post-write validation failed — rollback. {e}", file=sys.stderr)
        return
    os.rename(tmp, sidecar_path)

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    committed = os.path.join(os.path.dirname(staged_path), f"committed-{ts}.json")
    shutil.move(staged_path, committed)
    _atomic_write_json(staged_path, [])
    session["saved_anchors"] = 0
    session.setdefault("commits", []).append({"ts": ts, "count": len(staged)})
    _atomic_write_json(session_path, session)

    sidecar_name = os.path.basename(sidecar_path)
    msg = (f"{len(staged)} anchor sidecar'a yazıldı ({sidecar_name}). /prompt-test ile çalıştırabilirsin."
           if report_language == "tr"
           else f"{len(staged)} anchors written to sidecar ({sidecar_name}). Use /prompt-test to run.")
    print(msg)


def _exit_cleanly(run_dir: str, session_path: str, chat_jsonl_path: str,
                  staged_path: str, abs_prompt: str, report_language: str,
                  reason: str, session: dict) -> None:
    staged_count = len(_read_staged(staged_path))

    if reason == "user_quit" and staged_count > 0:
        prompt = (f"\n{staged_count} staged anchor var. Sidecar'a yazayım mı? [E/h/iptal]: "
                  if report_language == "tr"
                  else f"\n{staged_count} staged anchors uncommitted. Commit? [Y/n/cancel]: ")
        choice = input(prompt).strip().lower()
        if choice in ("e", "evet", "y", "yes", ""):
            _handle_commit(abs_prompt, staged_path, session_path, session, report_language)
        elif choice in ("iptal", "cancel"):
            print("İptal — staged anchors bekliyor." if report_language == "tr"
                  else "Cancelled — staged anchors retained.")

    turns = session.get("turns", 0)
    total_committed = sum(c.get("count", 0) for c in session.get("commits", []))

    # v0.5.26: leave the alternate screen BEFORE rendering the summary
    # card so the card lands in the main terminal's scrollback. If we
    # rendered it inside the alt screen, _leave_alt_screen would wipe
    # the whole canvas — including the summary — as soon as main()'s
    # finally block ran. _release_pin first so the print() calls
    # below aren't constrained to the scroll region.
    _release_pin()
    _leave_alt_screen()

    _render_summary_card(run_dir, report_language, turns, total_committed,
                         session.get("session_started_at"))


# ----------------------------------------------------------------- helpers


def _render_message(speaker: str, ts: str, text: str, is_user: bool) -> None:
    """v0.5.19: minimal one-liner per-turn render — '{speaker} [{ts}]
    {arrow} {text}'. Speaker is bold-colored (green for bot, orange for
    user), timestamp + arrow are dim, body uses the terminal's default
    colour so the eye lands on content rather than chrome.

    Long bodies are word-wrapped to the terminal width with continuation
    lines indented by the prefix width — keeps the message visually
    grouped under its speaker label even when it spans multiple rows.
    Blank lines inside the body are preserved as paragraph breaks.
    """
    if is_user:
        col_speaker = _COL_USER
        arrow = "«"
    else:
        col_speaker = _COL_BOT
        arrow = "»"

    prefix_plain = f"{speaker} [{ts}] {arrow} "
    prefix_colored = (
        _c(_COL_BOLD, _c(col_speaker, speaker)) + " "
        + _c(_COL_DIM, f"[{ts}]") + " "
        + _c(_COL_DIM, arrow) + " "
    )
    prefix_w = _visible_len(prefix_plain)
    indent = " " * prefix_w

    cols = _term_size()[0]
    available = max(20, cols - prefix_w - 1)
    paragraphs = (text or "").split("\n")

    with _STDOUT_LOCK:
        emitted_any = False
        first_line = True
        for para in paragraphs:
            if not para.strip():
                if emitted_any:
                    print()
                continue
            wrapped = textwrap.wrap(para, width=available) or [para]
            for line in wrapped:
                if first_line:
                    print(prefix_colored + line)
                    first_line = False
                else:
                    print(indent + line)
                emitted_any = True


def _render_bot_reply(text: str, persona_name: str | None) -> None:
    """v0.5.19: delegate to the shared one-liner renderer. Persona name
    defaults to 'Bot' when the opening reply hasn't supplied
    [PERSONA_NAME] yet (rare — only happens if the model omits the
    metadata line on its first turn)."""
    speaker = persona_name if persona_name else "Bot"
    ts = datetime.datetime.now().strftime("%H:%M")
    _render_message(speaker, ts, text, is_user=False)


# v0.5.13: terminal width / height probe + pinned bottom footer.
# v0.5.26: pinned footer now lives inside an alternate screen buffer
# (`\033[?1049h`) so the main terminal scrollback is untouched.
# DECSTBM scroll region starts at `_PIN_TOP_ROW` (right below the
# welcome banner) and ends at `rows - 1`, leaving row `rows` reserved
# for the sticky footer.
_PIN_ACTIVE = False
_PIN_TOP_ROW = 1
_ALT_SCREEN_ACTIVE = False


def _term_size() -> tuple[int, int]:
    cols, rows = shutil.get_terminal_size((80, 24))
    return cols, rows


def _enter_alt_screen() -> None:
    """v0.5.27: alt-screen experiment shelved.

    v0.5.26 wrapped the chat in `\\033[?1049h` so the banner could sit
    on row 1 of a clean canvas and DECSTBM could pin a footer to the
    bottom row. In Terminal.app the combination produced unpredictable
    rendering — banner halves got overwritten by the spinner's inline
    `\\r\\033[2K` paint, the bot's opening reply collided with the
    input arrow, and the user saw a visual mess.

    Sticky footer is back to a "logical" approach in v0.5.27:
    _input_prompt prints the footer line right above the arrow, and
    _rerender_user_oneliner clears both lines on Enter. The result is
    a footer that's visible while the user is typing — the only moment
    it matters — and gone from the scrollback between turns. No alt
    screen, no scroll region, no terminal-mode trickery.
    """
    return


def _leave_alt_screen() -> None:
    """v0.5.27: paired with _enter_alt_screen no-op — nothing to leave."""
    return


def _init_pin(top_row: int = 1) -> None:
    """v0.5.27: pinned-footer attempt deferred; see _enter_alt_screen
    docstring for the rationale. Footer is now drawn by _input_prompt
    and torn down by _rerender_user_oneliner."""
    return


def _release_pin() -> None:
    """v0.5.27: paired no-op for _init_pin."""
    return


def _compute_footer_line(turn: int, report_language: str,
                         post_call: bool = False) -> str:
    """v0.5.27: helper that returns the footer text for the current
    turn / post-call state without printing anything. _input_prompt
    calls this and emits the line right above the arrow so the footer
    visually attaches to the input area.

    v0.5.28: post-call state returns an empty string — the end-call
    box already explains the available commands, repeating them in
    the footer just adds noise. _input_prompt's `if footer and _TTY`
    guard skips the print when the line is empty."""
    if post_call:
        return ""
    cmds = "/save · /history · /commit · /quit · /help"
    return (f"― [tur {turn} · {cmds}]" if report_language == "tr"
            else f"― [turn {turn} · {cmds}]")


def _render_footer(turn: int, report_language: str,
                   post_call: bool = False) -> None:
    """v0.5.27: deprecated — footer is drawn by _input_prompt now,
    immediately above the user's arrow prompt, so it stays visually
    attached to the input area and disappears from the scrollback once
    the user submits a turn. This stub stays as a no-op for backwards
    compatibility with the existing call sites (main(), _repl,
    _exchange_turn, _start_fresh_call, _handle_silence) — none of
    them need to be touched until the next cleanup pass."""
    return


def _input_prompt(idle_timeout: float | None = None,
                  report_language: str = "tr",
                  footer: str = "") -> tuple[str | None, bool]:
    """Render the user input prompt in orange and read a line.

    Returns (text, timed_out):
      - (text, False) when the user submits a line (text may be empty).
      - (None,  True) when no Enter arrived within `idle_timeout`
        seconds (idle-silence trigger — Unix only).

    v0.5.14: optional idle timeout that fires the auto-silence path.
    v0.5.19: after input() returns we redraw the echo using the same
    one-liner format as the bot ('sen [hh:mm] « text') instead of
    pushing it to the right edge — matches the per-turn render style
    introduced in v0.5.19.
    v0.5.27: optional `footer` string is printed on its own line right
    above the arrow so it visually attaches to the input area. Both
    the echo and footer lines are cleared on Enter / timeout so they
    don't accumulate in the scrollback between turns.
    """
    if footer and _TTY:
        with _STDOUT_LOCK:
            sys.stdout.write(_c(_COL_DIM, footer))
            sys.stdout.write("\n")
            sys.stdout.flush()
    arrow = _c(_COL_USER, _c(_COL_BOLD, "❯ "))
    text, timed_out = _read_with_idle_timeout(arrow, idle_timeout)
    if timed_out:
        # Clear the empty echoed prompt line AND the footer above it so
        # the silence banner that follows renders on a fresh row.
        if _TTY:
            with _STDOUT_LOCK:
                sys.stdout.write("\r\033[2K")
                if footer:
                    sys.stdout.write("\033[1A\r\033[2K")
                sys.stdout.flush()
        return None, True
    if text and text.strip() and _TTY:
        _rerender_user_oneliner(text, report_language, has_footer=bool(footer))
    return text, False


def _read_with_idle_timeout(arrow_prompt: str,
                            idle_timeout: float | None) -> tuple[str | None, bool]:
    """Cross-platform line reader with optional idle timeout.

    When idle_timeout is None / 0 / negative — or when running on Windows
    / a non-TTY stream — falls back to blocking input() in cooked mode
    (preserving full readline support). On Unix TTYs with a positive
    timeout, switches to cbreak mode via _read_raw_with_idle_timeout so
    the silence deadline resets on every keystroke (v0.5.16 — fixes the
    v0.5.14 race where typing a long reply that hadn't reached Enter in
    time was tcflushed as silence).
    """
    if (not idle_timeout) or idle_timeout <= 0 or (not _IDLE_TIMEOUT_OK) or (
            not sys.stdin.isatty()):
        return input(arrow_prompt), False
    try:
        return _read_raw_with_idle_timeout(arrow_prompt, float(idle_timeout))
    except (OSError, ValueError):
        # Raw-mode setup failed on this stream (unusual fd, no termios
        # support, etc.). Degrade to cooked input() so the user can still
        # type — they just lose the silence timer for this turn.
        return input(), False


def _read_raw_with_idle_timeout(arrow_prompt: str,
                                idle_timeout: float) -> tuple[str | None, bool]:
    """Read a line in cbreak mode, resetting the silence deadline on each
    keystroke.

    Minimal line editor — readline history and cursor navigation are
    intentionally gone (the welcome banner no longer advertises arrow-key
    history). Handles:
      • UTF-8 multi-byte sequences via an incremental decoder so a code
        point split across two os.read() chunks is never echoed half-way,
      • Backspace (0x7f / 0x08) — pops one code point and emits ``\\b \\b``,
      • Enter (CR or LF) — commits and returns the buffer,
      • Ctrl-D on empty buffer → EOFError (cbreak keeps ISIG on, so
        Ctrl-C still arrives as SIGINT → KeyboardInterrupt above us),
      • CSI / SS3 escape sequences (arrow keys, function keys) — swallowed
        silently across chunk boundaries via a small state machine,
      • Other control bytes < 0x20 (Tab, etc.) — dropped to keep the line
        rendering clean.

    The deadline resets on every chunk received; only a select() that
    elapses to zero with no further bytes triggers the silence return.
    Typed chars echo in the terminal's default color — _rerender_user_right
    repaints them in orange after Enter, matching the v0.5.13 cooked-mode
    look.
    """
    import codecs
    fd = sys.stdin.fileno()
    old_attrs = _termios_mod.tcgetattr(fd)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf: list[str] = []
    in_escape = False
    escape_first: int | None = None

    with _STDOUT_LOCK:
        sys.stdout.write(arrow_prompt)
        sys.stdout.flush()

    try:
        _tty_mod.setcbreak(fd)
        deadline = _now_monotonic() + idle_timeout
        while True:
            remaining = deadline - _now_monotonic()
            if remaining <= 0:
                return None, True
            ready, _, _ = _select_mod.select([fd], [], [], remaining)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 256)
            except OSError:
                continue
            if not chunk:
                raise EOFError
            deadline = _now_monotonic() + idle_timeout

            for b in chunk:
                if in_escape:
                    if escape_first is None:
                        escape_first = b
                        # ESC <char> alt-key shortcut terminates immediately
                        # unless it introduces a CSI ('[') or SS3 ('O').
                        if b not in (0x5b, 0x4f):
                            in_escape = False
                            escape_first = None
                    elif 0x40 <= b <= 0x7e:
                        # Final byte of CSI / SS3 sequence.
                        in_escape = False
                        escape_first = None
                    continue
                if b == 0x03:  # Ctrl-C — defensive: ISIG normally fires SIGINT
                    raise KeyboardInterrupt
                if b == 0x04:  # Ctrl-D
                    if not buf:
                        raise EOFError
                    continue
                if b in (0x0a, 0x0d):
                    with _STDOUT_LOCK:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                    return "".join(buf), False
                if b in (0x7f, 0x08):
                    if buf:
                        buf.pop()
                        with _STDOUT_LOCK:
                            sys.stdout.write("\b \b")
                            sys.stdout.flush()
                    continue
                if b == 0x1b:
                    in_escape = True
                    escape_first = None
                    continue
                if b < 0x20:
                    continue
                # Feed bytes one at a time so a multi-byte code point split
                # across os.read() boundaries never echoes a half character.
                ch = decoder.decode(bytes([b]))
                if ch:
                    buf.append(ch)
                    with _STDOUT_LOCK:
                        sys.stdout.write(ch)
                        sys.stdout.flush()
    finally:
        _termios_mod.tcsetattr(fd, _termios_mod.TCSADRAIN, old_attrs)


def _exchange_turn(ctx: "_SubprocessCtx", user_msg: str,
                   chat_jsonl_path: str, session_path: str,
                   session: dict, report_language: str) -> bool:
    """Send one user message, render the bot reply, return True if call ended.

    Centralises the append-user → ctx.send → strip-markers → append-
    assistant → render flow that's shared by the normal REPL turn, the
    /silence command, and the v0.5.14 auto-silence path. The caller is
    responsible for any pre-render output (banners, blank lines) and
    end-of-call handling (banner + exit) — this helper only returns the
    "did the persona end the call" flag.
    """
    _append_chat_entry(chat_jsonl_path, "user", user_msg)
    try:
        reply_raw = _send_with_spinner(
            ctx, user_msg, session.get("persona_name"), report_language,
        )
    except RuntimeError as e:
        print(_c(_COL_ERR, f"\n[chat error: {e}]\n"), file=sys.stderr)
        return False
    reply, ended = _strip_end_call_marker(reply_raw)
    reply, maybe_name = _strip_persona_name_marker(reply)
    if maybe_name and not session.get("persona_name"):
        session["persona_name"] = maybe_name
    reply = reply.strip()
    _append_chat_entry(chat_jsonl_path, "assistant", reply)
    session["turns"] = session.get("turns", 0) + 1
    _atomic_write_json(session_path, session)
    _render_bot_reply(reply, session.get("persona_name"))
    _render_footer(session["turns"], report_language)
    return ended


def _rerender_user_oneliner(text: str, report_language: str,
                            has_footer: bool = False) -> None:
    """v0.5.19: replace the cooked-mode echo with the one-liner user
    render — 'sen [hh:mm] « text' (or 'you [hh:mm] « text' in EN mode).

    input() leaves the cursor at the start of the next line after Enter.
    We move up one row, clear the echoed prompt line, and let
    _render_message redraw the message in its canonical form. Best-
    effort for single-line input: pastes that wrapped during cooked echo
    may leave residue on the wrapped rows — acceptable for the common
    case of short replies.

    v0.5.27: when `has_footer` is True, also climb one more row and
    clear the footer line that _input_prompt printed above the arrow.
    """
    ts = datetime.datetime.now().strftime("%H:%M")
    speaker = "sen" if report_language == "tr" else "you"
    with _STDOUT_LOCK:
        sys.stdout.write("\033[1A\r\033[2K")          # clear echoed prompt line
        if has_footer:
            sys.stdout.write("\033[1A\r\033[2K")      # clear footer line above
        sys.stdout.flush()
    _render_message(speaker, ts, text, is_user=True)


# v0.5.13: animated "..." spinner shown on its own line while the bare
# Claude subprocess is producing a reply. Background daemon thread cycles
# states every ~350ms; the line is cleared atomically when stop() runs.
_STDOUT_LOCK = threading.Lock()


class _ThinkingSpinner:
    def __init__(self, persona_name: str | None, report_language: str):
        suffix = "düşünüyor" if report_language == "tr" else "is thinking"
        speaker = persona_name or ("Bot" if report_language == "tr" else "Bot")
        self.label = f"{speaker} {suffix}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not _TTY:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        # Three frames give a clear "loop" feel: ".  ", ".. ", "..."
        # v0.5.18: reverted the v0.5.17 footer-painting experiment — the
        # save/restore-cursor sequence (\033[s ... \033[u) interacted badly
        # with the active DECSTBM scroll region in Terminal.app, leaving
        # cursor state inconsistent and bot replies invisible. Back to the
        # v0.5.16 inline paint; _drain_stale_input still cleans up cooked-
        # mode echo and kernel-buffered chars after the spinner stops.
        frames = [".  ", ".. ", "..."]
        i = 0
        while not self._stop.is_set():
            with _STDOUT_LOCK:
                sys.stdout.write(
                    "\r\033[2K" + _c(_COL_SPIN, f"{self.label}{frames[i % 3]}")
                )
                sys.stdout.flush()
            i += 1
            self._stop.wait(0.35)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=1.0)
        with _STDOUT_LOCK:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()


def _drain_stale_input() -> None:
    """v0.5.17: discard chars the user typed during bot thinking.

    Between bot turns the terminal is in cooked mode (cbreak is only
    engaged inside _read_raw_with_idle_timeout). Anything typed while
    ctx.send() blocks goes into the kernel line discipline buffer and
    echoes inline. Without draining, those chars would (a) re-surface as
    ghost input on the row where the next bot reply is about to render,
    and (b) the kernel buffer would feed them straight into the next
    cbreak select() — making it look like the user typed them
    *after* the reply.
    """
    if not _IDLE_TIMEOUT_OK or not sys.stdin.isatty():
        return
    try:
        _termios_mod.tcflush(sys.stdin.fileno(), _termios_mod.TCIFLUSH)
    except (OSError, _termios_mod.error):
        pass
    if _TTY:
        with _STDOUT_LOCK:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()


def _send_with_spinner(ctx: "_SubprocessCtx", msg: str,
                       persona_name: str | None, report_language: str) -> str:
    """Wrap a ctx.send() call with the thinking spinner so the user sees
    progress feedback while the bare Claude subprocess is producing the
    reply. Spinner stops in finally so an exception still cleans up.
    v0.5.17: also drains stale cooked-mode input after spinner.stop so
    the next bot reply doesn't collide with ghost chars the user typed
    during the wait."""
    spinner = _ThinkingSpinner(persona_name, report_language)
    spinner.start()
    try:
        return ctx.send(msg, stream_to=None)
    finally:
        spinner.stop()
        _drain_stale_input()


def _print_welcome(run_dir: str, abs_prompt: str, chat_model: str,
                   report_language: str, caller_name: str,
                   session: dict) -> int:
    """v0.5.19: replaced the legacy three-bar layout with a single boxed
    frame so the welcome reads as one card. Same information density —
    prompt name + line count, caller, isolation mode, idle-silence hint,
    command crib — but visually cohesive with the end-call banner and
    final summary card that share the same _render_box helper.

    v0.5.25: idle-silence hint simplified back to on/off. The prompt's
    own silence policy (1st/2nd/3rd silence stages with their own
    thresholds) is exercised via manual `/silence N` calls, not via a
    single auto-fire threshold we'd have to pick for the user.

    v0.5.26: returns the number of rows the banner occupies so main()
    can size the DECSTBM scroll region to start RIGHT BELOW the
    banner — banner stays pinned, conversation scrolls underneath."""
    basename = os.path.basename(abs_prompt).rsplit(".", 1)[0]
    line_count = sum(1 for _ in open(os.path.join(run_dir, "body.txt"), encoding="utf-8"))
    idle_secs = int(session.get("idle_silence_secs", 0) or 0)
    if not _IDLE_TIMEOUT_OK:
        idle_hint_tr = "Otomatik sessizlik: bu platformda kapalı (manuel /silence <N>)."
        idle_hint_en = "Auto-silence: disabled on this platform (use manual /silence <N>)."
    elif idle_secs <= 0:
        idle_hint_tr = ("Otomatik sessizlik: kapalı · persona silence policy'sini "
                        "/silence <N> ile test et")
        idle_hint_en = ("Auto-silence: off · use /silence <N> to trigger the persona's "
                        "own silence policy stages")
    else:
        idle_hint_tr = (f"Otomatik sessizlik: {idle_secs} sn (manuel ayar · "
                        f"kapatmak için /silence-auto off)")
        idle_hint_en = (f"Auto-silence: {idle_secs}s (manual override · "
                        f"disable with /silence-auto off)")

    title = (_c(_COL_BOLD, "/prompt-chat") +
             _c(_COL_DIM, " · interactive persona simulator · v0.5.28"))
    print()
    if report_language == "tr":
        lines = [
            f"{_c(_COL_DIM, 'Prompt   ')} {_c(_COL_BOLD, basename)} "
            f"{_c(_COL_DIM, f'({line_count} satır · model: {chat_model})')}",
            f"{_c(_COL_DIM, 'Arayan   ')} {_c(_COL_BOLD, caller_name)}",
            f"{_c(_COL_DIM, 'İzolasyon')} {_c(_COL_DIM, 'yeni pencere · bare Claude session')}",
            "",
            _c(_COL_DIM, "Bot birazdan kendisi selamlayacak — aramayı cevapladın."),
            _c(_COL_DIM, idle_hint_tr),
            "",
            _c(_COL_DIM, "Komutlar:") + " " + _c(_COL_USER,
                "/save · /history · /reset · /silence · /commit · /help · /quit"),
        ]
    else:
        lines = [
            f"{_c(_COL_DIM, 'Prompt   ')} {_c(_COL_BOLD, basename)} "
            f"{_c(_COL_DIM, f'({line_count} lines · model: {chat_model})')}",
            f"{_c(_COL_DIM, 'Caller   ')} {_c(_COL_BOLD, caller_name)}",
            f"{_c(_COL_DIM, 'Isolation')} {_c(_COL_DIM, 'new window · bare Claude session')}",
            "",
            _c(_COL_DIM, "The bot will greet you first — you're the caller answering."),
            _c(_COL_DIM, idle_hint_en),
            "",
            _c(_COL_DIM, "Commands:") + " " + _c(_COL_USER,
                "/save · /history · /reset · /silence · /commit · /help · /quit"),
        ]
    box_rows = _render_box(title, lines)
    # 1 leading blank line (the print() above) + box rows.
    return 1 + box_rows


def _render_end_call_box(report_language: str) -> None:
    """v0.5.19: rendered when the persona ends the call (END_CALL marker
    or closing-phrase regex hit). The REPL no longer auto-exits; this box
    explains what just happened and surfaces the post-call options so the
    user has time to /save the last turn or /commit staged anchors before
    closing the session manually."""
    title = _c(_COL_BOLD, _c(_COL_ENDCALL,
        "ARAMA SONA ERDİ" if report_language == "tr" else "CALL ENDED"))
    if report_language == "tr":
        lines = [
            _c(_COL_DIM, "Persona end-call-tool eşdeğeri ile aramayı kapattı."),
            "",
            _c(_COL_DIM, "Devam etmek için:"),
            f"  {_c(_COL_USER, '/save')}    {_c(_COL_DIM, 'son turu anchor olarak kaydet')}",
            f"  {_c(_COL_USER, '/history')} {_c(_COL_DIM, 'konuşmayı gözden geçir')}",
            f"  {_c(_COL_USER, '/commit')}  {_c(_COL_DIM, 'staged anchorları sidecara yaz')}",
            f"  {_c(_COL_USER, '/quit')}    {_c(_COL_DIM, 'oturumu kapat (final özet)')}",
            "",
            _c(_COL_DIM, "Yeni bir mesaj yazarsan yeni bir arama başlatılır."),
        ]
    else:
        lines = [
            _c(_COL_DIM, "Persona ended the call (end-call-tool equivalent)."),
            "",
            _c(_COL_DIM, "Next steps:"),
            f"  {_c(_COL_USER, '/save')}    {_c(_COL_DIM, 'capture last turn as an anchor')}",
            f"  {_c(_COL_USER, '/history')} {_c(_COL_DIM, 'review the conversation')}",
            f"  {_c(_COL_USER, '/commit')}  {_c(_COL_DIM, 'write staged anchors to sidecar')}",
            f"  {_c(_COL_USER, '/quit')}    {_c(_COL_DIM, 'close the session (final summary)')}",
            "",
            _c(_COL_DIM, "Typing a new message starts a fresh call."),
        ]
    print()
    _render_box(title, lines)


def _format_duration(started_at: str | None) -> str:
    """Format wall-clock session duration as '4m 39s' / '12s'. Used by the
    summary card. Falls back to '—' when the start timestamp is missing
    or unparseable (e.g., legacy sessions before session_started_at was
    persisted in v0.5.19)."""
    if not started_at:
        return "—"
    try:
        start_dt = datetime.datetime.fromisoformat(started_at)
        now = datetime.datetime.now(datetime.timezone.utc)
        secs = int((now - start_dt).total_seconds())
    except (ValueError, TypeError):
        return "—"
    if secs < 0:
        return "—"
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60}s"


def _render_summary_card(run_dir: str, report_language: str, turns: int,
                         total_committed: int, started_at: str | None) -> None:
    """v0.5.19: replaces the four plain print lines at the end of
    _exit_cleanly with a single bordered card that visually parallels the
    welcome banner. Same fields plus an added wall-clock duration."""
    run_name = os.path.basename(run_dir)
    duration = _format_duration(started_at)
    title = _c(_COL_BOLD, _c(_COL_OK,
        "/prompt-chat oturum özeti" if report_language == "tr"
        else "/prompt-chat session summary"))
    if report_language == "tr":
        rows = [
            ("Run",            run_name),
            ("Konuşma turu",   str(turns)),
            ("Kayıtlı anchor", str(total_committed)),
            ("Süre",           duration),
            ("Run dir",        os.path.relpath(run_dir)),
        ]
    else:
        rows = [
            ("Run",      run_name),
            ("Turns",    str(turns)),
            ("Anchors",  str(total_committed)),
            ("Duration", duration),
            ("Run dir",  os.path.relpath(run_dir)),
        ]
    label_w = max(len(k) for k, _ in rows)
    lines = [
        f"{_c(_COL_DIM, k.ljust(label_w))}  {_c(_COL_BOLD, v)}"
        for k, v in rows
    ]
    print()
    _render_box(title, lines)
    print()


def _read_chat_jsonl(path: str) -> list:
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _read_staged(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or []
    except (json.JSONDecodeError, OSError):
        return []


def _append_chat_entry(path: str, role: str, content: str) -> None:
    entry = {"role": role, "content": content,
             "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _atomic_write_json(path: str, data) -> None:
    tmp = path + ".tmp." + str(os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.rename(tmp, path)


def _prompt_list(prompt: str) -> list:
    raw = input(prompt).strip()
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _truncate(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return (s[:n] + "…") if len(s) > n else s


if __name__ == "__main__":
    sys.exit(main())
