"""Local browser interface for final human validation of generated Anki cards."""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from anki_connect import DEFAULT_ANKI_URL, DEFAULT_SENTENCE_MODEL, DEFAULT_WORD_MODEL, push_approved_exports
from card_review_ui import ManualReviewItem, accepted_cards, export_accepted_cards, load_review_items, read_audit_csv
from card_review_ui import write_manual_review
from episode_to_anki import VocabCard, suffixed_output_path, validate_cards


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Anki Card Final Validation</title>
<style>
:root { --bg:#f6f3ec; --panel:#fffdf8; --ink:#24231f; --sub:#6b665a; --line:#ded7c9;
  --accent:#235c55; --accept:#217346; --pending:#a26018; --reject:#9b3232; }
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); background:var(--bg); font:15px "Segoe UI", Arial, sans-serif; }
header { padding:18px 24px 14px; border-bottom:1px solid var(--line); background:var(--panel); }
h1 { margin:0 0 5px; font-size:24px; font-weight:600; }
.subtitle { color:var(--sub); }
.toolbar { display:flex; gap:10px; align-items:center; margin-top:14px; flex-wrap:wrap; }
button, select, input, textarea { font:inherit; }
button { border:1px solid var(--line); background:white; border-radius:7px; padding:8px 13px; cursor:pointer; }
button.primary { background:var(--accent); color:white; border-color:var(--accent); }
button:hover { filter:brightness(.97); }
.counts { margin-left:auto; color:var(--sub); }
main { height:calc(100vh - 113px); display:grid; grid-template-columns:minmax(390px,44%) 1fr; }
.list { border-right:1px solid var(--line); overflow:auto; background:var(--panel); }
.cardrow { display:grid; grid-template-columns:95px 1fr 1fr; gap:8px; padding:11px 14px;
  border-bottom:1px solid #eee7db; cursor:pointer; }
.cardrow:hover, .cardrow.selected { background:#eee8dc; }
.pill { font-size:12px; font-weight:600; border-radius:99px; padding:4px 8px; height:max-content; text-align:center; }
.accept { color:var(--accept); background:#ddefe4; }
.needs_review { color:var(--pending); background:#f6e6cb; }
.reject { color:var(--reject); background:#f4dddd; }
.term { font-size:16px; }
.meaning, .origin { color:var(--sub); }
.editor { overflow:auto; padding:22px 26px; }
.field { margin-bottom:15px; }
label { display:block; font-size:13px; color:var(--sub); margin-bottom:5px; }
input[type=text], textarea, select { width:100%; border:1px solid var(--line); border-radius:7px;
  background:white; padding:9px 10px; }
textarea { min-height:86px; resize:vertical; }
.row { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.decisionbuttons { display:flex; gap:8px; margin:18px 0; }
.validation { min-height:48px; border-radius:8px; padding:11px; background:#ede9df; color:var(--sub); }
.validation.problem { color:#84351f; background:#f7e3d8; }
.message { color:var(--accent); min-height:20px; }
@media (max-width: 900px) { main { display:block; height:auto; } .list { max-height:42vh; border-right:0; } }
</style>
</head>
<body>
<header>
  <h1>Anki Card Final Validation</h1>
  <div class="subtitle">Review corrections and decide what is safe to export before Anki import.</div>
  <div class="toolbar">
    <label style="margin:0">Show
      <select id="filter"><option>all</option><option>accept</option><option>needs_review</option><option>reject</option></select>
    </label>
    <button id="save">Save Review</button>
    <button id="export" class="primary">Export Accepted</button>
    <button id="push">Push Accepted to Anki</button>
    <span class="message" id="message"></span>
    <span class="counts" id="counts"></span>
  </div>
</header>
<main>
  <section class="list" id="list"></section>
  <section class="editor">
    <div class="row">
      <div class="field"><label>Ukrainian</label><input id="ukrainian" type="text"></div>
      <div class="field"><label>English</label><input id="english" type="text"></div>
    </div>
    <div class="field"><label>Example</label><textarea id="example"></textarea></div>
    <div class="field"><label>Example target for sentence card</label><input id="target" type="text"></div>
    <div class="row">
      <div class="field"><label>Decision</label>
        <select id="decision"><option>accept</option><option>needs_review</option><option>reject</option></select>
      </div>
      <div class="field"><label>Source queue</label><input id="origin" type="text" readonly></div>
    </div>
    <div class="field"><label>Reviewer note</label><textarea id="reason"></textarea></div>
    <div class="decisionbuttons">
      <button onclick="decide('accept')">Accept + Next</button>
      <button onclick="decide('needs_review')">Needs Review + Next</button>
      <button onclick="decide('reject')">Reject + Next</button>
    </div>
    <div class="validation" id="validation">Choose a card to review.</div>
  </section>
</main>
<script>
let cards = [], selected = 0, timer;
const $ = id => document.getElementById(id);
async function request(path, method='GET', payload=null) {
  const options = { method, headers:{'Content-Type':'application/json'} };
  if (payload) options.body = JSON.stringify(payload);
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function escapeHtml(value) { return String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function current() { return cards[selected]; }
function updateCounts() {
  const count = d => cards.filter(c => c.decision === d).length;
  $('counts').textContent = `Accept: ${count('accept')}  Needs review: ${count('needs_review')}  Reject: ${count('reject')}`;
}
function renderList() {
  const filter = $('filter').value;
  $('list').innerHTML = cards.map((card, i) => {
    if (filter !== 'all' && card.decision !== filter) return '';
    return `<div class="cardrow ${i === selected ? 'selected' : ''}" onclick="selectCard(${i})">
      <span class="pill ${card.decision}">${escapeHtml(card.decision)}</span>
      <span class="term">${escapeHtml(card.ukrainian)}<br><small class="origin">${escapeHtml(card.origin)}</small></span>
      <span class="meaning">${escapeHtml(card.english)}</span>
    </div>`;
  }).join('');
  updateCounts();
}
function saveForm() {
  if (!cards.length) return;
  const card = current();
  card.ukrainian = $('ukrainian').value.trim();
  card.english = $('english').value.trim();
  card.example = $('example').value.trim();
  card.example_target = $('target').value.trim();
  card.decision = $('decision').value;
  card.reason = $('reason').value.trim();
}
function selectCard(i) {
  saveForm(); selected = i; const card = current();
  $('ukrainian').value = card.ukrainian; $('english').value = card.english;
  $('example').value = card.example; $('target').value = card.example_target;
  $('decision').value = card.decision; $('reason').value = card.reason || '';
  $('origin').value = card.origin || ''; renderList(); validateCurrent();
}
function nextVisible() {
  const filter = $('filter').value;
  for (let i = selected + 1; i < cards.length; i++) if (filter === 'all' || cards[i].decision === filter) return selectCard(i);
}
function decide(value) { $('decision').value = value; saveForm(); renderList(); nextVisible(); }
async function validateCurrent() {
  saveForm(); if (!cards.length) return;
  const data = await request('/api/validate', 'POST', current());
  const node = $('validation');
  if (data.issues.length) { node.classList.add('problem'); node.textContent = data.issues.map(i => `${i.severity.toUpperCase()}: ${i.message}`).join('\\n'); }
  else { node.classList.remove('problem'); node.textContent = current().example_target ? 'Sentence target passes deterministic validation.' : 'Word card only: no safe sentence target selected.'; }
}
async function persist(path) {
  saveForm();
  if (path === '/api/export' || path === '/api/push-anki') {
    const pending = cards.filter(c => c.decision === 'needs_review').length;
    const action = path === '/api/push-anki' ? 'push accepted cards to Anki' : 'export accepted cards';
    if (pending && !window.confirm(`${pending} cards still need review. ${action} anyway?`)) return;
    if (path === '/api/push-anki' && !window.confirm('Push currently accepted cards into the configured Anki deck?')) return;
  }
  const result = await request(path, 'POST', {cards});
  $('message').textContent = result.message;
  setTimeout(() => $('message').textContent = '', 5000);
}
async function init() {
  const data = await request('/api/cards'); cards = data.cards;
  renderList(); if (cards.length) selectCard(0);
  $('filter').addEventListener('change', renderList);
  $('save').addEventListener('click', () => persist('/api/save'));
  $('export').addEventListener('click', () => persist('/api/export'));
  $('push').addEventListener('click', () => persist('/api/push-anki'));
  ['ukrainian','english','example','target','decision'].forEach(id => $(id).addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(validateCurrent, 250); }));
}
init().catch(error => $('message').textContent = error.message);
</script>
</body>
</html>"""


def item_to_dict(item: ManualReviewItem) -> Dict[str, str]:
    card = item.card
    return {
        "ukrainian": card.ukrainian,
        "english": card.english,
        "example": card.example,
        "example_target": card.example_target,
        "decision": item.decision,
        "reason": item.reason,
        "origin": item.origin,
        "tags": card.tags,
        "source": card.source,
        "episode": card.episode,
    }


def item_from_dict(data: Dict[str, Any]) -> ManualReviewItem:
    decision = str(data.get("decision", "needs_review"))
    if decision not in {"accept", "needs_review", "reject"}:
        decision = "needs_review"
    return ManualReviewItem(
        card=VocabCard(
            ukrainian=str(data.get("ukrainian", "")).strip(),
            english=str(data.get("english", "")).strip(),
            example=str(data.get("example", "")).strip(),
            example_target=str(data.get("example_target", "")).strip(),
            tags=str(data.get("tags", "ukrainian podcast transcript vocab")).strip(),
            source=str(data.get("source", "")).strip(),
            episode=str(data.get("episode", "")).strip(),
        ),
        decision=decision,
        origin=str(data.get("origin", "")).strip(),
        reason=str(data.get("reason", "")).strip(),
    )


class ReviewState:
    def __init__(
        self,
        items: List[ManualReviewItem],
        session_output: Path,
        output: Path,
        card_format: str,
        *,
        anki_deck: Optional[str] = None,
        anki_word_model: str = DEFAULT_WORD_MODEL,
        anki_sentence_model: str = DEFAULT_SENTENCE_MODEL,
        anki_url: str = DEFAULT_ANKI_URL,
        anki_update_existing: bool = False,
    ) -> None:
        self.items = items
        self.session_output = session_output
        self.output = output
        self.card_format = card_format
        self.anki_deck = anki_deck
        self.anki_word_model = anki_word_model
        self.anki_sentence_model = anki_sentence_model
        self.anki_url = anki_url
        self.anki_update_existing = anki_update_existing
        self.lock = threading.Lock()

    def replace_items(self, payload: Dict[str, Any]) -> None:
        values = payload.get("cards", [])
        if not isinstance(values, list):
            raise ValueError("Expected cards list.")
        self.items = [item_from_dict(value) for value in values if isinstance(value, dict)]


def make_handler(state: ReviewState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                content = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            elif path == "/api/cards":
                with state.lock:
                    self.send_json({"cards": [item_to_dict(item) for item in state.items]})
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if path == "/api/validate":
                    item = item_from_dict(payload)
                    issues = [
                        {"severity": issue.severity, "code": issue.code, "message": issue.message}
                        for issue in validate_cards([item.card])
                        if issue.severity in {"error", "warning"}
                    ]
                    self.send_json({"issues": issues})
                    return
                if path not in {"/api/save", "/api/export", "/api/push-anki"}:
                    self.send_error(404)
                    return
                with state.lock:
                    state.replace_items(payload)
                    count = write_manual_review(state.items, state.session_output)
                    if path == "/api/save":
                        self.send_json({"message": f"Saved {count} decisions to {state.session_output.name}."})
                        return
                    exported = export_accepted_cards(state.items, state.output, state.card_format)
                    pending = sum(item.decision == "needs_review" for item in state.items)
                    if path == "/api/push-anki":
                        if not state.anki_deck:
                            raise ValueError("Restart the UI with --anki-deck before pushing cards to Anki.")
                        if state.card_format != "bidirectional-with-sentences":
                            raise ValueError("Direct Anki push currently requires bidirectional-with-sentences export.")
                        result = push_approved_exports(
                            suffixed_output_path(state.output, "words"),
                            suffixed_output_path(state.output, "sentences"),
                            deck=state.anki_deck,
                            word_model=state.anki_word_model,
                            sentence_model=state.anki_sentence_model,
                            url=state.anki_url,
                            update_existing=state.anki_update_existing,
                        )
                        message = (
                            f"Pushed accepted cards to {state.anki_deck}: "
                            f"{result.added} added, {result.updated} updated, {result.skipped} skipped."
                        )
                        if pending:
                            message += f" {pending} cards remain pending."
                        self.send_json({"message": message})
                        return
                    message = f"Exported {exported} accepted rows from {state.output.name}."
                    if pending:
                        message += f" {pending} cards remain pending."
                    self.send_json({"message": message})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)

    return Handler


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open a browser UI for final flashcard validation.")
    parser.add_argument("--input", type=Path, help="Accepted/reviewed fields CSV to load.")
    parser.add_argument("--needs-review", type=Path, help="Optional *_needs_review.csv audit to inspect.")
    parser.add_argument("--rejected", type=Path, help="Optional *_rejected.csv audit to inspect.")
    parser.add_argument("--resume-session", type=Path, help="Reopen a previously saved manual-review CSV.")
    parser.add_argument("--session-output", type=Path, help="Where all manual decisions are saved.")
    parser.add_argument("--output", type=Path, help="Accepted-card export base CSV.")
    parser.add_argument(
        "--card-format",
        choices=["fields", "basic", "basic-with-sentences", "bidirectional-with-sentences"],
        default="bidirectional-with-sentences",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="Local port; defaults to an available random port.")
    parser.add_argument("--no-browser", action="store_true", help="Print the local URL without opening a browser tab.")
    parser.add_argument("--anki-deck", help="Deck used by the Push Accepted to Anki button.")
    parser.add_argument("--anki-word-model", default=DEFAULT_WORD_MODEL)
    parser.add_argument("--anki-sentence-model", default=DEFAULT_SENTENCE_MODEL)
    parser.add_argument("--anki-url", default=DEFAULT_ANKI_URL)
    parser.add_argument("--anki-update-existing", action="store_true", help="Update prior notes pushed by this tool.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.resume_session:
        items = read_audit_csv(args.resume_session, default_decision="needs_review")
        source_path = args.resume_session
    elif args.input:
        items = load_review_items(args.input, needs_review_path=args.needs_review, rejected_path=args.rejected)
        source_path = args.input
    else:
        raise SystemExit("Provide --input or --resume-session.")
    state = ReviewState(
        items,
        args.session_output or source_path.with_name(f"{source_path.stem}_manual_review.csv"),
        args.output or source_path.parent / "approved" / f"{source_path.stem}_approved.csv",
        args.card_format,
        anki_deck=args.anki_deck,
        anki_word_model=args.anki_word_model,
        anki_sentence_model=args.anki_sentence_model,
        anki_url=args.anki_url,
        anki_update_existing=args.anki_update_existing,
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    url = f"http://{args.host}:{server.server_port}/"
    print(f"Final validation interface running at {url}")
    print("Press Ctrl+C in this terminal when finished.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
