"""Local three-tab dashboard: transcribe, generate flashcards, and review."""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set
from urllib.parse import parse_qs, unquote, urlparse

from anki_connect import DEFAULT_ANKI_URL, DEFAULT_SENTENCE_MODEL, DEFAULT_WORD_MODEL, push_approved_exports
from card_review_ui import export_accepted_cards, load_review_items, write_manual_review
from card_review_web import item_from_dict, item_to_dict
from episode_to_anki import anki_invoke, safe_filename, select_episode, suffixed_output_path, validate_cards
from podcast_to_anki import DEFAULT_RSS_URL, fetch_text, parse_rss

REPO_ROOT = Path(__file__).resolve().parent
STATIC_DIR = REPO_ROOT / "dashboard" / "static"
TRANSCRIPT_DIR = REPO_ROOT / "transcripts"
AUDIO_DIR = REPO_ROOT / "audio"
OUTPUTS_DIR = REPO_ROOT / "outputs"
LOGS_DIR = REPO_ROOT / "logs"
DEFAULT_PORT = 8787
DEFAULT_CROSSCHECK_DECK = "Ukrainian"
DEFAULT_EPISODE_LIMIT = 40
_RSS_CACHE: Dict[str, Any] = {"fetched_at": 0.0, "episodes": []}
_RSS_CACHE_TTL_SECONDS = 120.0


@dataclass
class JobRecord:
    job_id: str
    kind: str
    label: str
    status: str = "queued"
    command: List[str] = field(default_factory=list)
    log_path: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    returncode: Optional[int] = None
    error: str = ""
    episode_key: str = ""
    output_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["command"] = " ".join(self.command)
        return payload


def terminate_process_tree(pid: int) -> None:
    """Stop a job process and any children (needed for long local Whisper runs)."""
    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
            text=True,
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def force_stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    terminate_process_tree(process.pid)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


