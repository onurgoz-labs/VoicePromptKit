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
import datetime
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import uuid
from queue import Empty, Queue

# v0.5.11: readline is stdlib on macOS/Linux but not bundled on Windows. Import
# defensively — when unavailable, input() still works, just without arrow-key
# history or line editing.
try:
    import readline  # noqa: F401 — side-effect import: enables history + line edit on input()
    _READLINE_AVAILABLE = True
except ImportError:
    _READLINE_AVAILABLE = False

# v0.5.11: ANSI escape sequences for color. Stdlib-only (no rich/colorama).
# We only emit these when stdout is a real TTY — piping to a file would
# pollute it with raw escape codes otherwise.
_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    """Wrap text in ANSI color codes when stdout is a TTY; otherwise no-op."""
    if not _TTY:
        return text
    return f"\033[{code}m{text}\033[0m"


_COL_BOT       = "38;5;208"  # orange — persona reply prefix + footer
_COL_USER      = "38;5;42"   # green  — user input prompt
_COL_SYS       = "38;5;245"  # gray   — system / meta messages
_COL_DIM       = "2"         # dim    — secondary text (timestamps, hints)
_COL_BOLD      = "1"         # bold   — emphasis
_COL_ERR       = "38;5;196"  # red    — errors


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

8. **Mid-call interruptions, silences, off-topic comments from the caller are EXPECTED.** Handle them per the script's interruption / silence / off-scope rules. Do not break character to comment on the disruption. When you receive a user message that matches `[silence for N seconds]` (case-insensitive), treat it as the caller saying NOTHING for N seconds — apply your script's silence policy (gentle confirmation prompt, escalation after K silences, etc.), do not respond as if "silence for 6 seconds" were spoken text.

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


