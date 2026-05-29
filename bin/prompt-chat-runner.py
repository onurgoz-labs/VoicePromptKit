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
import re
import shutil
import subprocess
import sys
import threading
import uuid
from queue import Empty, Queue


NEUTRAL_CWD = "/tmp"  # subprocess cwd — no CLAUDE.md auto-discovery here
CLAUDE_CLI = shutil.which("claude") or "/Users/onur/.local/bin/claude"


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

8. **Mid-call interruptions, silences, off-topic comments from the caller are EXPECTED.** Handle them per the script's interruption / silence / off-scope rules. Do not break character to comment on the disruption.

YOUR PERSONA SCRIPT — internalise this as your voice, your rules, your scope. The call begins when the user sends their first message.

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

    _print_welcome(run_dir, abs_prompt, chat_model, report_language)

    # v0.5.8: wrap body.txt with persona roleplay framing before passing to
    # claude. The original body.txt is left untouched; .body-wrapped.txt is
    # the framed version the subprocess actually reads as its system prompt.
    wrapped_body_path = _write_wrapped_body(run_dir, body_path)

    # Spawn the long-lived claude subprocess ONCE.
    ctx = _SubprocessCtx(wrapped_body_path, chat_session_uuid, chat_model)
    ctx.start()

    try:
        return _repl(ctx, run_dir, body_path, session_path, chat_jsonl_path,
                    staged_path, abs_prompt, report_language, session)
    finally:
        ctx.close()


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
            user_input = input("❯ ").strip()
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

        # Normal turn — append user, stream reply from subprocess.
        _append_chat_entry(chat_jsonl_path, "user", user_input)
        print()  # blank line before the streaming reply
        try:
            reply = ctx.send(user_input, stream_to=sys.stdout)
        except RuntimeError as e:
            print(f"\n[chat error: {e}]\n", file=sys.stderr)
            continue

        _append_chat_entry(chat_jsonl_path, "assistant", reply)
        session["turns"] = session.get("turns", 0) + 1
        _atomic_write_json(session_path, session)

        cmds = "/save | /history | /commit | /quit"
        if report_language == "tr":
            print(f"\n― [tur {session['turns']} · {cmds}]\n")
        else:
            print(f"\n― [turn {session['turns']} · {cmds}]\n")


# ---------------------------------------------------------- slash commands


def _dispatch_slash(user_input: str, run_dir: str, session_path: str,
                    chat_jsonl_path: str, staged_path: str, abs_prompt: str,
                    report_language: str, session: dict,
                    ctx: "_SubprocessCtx") -> bool:
    cmd = user_input.lstrip("/").strip().split(None, 1)[0].lower()
    if cmd == "save":
        _handle_save(chat_jsonl_path, staged_path, report_language, session_path, session)
    elif cmd == "history":
        _handle_history(chat_jsonl_path, report_language)
    elif cmd == "reset":
        _handle_reset(run_dir, chat_jsonl_path, session_path, session, report_language, ctx)
    elif cmd == "commit":
        _handle_commit(abs_prompt, staged_path, session_path, session, report_language)
    elif cmd == "quit":
        _exit_cleanly(run_dir, session_path, chat_jsonl_path, staged_path,
                      abs_prompt, report_language, "user_quit", session)
        return True
    else:
        msg = (f"Unknown command: /{cmd}. Available: /save /history /reset /commit /quit"
               if report_language == "en"
               else f"Bilinmeyen komut: /{cmd}. Mevcut: /save /history /reset /commit /quit")
        print(msg)
    return False


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


def _handle_history(chat_jsonl_path: str, report_language: str) -> None:
    entries = _read_chat_jsonl(chat_jsonl_path)
    if not entries:
        print("(boş)" if report_language == "tr" else "(empty)")
        return
    print()
    for i, e in enumerate(entries, 1):
        print(f"{i:3d}. [{e.get('role', '?')}] {_truncate(e.get('content', ''), 100)}")
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
    _atomic_write_json(session_path, session)

    # Tear down old claude subprocess and spawn a fresh one with the new session id.
    ctx.close()
    ctx.session_uuid = session["chat_session_uuid"]
    ctx.start()

    msg = (f"Sıfırlandı. Geçmiş arşivlendi: {os.path.basename(archive)}. Yeni mesaj fresh persona ile başlar."
           if report_language == "tr"
           else f"Reset. History archived to {os.path.basename(archive)}. Next message starts a fresh persona.")
    print(msg)


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


def _print_welcome(run_dir: str, abs_prompt: str, chat_model: str,
                   report_language: str) -> None:
    basename = os.path.basename(abs_prompt).rsplit(".", 1)[0]
    line_count = sum(1 for _ in open(os.path.join(run_dir, "body.txt"), encoding="utf-8"))
    if report_language == "tr":
        print(f"\n/prompt-chat başlatıldı.")
        print(f"Prompt: {basename} ({line_count} satır, model: {chat_model})")
        print("İzolasyon: yeni pencere — bare Claude session (v0.5.6)\n")
        print("Yaz, ben prompt'a göre cevap vereyim.\n")
        print("Komutlar:")
        print("  /save     — son turu anchor olarak kaydet (staging)")
        print("  /history  — bu oturumdaki turları göster")
        print("  /reset    — geçmişi sil, baştan başla")
        print(f"  /commit   — staged anchor'ları sidecar'a ({basename}.md.anchors.yaml) yaz")
        print("  /quit     — çıkış + final summary\n")
    else:
        print(f"\n/prompt-chat started.")
        print(f"Prompt: {basename} ({line_count} lines, model: {chat_model})")
        print("Isolation: new window — bare Claude session (v0.5.6)\n")
        print("Type your message; I'll reply as the prompt's persona.\n")
        print("Commands:")
        print("  /save     — capture last turn as a test anchor (staging)")
        print("  /history  — show this session's turns")
        print("  /reset    — discard history, start over")
        print(f"  /commit   — write staged anchors to sidecar ({basename}.md.anchors.yaml)")
        print("  /quit     — exit with a final summary\n")


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