class JobQueue:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: Dict[str, JobRecord] = {}
        self.pending: "queue.Queue[str]" = queue.Queue()
        self.processes: Dict[str, subprocess.Popen] = {}
        self.cancel_flags: Set[str] = set()
        self.worker = threading.Thread(target=self._run_loop, daemon=True)
        self.worker.start()

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda job: job.created_at, reverse=True)
            return [job.to_dict() for job in jobs]

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self.lock:
            return self.jobs.get(job_id)

    def enqueue(self, job: JobRecord) -> JobRecord:
        with self.lock:
            self.jobs[job.job_id] = job
        self.pending.put(job.job_id)
        return job

    def cancel(self, job_id: str) -> JobRecord:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in {"done", "failed", "cancelled"}:
                return job
            self.cancel_flags.add(job_id)
            process = self.processes.get(job_id)
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = time.time()
                job.error = "Cancelled before start."
                return job
        if process is not None:
            force_stop_process(process)
        with self.lock:
            if job.status == "running":
                job.status = "cancelled"
                job.finished_at = time.time()
                job.error = "Cancelled by user."
                if process is not None:
                    job.returncode = process.returncode
            return job

    def _run_loop(self) -> None:
        while True:
            job_id = self.pending.get()
            job = self.get(job_id)
            if job is None:
                continue
            with self.lock:
                if job.status == "cancelled" or job_id in self.cancel_flags:
                    if job.status != "cancelled":
                        job.status = "cancelled"
                        job.finished_at = time.time()
                        job.error = job.error or "Cancelled before start."
                    continue
                job.status = "running"
                job.started_at = time.time()
            log_path = Path(job.log_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            process: Optional[subprocess.Popen] = None
            try:
                with log_path.open("a", encoding="utf-8") as log_file:
                    log_file.write(f"\n=== Starting {job.kind}: {job.label} ===\n")
                    log_file.write("Command: " + " ".join(job.command) + "\n")
                    log_file.flush()
                    popen_kwargs: Dict[str, Any] = {
                        "args": job.command,
                        "cwd": str(REPO_ROOT),
                        "stdout": log_file,
                        "stderr": subprocess.STDOUT,
                        "env": os.environ.copy(),
                    }
                    if sys.platform == "win32":
                        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                    else:
                        popen_kwargs["start_new_session"] = True
                    process = subprocess.Popen(**popen_kwargs)
                    with self.lock:
                        self.processes[job_id] = process
                        already_cancelled = job_id in self.cancel_flags
                    if already_cancelled:
                        force_stop_process(process)
                    while process.poll() is None:
                        with self.lock:
                            should_stop = job_id in self.cancel_flags
                        if should_stop:
                            force_stop_process(process)
                            break
                        time.sleep(0.4)
                    returncode = process.poll()
                    if returncode is None:
                        force_stop_process(process)
                        returncode = process.poll()
                    if returncode is None:
                        returncode = -1
                with self.lock:
                    self.processes.pop(job_id, None)
                    job.returncode = returncode
                    job.finished_at = time.time()
                    if job_id in self.cancel_flags or job.status == "cancelled":
                        job.status = "cancelled"
                        job.error = job.error or "Cancelled by user."
                        try:
                            with log_path.open("a", encoding="utf-8") as note:
                                note.write("\n=== Cancelled by user ===\n")
                        except OSError:
                            pass
                    elif returncode == 0:
                        job.status = "done"
                    else:
                        job.status = "failed"
                        job.error = f"Process exited with code {returncode}"
            except Exception as exc:  # noqa: BLE001
                with self.lock:
                    self.processes.pop(job_id, None)
                    if job_id in self.cancel_flags:
                        job.status = "cancelled"
                        job.error = "Cancelled by user."
                    else:
                        job.status = "failed"
                        job.error = str(exc)
                    job.finished_at = time.time()
                    if process is not None and process.poll() is None:
                        force_stop_process(process)

def episode_slug(title: str) -> str:
    match = re.search(r"ULP\s*([0-9]+)\s*[-–—]\s*([0-9]+)", title, flags=re.IGNORECASE)
    if match:
        return f"ulp_{int(match.group(1))}_{int(match.group(2))}"
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", title).strip("_").lower()
    return (cleaned[:40] or "episode")


def episode_short_name(title: str) -> str:
    match = re.search(r"ULP\s*[0-9]+\s*[-–—]\s*[0-9]+", title, flags=re.IGNORECASE)
    if match:
        return re.sub(r"\s+", " ", match.group(0)).replace("–", "-").replace("—", "-")
    return title.split("|")[0].strip()[:80]


def transcript_path_for(title: str, episode_name: str = "") -> Path:
    name = episode_name or episode_short_name(title)
    return TRANSCRIPT_DIR / safe_filename(name, ".txt")


def audio_path_for(title: str) -> Path:
    return AUDIO_DIR / safe_filename(title, ".mp3")


def output_dir_for(slug: str) -> Path:
    return OUTPUTS_DIR / slug


def find_first(path: Path, pattern: str) -> Optional[Path]:
    matches = sorted(path.glob(pattern))
    return matches[0] if matches else None


def pipeline_status_for(slug: str, transcript: Path) -> Dict[str, Any]:
    out = output_dir_for(slug)
    extracted = find_first(out, "*_extracted.csv") or find_first(out, "*extracted*.csv")
    reviewed = find_first(out, "*_reviewed_checkpoint.csv") or find_first(out, "*reviewed_checkpoint*.csv")
    needs = find_first(out, "*_needs_review.csv")
    rejected = find_first(out, "*_rejected.csv")
    approved_dir = out / "approved"
    approved = None
    if approved_dir.exists():
        approved = find_first(approved_dir, "*_words.csv") or find_first(approved_dir, "*approved*")
    stage = "none"
    if transcript.exists():
        stage = "transcript"
    if extracted and extracted.exists():
        stage = "extracted"
    if reviewed and reviewed.exists():
        stage = "reviewed"
    if approved and approved.exists():
        stage = "approved"
    return {
        "stage": stage,
        "output_dir": str(out) if out.exists() else "",
        "extracted": str(extracted) if extracted else "",
        "reviewed_checkpoint": str(reviewed) if reviewed else "",
        "needs_review": str(needs) if needs else "",
        "rejected": str(rejected) if rejected else "",
        "approved": str(approved) if approved else "",
    }


def load_rss_episodes(*, force: bool = False) -> List[Any]:
    now = time.time()
    if (
        not force
        and _RSS_CACHE["episodes"]
        and now - float(_RSS_CACHE["fetched_at"]) < _RSS_CACHE_TTL_SECONDS
    ):
        return list(_RSS_CACHE["episodes"])
    episodes = parse_rss(fetch_text(DEFAULT_RSS_URL))
    _RSS_CACHE["episodes"] = episodes
    _RSS_CACHE["fetched_at"] = now
    return list(episodes)


def build_episode_row(index: int, episode: Any) -> Dict[str, Any]:
    short = episode_short_name(episode.title)
    slug = episode_slug(episode.title)
    tpath = transcript_path_for(episode.title, short)
    apath = audio_path_for(episode.title)
    alt = TRANSCRIPT_DIR / safe_filename(short.replace(" ", "_"), ".txt")
    transcript = tpath if tpath.exists() else alt if alt.exists() else tpath
    for candidate in TRANSCRIPT_DIR.glob("*.txt"):
        if short.casefold().replace(" ", "") in candidate.stem.casefold().replace(" ", "").replace("_", ""):
            transcript = candidate
            break
    status = pipeline_status_for(slug, transcript)
    return {
        "index": index,
        "title": episode.title,
        "short_name": short,
        "slug": slug,
        "published": episode.published,
        "audio_url": episode.audio_url,
        "has_audio": apath.exists(),
        "has_transcript": transcript.exists(),
        "transcript_path": str(transcript) if transcript.exists() else str(tpath),
        "audio_path": str(apath),
        "stage": status["stage"],
        "pipeline": status,
    }


def list_rss_episodes(
    *,
    search: str = "",
    offset: int = 0,
    limit: int = DEFAULT_EPISODE_LIMIT,
    index_from: Optional[int] = None,
    index_to: Optional[int] = None,
) -> Dict[str, Any]:
    episodes = load_rss_episodes()
    feed_total = len(episodes)
    matched: List[tuple[int, Any]] = list(enumerate(episodes))

    if index_from is not None or index_to is not None:
        start = 0 if index_from is None else max(0, index_from)
        end = feed_total - 1 if index_to is None else min(feed_total - 1, index_to)
        if start > end:
            start, end = end, start
        matched = [(index, episode) for index, episode in matched if start <= index <= end]

    needle = search.strip().casefold()
    if needle:
        matched = [
            (index, episode)
            for index, episode in matched
            if needle in episode.title.casefold()
            or needle in episode_short_name(episode.title).casefold()
        ]

    total = len(matched)
    offset = max(0, offset)
    limit = max(1, min(limit, 200))
    page = matched[offset : offset + limit]
    rows = [build_episode_row(index, episode) for index, episode in page]
    return {
        "episodes": rows,
        "total": total,
        "feed_total": feed_total,
        "offset": offset,
        "limit": limit,
        "search": search.strip(),
        "index_from": index_from,
        "index_to": index_to,
        "has_more": offset + len(rows) < total,
    }


def list_transcript_inventory() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not TRANSCRIPT_DIR.exists():
        return rows
    rss_by_stem = {}
    try:
        for index, episode in enumerate(load_rss_episodes()):
            row = build_episode_row(index, episode)
            rss_by_stem[Path(row["transcript_path"]).stem.casefold()] = row
            rss_by_stem[row["short_name"].casefold()] = row
            rss_by_stem[row["slug"].casefold()] = row
    except Exception:  # noqa: BLE001
        rss_by_stem = {}

    for path in sorted(TRANSCRIPT_DIR.glob("*.txt")):
        stem = path.stem
        short = stem.replace("_", " ")
        slug = episode_slug(stem)
        matched = rss_by_stem.get(stem.casefold()) or rss_by_stem.get(short.casefold())
        if matched:
            short = matched["short_name"]
            slug = matched["slug"]
            title = matched["title"]
            index = matched["index"]
        else:
            title = short
            index = None
        status = pipeline_status_for(slug, path)
        rows.append(
            {
                "title": title,
                "short_name": short,
                "slug": slug,
                "index": index,
                "transcript_path": str(path),
                "chars": path.stat().st_size,
                "stage": status["stage"],
                "pipeline": status,
                "default_deck": f"Ukrainian::{short}",
            }
        )
    return rows


def list_reviewable() -> List[Dict[str, Any]]:
    rows = []
    for item in list_transcript_inventory():
        reviewed = item["pipeline"].get("reviewed_checkpoint") or ""
        if reviewed and Path(reviewed).exists():
            rows.append(item)
    # Also pick up output folders that have reviewed checkpoints without matching transcript naming.
    if OUTPUTS_DIR.exists():
        known = {row["slug"] for row in rows}
        for folder in sorted(OUTPUTS_DIR.iterdir()):
            if not folder.is_dir() or folder.name in known or folder.name == "misc":
                continue
            reviewed = find_first(folder, "*_reviewed_checkpoint.csv")
            if not reviewed:
                continue
            needs = find_first(folder, "*_needs_review.csv")
            rejected = find_first(folder, "*_rejected.csv")
            rows.append(
                {
                    "title": folder.name,
                    "short_name": folder.name.replace("_", " "),
                    "slug": folder.name,
                    "index": None,
                    "transcript_path": "",
                    "chars": 0,
                    "stage": "reviewed",
                    "pipeline": {
                        "stage": "reviewed",
                        "output_dir": str(folder),
                        "reviewed_checkpoint": str(reviewed),
                        "needs_review": str(needs) if needs else "",
                        "rejected": str(rejected) if rejected else "",
                        "approved": "",
                    },
                    "default_deck": f"Ukrainian::{folder.name.replace('_', ' ').upper()}",
                }
            )
    return rows


def anki_status(url: str = DEFAULT_ANKI_URL) -> Dict[str, Any]:
    try:
        version = anki_invoke("version", url=url, timeout=3)
        decks = anki_invoke("deckNames", url=url, timeout=5)
        return {"ok": True, "version": version, "decks": decks if isinstance(decks, list) else []}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "decks": []}