def _write_wrapped_body(run_dir: str, body_path: str) -> str:
    """Write a persona-framed copy of body.txt that claude will receive as the
    system prompt. The original body.txt is never modified. Wrapped file
    lives in the run dir as .body-wrapped.txt; regenerated on every script
    start so framing edits in this file propagate automatically.
    """
    body = open(body_path, encoding="utf-8").read()
    wrapped = _ROLEPLAY_FRAMING + body
    wrapped_path = os.path.join(run_dir, ".body-wrapped.txt")
    tmp = wrapped_path + ".tmp." + str(os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(wrapped)
    os.rename(tmp, wrapped_path)
    return wrapped_path


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

    _print_welcome(run_dir, abs_prompt, chat_model, report_language, caller_name)

    # v0.5.8: wrap body.txt with persona roleplay framing before passing to
    # claude. The original body.txt is left untouched; .body-wrapped.txt is
    # the framed version the subprocess actually reads as its system prompt.
    wrapped_body_path = _write_wrapped_body(run_dir, body_path)

    # Spawn the long-lived claude subprocess ONCE.
    ctx = _SubprocessCtx(wrapped_body_path, chat_session_uuid, chat_model)
    ctx.start()

    # v0.5.9: bot greets FIRST. After welcome, send a [SYSTEM: call connected]
    # trigger so the persona delivers its opening line before we hand the
    # terminal to the user — matching production Vapi behaviour where the
    # bot speaks first when the call connects.
    if session.get("turns", 0) == 0:
        try:
            print()  # blank line before the opening
            # We can't stream the opening directly to stdout because the bot
            # is required to emit [PERSONA_NAME: X] as its first line; that
            # bracketed marker would flash on screen if we passed
            # stream_to=sys.stdout. Buffer the opening, strip the marker,
            # THEN print the clean reply with the persona prefix.
            opening_raw = ctx.send(
                f"[SYSTEM: call connected — caller is {caller_name}]",
                stream_to=None,  # buffer-only; we re-render after stripping markers
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
            _render_footer(session["turns"], report_language)
            if opening_ended:
                # Persona ended the call on the opening (rare — refused to take
                # the call, e.g. the script's "wrong number / decline" branch).
                _exit_cleanly(run_dir, session_path, chat_jsonl_path, staged_path,
                              abs_prompt, report_language, "end_call_marker", session)
                return 0
        except RuntimeError as e:
            print(_c(_COL_ERR, f"\n[chat error during opening: {e}]\n"), file=sys.stderr)

    try:
        return _repl(ctx, run_dir, body_path, session_path, chat_jsonl_path,
                    staged_path, abs_prompt, report_language, session)
    finally:
        ctx.close()


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
          report_language: str, session: dict) -> int:
    while True:
        try:
            user_input = _input_prompt().strip()
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

        if not user_input:
            continue

        if user_input.startswith("/"):
            should_quit = _dispatch_slash(user_input, run_dir, session_path,
                                          chat_jsonl_path, staged_path,
                                          abs_prompt, report_language, session, ctx)
            if should_quit:
                return 0
            continue

        # Normal turn — append user, buffer reply (need to strip markers before
        # rendering), then render with persona prefix.
        _append_chat_entry(chat_jsonl_path, "user", user_input)
        print()  # blank line before the reply
        try:
            reply_raw = ctx.send(user_input, stream_to=None)
        except RuntimeError as e:
            print(_c(_COL_ERR, f"\n[chat error: {e}]\n"), file=sys.stderr)
            continue

        reply, ended = _strip_end_call_marker(reply_raw)
        reply, _maybe_name = _strip_persona_name_marker(reply)
        if _maybe_name and not session.get("persona_name"):
            session["persona_name"] = _maybe_name
        reply = reply.strip()
        _append_chat_entry(chat_jsonl_path, "assistant", reply)
        session["turns"] = session.get("turns", 0) + 1
        _atomic_write_json(session_path, session)

        _render_bot_reply(reply, session.get("persona_name"))
        _render_footer(session["turns"], report_language)

        if ended:
            # v0.5.9: persona signalled end-of-call via <<END_CALL>>. Treat
            # as auto-quit — print a short banner so the user knows why we're
            # closing, then run the normal exit path (with the staged-anchor
            # commit prompt if any).
            banner = ("Persona aramayı kapattı (end-call-tool eşdeğeri)."
                      if report_language == "tr"
                      else "Persona ended the call (end-call-tool equivalent).")
            print(_c(_COL_SYS, f"[{banner}]"))
            print()
            _exit_cleanly(run_dir, session_path, chat_jsonl_path, staged_path,
                          abs_prompt, report_language, "end_call_marker", session)
            return 0


# ---------------------------------------------------------- slash commands


def _dispatch_slash(user_input: str, run_dir: str, session_path: str,
                    chat_jsonl_path: str, staged_path: str, abs_prompt: str,
                    report_language: str, session: dict,
                    ctx: "_SubprocessCtx") -> bool:
    parts = user_input.lstrip("/").strip().split(None, 1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd == "save":
        _handle_save(chat_jsonl_path, staged_path, report_language, session_path, session)
    elif cmd == "history":
        _handle_history(chat_jsonl_path, report_language, session.get("persona_name"))
    elif cmd == "reset":
        _handle_reset(run_dir, chat_jsonl_path, session_path, session, report_language, ctx)
    elif cmd == "silence":
        _handle_silence(arg, ctx, chat_jsonl_path, session_path, session, report_language)
    elif cmd == "commit":
        _handle_commit(abs_prompt, staged_path, session_path, session, report_language)
    elif cmd == "help":
        _handle_help(report_language)
    elif cmd == "quit":
        _exit_cleanly(run_dir, session_path, chat_jsonl_path, staged_path,
                      abs_prompt, report_language, "user_quit", session)
        return True
    else:
        msg = (f"Unknown command: /{cmd}. Try /help."
               if report_language == "en"
               else f"Bilinmeyen komut: /{cmd}. /help yaz.")
        print(_c(_COL_ERR, msg))
    return False


def _handle_help(report_language: str) -> None:
    """v0.5.11: dump the command crib at any time during the chat."""
    if report_language == "tr":
        print()
        print(_c(_COL_BOLD, "Komutlar:"))
        print(f"  {_c(_COL_USER, '/save')}        — son turu anchor olarak kaydet (staging)")
        print(f"  {_c(_COL_USER, '/history')}     — bu oturumdaki turları göster")
        print(f"  {_c(_COL_USER, '/reset')}       — geçmişi sil, baştan başla (fresh persona + fresh caller)")
        print(f"  {_c(_COL_USER, '/silence <N>')} — N saniye sessizlik simüle et (örn. /silence 6)")
        print(f"  {_c(_COL_USER, '/commit')}      — staged anchor'ları sidecar dosyaya yaz")
        print(f"  {_c(_COL_USER, '/help')}        — bu listeyi göster")
        print(f"  {_c(_COL_USER, '/quit')}        — çıkış + final summary")
        print()
        print(_c(_COL_DIM, "İpucu: ok tuşları ile geçmiş mesajlar arasında dolaşabilirsin (readline)."))
        print()
    else:
        print()
        print(_c(_COL_BOLD, "Commands:"))
        print(f"  {_c(_COL_USER, '/save')}        — capture last turn as a test anchor (staging)")
        print(f"  {_c(_COL_USER, '/history')}     — show this session's turns")
        print(f"  {_c(_COL_USER, '/reset')}       — discard history, fresh persona + caller")
        print(f"  {_c(_COL_USER, '/silence <N>')} — simulate N seconds of caller silence")
        print(f"  {_c(_COL_USER, '/commit')}      — write staged anchors to the sidecar file")
        print(f"  {_c(_COL_USER, '/help')}        — show this list")
        print(f"  {_c(_COL_USER, '/quit')}        — exit with a final summary")
        print()
        print(_c(_COL_DIM, "Tip: use arrow keys to browse previous input (readline)."))
        print()


def _handle_silence(arg: str, ctx: "_SubprocessCtx", chat_jsonl_path: str,
                    session_path: str, session: dict, report_language: str) -> None:
    """v0.5.9: simulate N seconds of caller silence as a user turn.

    Sends the opaque-string convention `[silence for N seconds]` (the same
    pattern drift-runner uses when expanding silence_input sugar) to the
    bare subprocess. Framing rule 8 tells the persona how to react.
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
        return

    silence_msg = f"[silence for {duration} seconds]"
    _append_chat_entry(chat_jsonl_path, "user", silence_msg)
    print()  # blank line before reply
    # Show what we sent so the user has context.
    print(_c(_COL_SYS, f"  (sessizlik: {duration} saniye)" if report_language == "tr"
              else f"  (silence: {duration} seconds)"))
    print()
    try:
        reply_raw = ctx.send(silence_msg, stream_to=None)
    except RuntimeError as e:
        print(_c(_COL_ERR, f"\n[chat error: {e}]\n"), file=sys.stderr)
        return
    reply, ended = _strip_end_call_marker(reply_raw)
    reply, _maybe_name = _strip_persona_name_marker(reply)
    if _maybe_name and not session.get("persona_name"):
        session["persona_name"] = _maybe_name
    reply = reply.strip()
    _append_chat_entry(chat_jsonl_path, "assistant", reply)
    session["turns"] = session.get("turns", 0) + 1
    _atomic_write_json(session_path, session)
    _render_bot_reply(reply, session.get("persona_name"))
    _render_footer(session["turns"], report_language)
    if ended:
        # Silence policy escalated to hangup — persona dropped the call.
        # Note: _handle_silence is called from _dispatch_slash which doesn't
        # propagate an exit code; setting a flag on session signals the REPL
        # to exit on the next iteration. Simpler: just raise SystemExit here.
        banner = ("Persona aramayı kapattı (sessizlik politikası — end-call eşdeğeri)."
                  if report_language == "tr"
                  else "Persona ended the call (silence policy → end-call equivalent).")
        print(f"\n[{banner}]\n")
        raise SystemExit(0)


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
    entries = _read_chat_jsonl(chat_jsonl_path)
    if not entries:
        print(_c(_COL_DIM, "(boş)" if report_language == "tr" else "(empty)"))
        return
    print()
    for i, e in enumerate(entries, 1):
        role = e.get('role', '?')
        text = _truncate(e.get('content', ''), 100)
        if role == "assistant":
            label = persona_name if persona_name else "bot"
            line = _c(_COL_BOT, f"{i:3d}. {label}: {text}")
        else:
            line = _c(_COL_USER, f"{i:3d}. you: {text}")
        print(line)
    print()


def _handle_reset(run_dir: str, chat_jsonl_path: str, session_path: str,
                  session: dict, report_language: str,
                  ctx: "_SubprocessCtx") -> None:
    if os.path.getsize(chat_jsonl_path) == 0 and session.get("turns", 0) == 0:
        print("Geçmiş zaten boş." if report_language == "tr" else "History already empty.")
        return

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = os.path.join(run_dir, f"chat-{ts}-discarded.jsonl")
    if os.path.exists(chat_jsonl_path) and os.path.getsize(chat_jsonl_path) > 0:
        shutil.move(chat_jsonl_path, archive)
    open(chat_jsonl_path, "w").close()
    session["turns"] = 0
    session["chat_session_uuid"] = str(uuid.uuid4())
    # v0.5.9: re-roll caller name on reset so the fresh persona meets a fresh caller.
    first = random.choice(_TR_FIRST_NAMES)
    last = random.choice(_TR_LAST_NAMES)
    session["caller_name"] = f"{first} {last}"
    _atomic_write_json(session_path, session)

    # /reset re-rolls the caller — also drop the persona_name; the bot
    # re-emits [PERSONA_NAME: X] on its fresh opening.
    session["persona_name"] = None
    _atomic_write_json(session_path, session)

    # Tear down old claude subprocess and spawn a fresh one with the new session id.
    ctx.close()
    ctx.session_uuid = session["chat_session_uuid"]
    ctx.start()

    # Send the call-connected trigger for the new caller; bot greets first.
    print()
    try:
        opening_raw = ctx.send(
            f"[SYSTEM: call connected — caller is {session['caller_name']}]",
            stream_to=None,
        )
        opening, _ = _strip_end_call_marker(opening_raw)
        opening, persona_name = _strip_persona_name_marker(opening)
        if persona_name:
            session["persona_name"] = persona_name
        opening = opening.strip()
        _append_chat_entry(chat_jsonl_path, "assistant", opening)
        session["turns"] = 1
        _atomic_write_json(session_path, session)
        _render_bot_reply(opening, session.get("persona_name"))
        _render_footer(session["turns"], report_language)
    except RuntimeError as e:
        print(_c(_COL_ERR, f"\n[chat error during reset opening: {e}]\n"), file=sys.stderr)

    msg = (f"Sıfırlandı. Geçmiş arşivlendi: {os.path.basename(archive)}. Yeni arayan: {session['caller_name']}."
           if report_language == "tr"
           else f"Reset. History archived to {os.path.basename(archive)}. New caller: {session['caller_name']}.")
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
    run_name = os.path.basename(run_dir)
    if report_language == "tr":
        print(f"\n/prompt-chat oturumu kapatıldı — {run_name}")
        print(f"- Konuşma turu: {turns}")
        print(f"- Commit edilen anchor toplamı: {total_committed}")
        print(f"- Run dir: {os.path.relpath(run_dir)}\n")
    else:
        print(f"\n/prompt-chat session closed — {run_name}")
        print(f"- Turns: {turns}")
        print(f"- Total committed anchors: {total_committed}")
        print(f"- Run dir: {os.path.relpath(run_dir)}\n")


# ----------------------------------------------------------------- helpers


def _render_bot_reply(text: str, persona_name: str | None) -> None:
    """Render the bot reply with a coloured persona-name prefix."""
    if persona_name:
        prefix = _c(_COL_BOT, _c(_COL_BOLD, f"❝ {persona_name}: "))
    else:
        prefix = _c(_COL_BOT, _c(_COL_BOLD, "❝ "))
    print(prefix + _c(_COL_BOT, text))


def _render_footer(turn: int, report_language: str) -> None:
    cmds = "/save | /history | /commit | /quit | /help"
    if report_language == "tr":
        line = f"― [tur {turn} · {cmds}]"
    else:
        line = f"― [turn {turn} · {cmds}]"
    print()
    print(_c(_COL_DIM, line))
    print()


def _input_prompt() -> str:
    """Render the user input prompt in green and read a line."""
    arrow = _c(_COL_USER, _c(_COL_BOLD, "❯ "))
    return input(arrow)


def _print_welcome(run_dir: str, abs_prompt: str, chat_model: str,
                   report_language: str, caller_name: str) -> None:
    basename = os.path.basename(abs_prompt).rsplit(".", 1)[0]
    line_count = sum(1 for _ in open(os.path.join(run_dir, "body.txt"), encoding="utf-8"))
    bar = _c(_COL_DIM, "─" * 60)
    print()
    print(bar)
    if report_language == "tr":
        print(_c(_COL_BOLD, "  /prompt-chat — interactive persona simulator (v0.5.11)"))
        print(bar)
        print(f"  Prompt:    {_c(_COL_BOLD, basename)} ({line_count} satır, model: {chat_model})")
        print(f"  Arayan:    {_c(_COL_BOLD, caller_name)}")
        print(f"  İzolasyon: yeni pencere · bare Claude session")
        print(bar)
        print(_c(_COL_DIM, "  Bot birazdan kendisi selamlayacak — sen aramayı cevapladın."))
        print(_c(_COL_DIM, "  Komutlar: /save  /history  /reset  /silence <N>  /commit  /help  /quit"))
        if _READLINE_AVAILABLE:
            print(_c(_COL_DIM, "  İpucu: ok tuşları geçmiş mesajları gezer."))
        print(bar)
    else:
        print(_c(_COL_BOLD, "  /prompt-chat — interactive persona simulator (v0.5.11)"))
        print(bar)
        print(f"  Prompt:    {_c(_COL_BOLD, basename)} ({line_count} lines, model: {chat_model})")
        print(f"  Caller:    {_c(_COL_BOLD, caller_name)}")
        print(f"  Isolation: new window · bare Claude session")
        print(bar)
        print(_c(_COL_DIM, "  The bot will greet you first — you're the caller answering."))
        print(_c(_COL_DIM, "  Commands: /save  /history  /reset  /silence <N>  /commit  /help  /quit"))
        if _READLINE_AVAILABLE:
            print(_c(_COL_DIM, "  Tip: arrow keys browse previous input."))
        print(bar)


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