def python_command() -> List[str]:
    return [sys.executable]


def build_transcribe_command(
    *,
    episode_index: int,
    episode_name: str,
    log_path: Path,
    transcriber: str = "faster-whisper",
) -> List[str]:
    if transcriber not in {"faster-whisper", "openai"}:
        raise ValueError("transcriber must be faster-whisper or openai")
    model = "whisper-1" if transcriber == "openai" else "medium"
    return [
        *python_command(),
        str(REPO_ROOT / "episode_to_anki.py"),
        "--episode-index",
        str(episode_index),
        "--episode-name",
        episode_name,
        "--transcribe-only",
        "--transcriber",
        transcriber,
        "--transcription-model",
        model,
        "--log-level",
        "INFO",
        "--log-file",
        str(log_path),
    ]


def build_generate_command(
    *,
    transcript_path: Path,
    episode_name: str,
    slug: str,
    provider: str,
    log_path: Path,
    synthesize_examples: bool = False,
) -> List[str]:
    output = output_dir_for(slug) / f"anki_vocab_{slug}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        *python_command(),
        str(REPO_ROOT / "episode_to_anki.py"),
        "--transcript-file",
        str(transcript_path),
        "--episode-name",
        episode_name,
        "--output",
        str(output),
        "--card-format",
        "bidirectional-with-sentences",
        "--review-cards",
        "--crosscheck-anki",
        "--crosscheck-anki-deck",
        DEFAULT_CROSSCHECK_DECK,
        "--log-level",
        "INFO",
        "--log-file",
        str(log_path),
    ]
    if provider == "openai":
        cmd.extend(
            [
                "--vocab-provider",
                "openai",
                "--review-provider",
                "openai",
                "--text-model",
                "gpt-4o-mini",
                "--batch-chars",
                "4000",
                "--cards-per-batch",
                "12",
            ]
        )
        if synthesize_examples:
            cmd.append("--synthesize-examples")
    else:
        cmd.extend(
            [
                "--vocab-provider",
                "ollama",
                "--review-provider",
                "ollama",
                "--ollama-model",
                "qwen3:4b",
                "--batch-chars",
                "1200",
                "--cards-per-batch",
                "6",
                "--review-batch-size",
                "4",
                "--ollama-timeout",
                "900",
                "--recover-needs-review",
            ]
        )
    return cmd


class DashboardState:
    def __init__(self) -> None:
        self.jobs = JobQueue()
        self.review_lock = threading.Lock()
        self.review_items: List[Any] = []
        self.review_session_output = OUTPUTS_DIR / "misc" / "dashboard_manual_review.csv"
        self.review_export_output = OUTPUTS_DIR / "misc" / "approved" / "dashboard_approved.csv"
        self.review_anki_deck = "Ukrainian::ULP"
        self.review_card_format = "bidirectional-with-sentences"
        self.review_anki_url = DEFAULT_ANKI_URL
        self.review_update_existing = False


STATE = DashboardState()


def read_json_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object.")
    return data


def send_json(handler: BaseHTTPRequestHandler, payload: Dict[str, Any], status: int = 200) -> None:
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def send_file(handler: BaseHTTPRequestHandler, path: Path, content_type: str) -> None:
    data = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.end_headers()
    handler.wfile.write(data)


def load_review_for_episode(slug: str) -> Dict[str, Any]:
    inventory = {item["slug"]: item for item in list_reviewable()}
    item = inventory.get(slug)
    if item is None:
        # Fall back to scanning outputs/<slug>
        folder = output_dir_for(slug)
        reviewed = find_first(folder, "*_reviewed_checkpoint.csv")
        if not reviewed:
            raise FileNotFoundError(f"No reviewed checkpoint found for {slug}.")
        needs = find_first(folder, "*_needs_review.csv")
        rejected = find_first(folder, "*_rejected.csv")
        item = {
            "slug": slug,
            "short_name": slug.replace("_", " "),
            "default_deck": f"Ukrainian::{slug.replace('_', ' ')}",
            "pipeline": {
                "reviewed_checkpoint": str(reviewed),
                "needs_review": str(needs) if needs else "",
                "rejected": str(rejected) if rejected else "",
                "output_dir": str(folder),
            },
        }
    reviewed = Path(item["pipeline"]["reviewed_checkpoint"])
    needs = Path(item["pipeline"]["needs_review"]) if item["pipeline"].get("needs_review") else None
    rejected = Path(item["pipeline"]["rejected"]) if item["pipeline"].get("rejected") else None
    items = load_review_items(
        reviewed,
        needs_review_path=needs if needs and needs.exists() else None,
        rejected_path=rejected if rejected and rejected.exists() else None,
    )
    out_dir = Path(item["pipeline"].get("output_dir") or output_dir_for(slug))
    session = out_dir / f"{reviewed.stem}_manual_review.csv"
    export = out_dir / "approved" / f"{slug}_approved.csv"
    with STATE.review_lock:
        STATE.review_items = items
        STATE.review_session_output = session
        STATE.review_export_output = export
        STATE.review_anki_deck = item.get("default_deck") or f"Ukrainian::{item.get('short_name', slug)}"
    return {
        "slug": slug,
        "count": len(items),
        "anki_deck": STATE.review_anki_deck,
        "session_output": str(session),
        "export_output": str(export),
        "cards": [item_to_dict(value) for value in items],
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/":
                return send_file(self, STATIC_DIR / "index.html", "text/html; charset=utf-8")
            if path == "/static/app.css":
                return send_file(self, STATIC_DIR / "app.css", "text/css; charset=utf-8")
            if path == "/static/app.js":
                return send_file(self, STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            if path == "/api/status":
                anki = anki_status()
                return send_json(
                    self,
                    {
                        "openai_key_set": bool(os.environ.get("OPENAI_API_KEY")),
                        "anki": anki,
                        "crosscheck_deck": DEFAULT_CROSSCHECK_DECK,
                        "repo_root": str(REPO_ROOT),
                    },
                )
            if path == "/api/episodes":
                qs = parse_qs(parsed.query)
                limit = int((qs.get("limit") or [DEFAULT_EPISODE_LIMIT])[0])
                offset = int((qs.get("offset") or [0])[0])
                search = str((qs.get("search") or [""])[0])
                index_from = qs.get("index_from", [None])[0]
                index_to = qs.get("index_to", [None])[0]
                return send_json(
                    self,
                    list_rss_episodes(
                        search=search,
                        offset=offset,
                        limit=limit,
                        index_from=None if index_from in (None, "") else int(index_from),
                        index_to=None if index_to in (None, "") else int(index_to),
                    ),
                )
            if path == "/api/transcripts":
                return send_json(self, {"transcripts": list_transcript_inventory()})
            if path == "/api/reviewable":
                return send_json(self, {"episodes": list_reviewable()})
            if path == "/api/jobs":
                return send_json(self, {"jobs": STATE.jobs.list_jobs()})
            if path.startswith("/api/jobs/") and path.endswith("/log"):
                job_id = unquote(path[len("/api/jobs/") : -len("/log")])
                job = STATE.jobs.get(job_id)
                if job is None:
                    return send_json(self, {"error": "Unknown job."}, status=404)
                qs = parse_qs(parsed.query)
                offset = int((qs.get("offset") or [0])[0])
                log_path = Path(job.log_path)
                text = ""
                if log_path.exists():
                    data = log_path.read_text(encoding="utf-8", errors="replace")
                    text = data[offset:]
                    offset = len(data)
                return send_json(self, {"job": job.to_dict(), "chunk": text, "offset": offset})
            if path.startswith("/api/jobs/"):
                job_id = unquote(path[len("/api/jobs/") :])
                job = STATE.jobs.get(job_id)
                if job is None:
                    return send_json(self, {"error": "Unknown job."}, status=404)
                return send_json(self, {"job": job.to_dict()})
            if path == "/api/review/cards":
                with STATE.review_lock:
                    return send_json(
                        self,
                        {
                            "cards": [item_to_dict(item) for item in STATE.review_items],
                            "anki_deck": STATE.review_anki_deck,
                            "session_output": str(STATE.review_session_output),
                            "export_output": str(STATE.review_export_output),
                        },
                    )
            return send_json(self, {"error": "Not found."}, status=404)
        except Exception as exc:  # noqa: BLE001
            return send_json(self, {"error": str(exc)}, status=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = read_json_body(self)
            if path == "/api/jobs/transcribe":
                indexes = payload.get("episode_indexes") or []
                if not isinstance(indexes, list) or not indexes:
                    return send_json(self, {"error": "Provide episode_indexes."}, status=400)
                transcriber = str(payload.get("transcriber") or "faster-whisper").strip().lower()
                if transcriber not in {"faster-whisper", "openai"}:
                    return send_json(
                        self,
                        {"error": "transcriber must be faster-whisper or openai."},
                        status=400,
                    )
                if transcriber == "openai" and not os.environ.get("OPENAI_API_KEY"):
                    return send_json(
                        self,
                        {"error": "OPENAI_API_KEY is not set in this environment."},
                        status=400,
                    )
                episodes = load_rss_episodes()
                created = []
                engine_label = "openai" if transcriber == "openai" else "local"
                for raw_index in indexes:
                    index = int(raw_index)
                    episode = select_episode(episodes, index=index)
                    short = episode_short_name(episode.title)
                    slug = episode_slug(episode.title)
                    job_id = uuid.uuid4().hex[:10]
                    log_path = LOGS_DIR / f"dashboard_transcribe_{slug}_{job_id}.log"
                    command = build_transcribe_command(
                        episode_index=index,
                        episode_name=short,
                        log_path=log_path,
                        transcriber=transcriber,
                    )
                    job = JobRecord(
                        job_id=job_id,
                        kind="transcribe",
                        label=f"{short} ({engine_label})",
                        command=command,
                        log_path=str(log_path),
                        episode_key=slug,
                    )
                    STATE.jobs.enqueue(job)
                    created.append(job.to_dict())
                return send_json(self, {"jobs": created})

            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = unquote(path[len("/api/jobs/") : -len("/cancel")])
                try:
                    job = STATE.jobs.cancel(job_id)
                except KeyError:
                    return send_json(self, {"error": "Unknown job."}, status=404)
                return send_json(self, {"job": job.to_dict(), "message": f"Cancel requested for {job_id}."})

            if path == "/api/jobs/generate":
                transcript = Path(str(payload.get("transcript_path", "")))
                provider = str(payload.get("provider", "ollama")).strip().lower()
                if provider not in {"ollama", "openai"}:
                    return send_json(self, {"error": "provider must be ollama or openai."}, status=400)
                if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
                    return send_json(self, {"error": "OPENAI_API_KEY is not set in this environment."}, status=400)
                if not transcript.exists():
                    return send_json(self, {"error": f"Transcript not found: {transcript}"}, status=400)
                episode_name = str(payload.get("episode_name") or transcript.stem)
                slug = str(payload.get("slug") or episode_slug(episode_name))
                anki_deck = str(payload.get("anki_deck") or f"Ukrainian::{episode_name}")
                anki = anki_status()
                if not anki.get("ok"):
                    return send_json(
                        self,
                        {
                            "error": "AnkiConnect is not reachable. Open Anki with the AnkiConnect add-on before generating.",
                            "anki": anki,
                        },
                        status=400,
                    )
                job_id = uuid.uuid4().hex[:10]
                log_path = LOGS_DIR / f"dashboard_generate_{slug}_{job_id}.log"
                synthesize_examples = bool(payload.get("synthesize_examples", provider == "openai"))
                command = build_generate_command(
                    transcript_path=transcript,
                    episode_name=episode_name,
                    slug=slug,
                    provider=provider,
                    log_path=log_path,
                    synthesize_examples=synthesize_examples,
                )
                job = JobRecord(
                    job_id=job_id,
                    kind="generate",
                    label=f"{episode_name} ({provider})",
                    command=command,
                    log_path=str(log_path),
                    episode_key=slug,
                    output_dir=str(output_dir_for(slug)),
                )
                STATE.jobs.enqueue(job)
                return send_json(self, {"job": job.to_dict(), "anki_deck": anki_deck})

            if path == "/api/review/load":
                slug = str(payload.get("slug", "")).strip()
                if not slug:
                    return send_json(self, {"error": "Provide slug."}, status=400)
                if payload.get("anki_deck"):
                    STATE.review_anki_deck = str(payload["anki_deck"])
                result = load_review_for_episode(slug)
                if payload.get("anki_deck"):
                    result["anki_deck"] = STATE.review_anki_deck
                return send_json(self, result)

            if path == "/api/review/validate":
                item = item_from_dict(payload)
                issues = [
                    {"severity": issue.severity, "code": issue.code, "message": issue.message}
                    for issue in validate_cards([item.card])
                    if issue.severity in {"error", "warning"}
                ]
                return send_json(self, {"issues": issues})

            if path in {"/api/review/save", "/api/review/export", "/api/review/push-anki"}:
                cards = payload.get("cards", [])
                if not isinstance(cards, list):
                    return send_json(self, {"error": "Expected cards list."}, status=400)
                with STATE.review_lock:
                    STATE.review_items = [item_from_dict(card) for card in cards if isinstance(card, dict)]
                    if payload.get("anki_deck"):
                        STATE.review_anki_deck = str(payload["anki_deck"])
                    count = write_manual_review(STATE.review_items, STATE.review_session_output)
                    if path == "/api/review/save":
                        return send_json(
                            self,
                            {"message": f"Saved {count} decisions to {STATE.review_session_output.name}."},
                        )
                    exported = export_accepted_cards(
                        STATE.review_items,
                        STATE.review_export_output,
                        STATE.review_card_format,
                    )
                    if path == "/api/review/export":
                        return send_json(
                            self,
                            {
                                "message": f"Exported {exported} accepted rows to {STATE.review_export_output}."
                            },
                        )
                    if not STATE.review_anki_deck:
                        return send_json(self, {"error": "Set an Anki deck before pushing."}, status=400)
                    words_path = suffixed_output_path(STATE.review_export_output, "words")
                    sentences_path = suffixed_output_path(STATE.review_export_output, "sentences")
                    if not words_path.exists():
                        return send_json(
                            self,
                            {"error": f"Word export missing after export: {words_path}"},
                            status=500,
                        )
                    result = push_approved_exports(
                        words_path=words_path,
                        sentences_path=sentences_path if sentences_path.exists() else None,
                        deck=STATE.review_anki_deck,
                        word_model=DEFAULT_WORD_MODEL,
                        sentence_model=DEFAULT_SENTENCE_MODEL,
                        url=STATE.review_anki_url,
                        update_existing=STATE.review_update_existing,
                    )
                    return send_json(
                        self,
                        {
                            "message": (
                                f"Anki push complete for {STATE.review_anki_deck}: "
                                f"added={result.added}, updated={result.updated}, skipped={result.skipped}."
                            )
                        },
                    )

            return send_json(self, {"error": "Not found."}, status=404)
        except Exception as exc:  # noqa: BLE001
            return send_json(self, {"error": str(exc)}, status=500)


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Load KEY=VALUE pairs into os.environ without overriding existing vars."""
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the ULP pipeline dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv()
    args = build_arg_parser().parse_args(argv)
    if not (STATIC_DIR / "index.html").exists():
        raise SystemExit(f"Missing dashboard UI files under {STATIC_DIR}")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"ULP pipeline dashboard running at {url}")
    print(
        "OpenAI key: "
        + ("set" if os.environ.get("OPENAI_API_KEY") else "not set (add OPENAI_API_KEY to .env or the shell)")
    )
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
