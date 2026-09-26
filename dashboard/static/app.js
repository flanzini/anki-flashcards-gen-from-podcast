let episodes = [];
let episodeOffset = 0;
let episodeTotal = 0;
let episodeFeedTotal = 0;
let episodeHasMore = false;
const EPISODE_PAGE = 40;
let transcripts = [];
let reviewable = [];
let cards = [];
let selectedCard = 0;
let selectedJobId = null;
let logOffset = 0;
let validateTimer = null;

const $ = (id) => document.getElementById(id);

async function request(path, method = "GET", payload = null) {
  const options = { method, headers: { "Content-Type": "application/json" } };
  if (payload) options.body = JSON.stringify(payload);
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[c]);
}

function showBanner(message) {
  const node = $("banner");
  if (!message) {
    node.hidden = true;
    node.textContent = "";
    return;
  }
  node.hidden = false;
  node.textContent = message;
}

function setTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `panel-${name}`);
  });
}

async function refreshStatus() {
  const data = await request("/api/status");
  const anki = $("ankiBadge");
  if (data.anki && data.anki.ok) {
    anki.className = "badge ok";
    anki.textContent = "AnkiConnect: connected";
  } else {
    anki.className = "badge bad";
    anki.textContent = "AnkiConnect: not reachable — open Anki";
  }
  const openai = $("openaiBadge");
  openai.className = data.openai_key_set ? "badge ok" : "badge warn";
  openai.textContent = data.openai_key_set ? "OpenAI key: set" : "OpenAI key: not set";
}

function stagePill(stage) {
  const label = stage || "none";
  return `<span class="pill ${escapeHtml(label)}">${escapeHtml(label)}</span>`;
}

function currentEpisodeFilters() {
  return {
    search: $("episodeSearch").value.trim(),
    indexFrom: $("indexFrom").value.trim(),
    indexTo: $("indexTo").value.trim(),
  };
}

function describeEpisodeFilters(filters) {
  const parts = [];
  if (filters.search) parts.push(`title contains “${filters.search}”`);
  if (filters.indexFrom !== "" || filters.indexTo !== "") {
    const from = filters.indexFrom === "" ? "0" : filters.indexFrom;
    const to = filters.indexTo === "" ? "end" : filters.indexTo;
    parts.push(`RSS index ${from}–${to} (0 = newest)`);
  }
  return parts;
}

function setEpisodeStatus(message, kind = "") {
  const node = $("episodeStatus");
  node.textContent = message;
  node.className = `filter-status${kind ? ` ${kind}` : ""}`;
}

function episodeQuery(offset) {
  const params = new URLSearchParams();
  params.set("limit", String(EPISODE_PAGE));
  params.set("offset", String(offset));
  const filters = currentEpisodeFilters();
  if (filters.search) params.set("search", filters.search);
  if (filters.indexFrom !== "") params.set("index_from", filters.indexFrom);
  if (filters.indexTo !== "") params.set("index_to", filters.indexTo);
  return params.toString();
}

function renderEpisodes() {
  const filters = currentEpisodeFilters();
  const filterParts = describeEpisodeFilters(filters);
  if (!episodes.length) {
    $("episodeRows").innerHTML = `<tr><td colspan="7" style="padding:18px;color:var(--sub)">
      No episodes in this list.
    </td></tr>`;
  } else {
    $("episodeRows").innerHTML = episodes
      .map(
        (ep) => `<tr>
      <td><input type="checkbox" data-index="${ep.index}" ${ep.has_transcript ? "" : "checked"}></td>
      <td>${ep.index}</td>
      <td><strong>${escapeHtml(ep.short_name)}</strong><br><small>${escapeHtml(ep.title)}</small></td>
      <td>${escapeHtml(ep.published || "")}</td>
      <td>${ep.has_audio ? '<span class="pill cached">cached</span>' : "—"}</td>
      <td>${ep.has_transcript ? '<span class="pill transcript">yes</span>' : "missing"}</td>
      <td>${stagePill(ep.stage)}</td>
    </tr>`
      )
      .join("");
  }
  $("loadMoreEpisodes").hidden = !episodeHasMore;

  if (filterParts.length) {
    if (episodeTotal === 0) {
      setEpisodeStatus(
        `Search ran — 0 matches for ${filterParts.join(" and ")}. Try Clear filters, or a shorter title like 4-135.`,
        "empty"
      );
    } else {
      setEpisodeStatus(
        `Search ran — ${episodeTotal} match${episodeTotal === 1 ? "" : "es"} for ${filterParts.join(" and ")}. Showing ${episodes.length}${episodeHasMore ? " (more available)" : ""}.`,
        "active"
      );
    }
  } else {
    setEpisodeStatus(
      `Showing newest ${episodes.length} of ${episodeFeedTotal} episodes. Type a title (e.g. 4-135) and press Search.`,
      ""
    );
  }
}

async function refreshEpisodes(reset = true) {
  const button = $("refreshEpisodes");
  const filters = currentEpisodeFilters();
  const filterParts = describeEpisodeFilters(filters);
  button.disabled = true;
  button.textContent = filterParts.length ? "Searching…" : "Loading…";
  setEpisodeStatus(
    filterParts.length
      ? `Searching for ${filterParts.join(" and ")}…`
      : "Loading newest episodes…",
    "busy"
  );
  try {
    if (reset) {
      episodeOffset = 0;
      episodes = [];
    }
    const data = await request(`/api/episodes?${episodeQuery(episodeOffset)}`);
    if (!("search" in data) && filters.search) {
      setEpisodeStatus(
        "This dashboard server is outdated and ignored your search. Close the launcher window, restart Launch ULP Dashboard.bat, then hard-refresh (Ctrl+F5).",
        "error"
      );
      return;
    }
    const page = data.episodes || [];
    episodes = reset ? page : episodes.concat(page);
    episodeTotal = data.total || 0;
    episodeFeedTotal = data.feed_total || 0;
    episodeHasMore = Boolean(data.has_more);
    episodeOffset = (data.offset || 0) + page.length;
    renderEpisodes();
  } catch (error) {
    setEpisodeStatus(`Search failed: ${error.message}`, "error");
    throw error;
  } finally {
    button.disabled = false;
    button.textContent = "Search";
  }
}

async function loadMoreEpisodes() {
  setEpisodeStatus("Loading more episodes…", "busy");
  const data = await request(`/api/episodes?${episodeQuery(episodeOffset)}`);
  const page = data.episodes || [];
  episodes = episodes.concat(page);
  episodeTotal = data.total || 0;
  episodeFeedTotal = data.feed_total || 0;
  episodeHasMore = Boolean(data.has_more);
  episodeOffset = (data.offset || 0) + page.length;
  renderEpisodes();
}

async function clearEpisodeFilters() {
  $("episodeSearch").value = "";
  $("indexFrom").value = "";
  $("indexTo").value = "";
  await refreshEpisodes(true);
}

async function refreshTranscripts() {
  const data = await request("/api/transcripts");
  transcripts = data.transcripts || [];
  $("transcriptRows").innerHTML = transcripts
    .map((item, i) => {
      const openaiDisabled = "";
      return `<tr>
      <td><strong>${escapeHtml(item.short_name)}</strong><br><small>${escapeHtml(item.transcript_path)}</small></td>
      <td>${stagePill(item.stage)}</td>
      <td>
        <select id="provider-${i}">
          <option value="ollama">Local qwen3:4b</option>
          <option value="openai">OpenAI gpt-4o-mini</option>
        </select>
      </td>
      <td><input id="deck-${i}" type="text" value="${escapeHtml(item.default_deck)}"></td>
      <td><button type="button" data-generate="${i}">Generate</button>
          ${item.stage === "reviewed" || item.stage === "approved" ? `<button type="button" data-goto-review="${escapeHtml(item.slug)}">Review</button>` : ""}
      </td>
    </tr>`;
    })
    .join("");
}

async function refreshReviewable() {
  const data = await request("/api/reviewable");
  reviewable = data.episodes || [];
  $("reviewEpisode").innerHTML = reviewable
    .map((item) => `<option value="${escapeHtml(item.slug)}">${escapeHtml(item.short_name)} (${escapeHtml(item.stage)})</option>`)
    .join("");
  if (reviewable.length) {
    const current = reviewable.find((item) => item.slug === $("reviewEpisode").value) || reviewable[0];
    $("reviewDeck").value = current.default_deck || "";
  }
}

async function startTranscribe() {
  showBanner("");
  const indexes = [...document.querySelectorAll("#episodeRows input[type=checkbox]:checked")].map((node) =>
    Number(node.dataset.index)
  );
  if (!indexes.length) {
    showBanner("Select at least one episode to transcribe.");
    return;
  }
  const transcriber = $("transcribeEngine").value;
  if (transcriber === "openai") {
    await refreshStatus();
    if (!$("openaiBadge").classList.contains("ok")) {
      showBanner("OpenAI Whisper needs OPENAI_API_KEY in the environment that launched the dashboard.");
      return;
    }
    if (
      !window.confirm(
        `Transcribe ${indexes.length} episode(s) via OpenAI Whisper API?\nRough cost ~$0.12 per ~20 min episode.`
      )
    ) {
      return;
    }
  } else if (indexes.length > 1) {
    if (!window.confirm(`Queue ${indexes.length} local (slow) transcription jobs? You can Stop them from Jobs.`)) {
      return;
    }
  }
  const result = await request("/api/jobs/transcribe", "POST", {
    episode_indexes: indexes,
    transcriber,
  });
  await refreshJobs();
  if (result.jobs && result.jobs[0]) selectJob(result.jobs[0].job_id);
}

async function cancelJob(jobId) {
  if (!jobId) return;
  showBanner("");
  const result = await request(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, "POST", {});
  showBanner(result.message || "Cancel requested.");
  await refreshJobs();
  if (selectedJobId === jobId) await pollLog(false);
}

async function startGenerate(index) {
  showBanner("");
  await refreshStatus();
  const item = transcripts[index];
  const provider = $(`provider-${index}`).value;
  const anki_deck = $(`deck-${index}`).value.trim();
  try {
    const result = await request("/api/jobs/generate", "POST", {
      transcript_path: item.transcript_path,
      episode_name: item.short_name,
      slug: item.slug,
      provider,
      anki_deck,
    });
    await refreshJobs();
    if (result.job) selectJob(result.job.job_id);
  } catch (error) {
    showBanner(error.message);
  }
}

async function refreshJobs() {
  const data = await request("/api/jobs");
  const jobs = data.jobs || [];
  $("jobList").innerHTML = jobs
    .map((job) => {
      const canStop = job.status === "queued" || job.status === "running";
      return `<div class="job-row ${job.job_id === selectedJobId ? "selected" : ""}" data-job="${job.job_id}">
      <span class="pill ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
      <div><strong>${escapeHtml(job.kind)}</strong> — ${escapeHtml(job.label)}<br><small>${escapeHtml(job.log_path)}</small></div>
      <div class="actions">
        ${canStop ? `<button type="button" class="danger" data-cancel-job="${job.job_id}">Stop</button>` : ""}
        <small>${escapeHtml(job.episode_key || "")}</small>
      </div>
    </div>`;
    })
    .join("");
  if (selectedJobId) await pollLog(false);
}

async function selectJob(jobId) {
  selectedJobId = jobId;
  logOffset = 0;
  $("jobLog").textContent = "";
  await refreshJobs();
  await pollLog(true);
}

async function pollLog(reset) {
  if (!selectedJobId) return;
  if (reset) logOffset = 0;
  const data = await request(`/api/jobs/${encodeURIComponent(selectedJobId)}/log?offset=${logOffset}`);
  if (data.chunk) $("jobLog").textContent += data.chunk;
  logOffset = data.offset || logOffset;
  $("jobLog").scrollTop = $("jobLog").scrollHeight;
  if (data.job && (data.job.status === "queued" || data.job.status === "running")) {
    setTimeout(() => pollLog(false), 2000);
  } else if (data.job && data.job.status === "done" && data.job.kind === "generate") {
    await refreshTranscripts();
    await refreshReviewable();
  } else if (data.job && data.job.status === "done" && data.job.kind === "transcribe") {
    await refreshEpisodes(true);
    await refreshTranscripts();
  }
}

function updateReviewCounts() {
  const count = (decision) => cards.filter((card) => card.decision === decision).length;
  $("reviewCounts").textContent = `Accept: ${count("accept")}  Needs review: ${count("needs_review")}  Reject: ${count("reject")}`;
}

function renderReviewList() {
  const filter = $("reviewFilter").value;
  $("reviewList").innerHTML = cards
    .map((card, i) => {
      if (filter !== "all" && card.decision !== filter) return "";
      return `<div class="cardrow ${i === selectedCard ? "selected" : ""}" data-card="${i}">
        <span class="pill ${escapeHtml(card.decision)}">${escapeHtml(card.decision)}</span>
        <span>${escapeHtml(card.ukrainian)}<br><small>${escapeHtml(card.origin || "")}</small></span>
        <span>${escapeHtml(card.english)}</span>
      </div>`;
    })
    .join("");
  updateReviewCounts();
}

function saveReviewForm() {
  if (!cards.length) return;
  const card = cards[selectedCard];
  card.ukrainian = $("ukrainian").value.trim();
  card.english = $("english").value.trim();
  card.example = $("example").value.trim();
  card.example_target = $("target").value.trim();
  card.decision = $("decision").value;
  card.reason = $("reason").value.trim();
}

function selectCard(index) {
  saveReviewForm();
  selectedCard = index;
  const card = cards[selectedCard];
  $("ukrainian").value = card.ukrainian;
  $("english").value = card.english;
  $("example").value = card.example || "";
  $("target").value = card.example_target || "";
  $("decision").value = card.decision;
  $("reason").value = card.reason || "";
  $("origin").value = card.origin || "";
  renderReviewList();
  validateCurrent();
}

function nextVisibleCard() {
  const filter = $("reviewFilter").value;
  for (let i = selectedCard + 1; i < cards.length; i += 1) {
    if (filter === "all" || cards[i].decision === filter) {
      selectCard(i);
      return;
    }
  }
}

function decide(value) {
  $("decision").value = value;
  saveReviewForm();
  renderReviewList();
  nextVisibleCard();
}

async function validateCurrent() {
  saveReviewForm();
  if (!cards.length) return;
  const data = await request("/api/review/validate", "POST", cards[selectedCard]);
  const node = $("validation");
  if (data.issues.length) {
    node.classList.add("problem");
    node.textContent = data.issues.map((issue) => `${issue.severity.toUpperCase()}: ${issue.message}`).join("\n");
  } else {
    node.classList.remove("problem");
    node.textContent = cards[selectedCard].example_target
      ? "Sentence target passes deterministic validation."
      : "Word card only: no safe sentence target selected.";
  }
}

async function loadReview(slug) {
  showBanner("");
  const target = slug || $("reviewEpisode").value;
  if (!target) {
    showBanner("No reviewed episode available yet.");
    return;
  }
  const data = await request("/api/review/load", "POST", {
    slug: target,
    anki_deck: $("reviewDeck").value.trim(),
  });
  cards = data.cards || [];
  selectedCard = 0;
  $("reviewDeck").value = data.anki_deck || $("reviewDeck").value;
  $("reviewMessage").textContent = `Loaded ${cards.length} cards.`;
  renderReviewList();
  if (cards.length) selectCard(0);
  setTab("review");
}

async function persistReview(path) {
  saveReviewForm();
  if (path === "/api/review/export" || path === "/api/review/push-anki") {
    const pending = cards.filter((card) => card.decision === "needs_review").length;
    const action = path === "/api/review/push-anki" ? "push accepted cards to Anki" : "export accepted cards";
    if (pending && !window.confirm(`${pending} cards still need review. ${action} anyway?`)) return;
    if (path === "/api/review/push-anki" && !window.confirm("Push currently accepted cards into the configured Anki deck?")) {
      return;
    }
  }
  const result = await request(path, "POST", {
    cards,
    anki_deck: $("reviewDeck").value.trim(),
  });
  $("reviewMessage").textContent = result.message;
  setTimeout(() => {
    $("reviewMessage").textContent = "";
  }, 6000);
}

function wireEvents() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => setTab(tab.dataset.tab));
  });
  $("clearEpisodeFilters").addEventListener("click", () => clearEpisodeFilters().catch((error) => showBanner(error.message)));
  $("loadMoreEpisodes").addEventListener("click", () => loadMoreEpisodes().catch((error) => showBanner(error.message)));
  $("episodeFilterForm").addEventListener("submit", (event) => {
    event.preventDefault();
    refreshEpisodes(true).catch((error) => showBanner(error.message));
  });
  $("startTranscribe").addEventListener("click", () => startTranscribe().catch((error) => showBanner(error.message)));
  $("cancelSelectedJob").addEventListener("click", () => {
    if (!selectedJobId) {
      showBanner("Select a job in the list first.");
      return;
    }
    cancelJob(selectedJobId).catch((error) => showBanner(error.message));
  });
  $("refreshTranscripts").addEventListener("click", () => refreshTranscripts().catch((error) => showBanner(error.message)));
  $("refreshJobs").addEventListener("click", () => refreshJobs().catch((error) => showBanner(error.message)));
  $("jobList").addEventListener("click", (event) => {
    const stop = event.target.closest("[data-cancel-job]");
    if (stop) {
      event.stopPropagation();
      cancelJob(stop.dataset.cancelJob).catch((error) => showBanner(error.message));
      return;
    }
    const row = event.target.closest("[data-job]");
    if (row) selectJob(row.dataset.job).catch((error) => showBanner(error.message));
  });
  $("transcriptRows").addEventListener("click", (event) => {
    const generate = event.target.closest("[data-generate]");
    if (generate) {
      startGenerate(Number(generate.dataset.generate)).catch((error) => showBanner(error.message));
      return;
    }
    const go = event.target.closest("[data-goto-review]");
    if (go) {
      $("reviewEpisode").value = go.dataset.gotoReview;
      loadReview(go.dataset.gotoReview).catch((error) => showBanner(error.message));
    }
  });
  $("loadReview").addEventListener("click", () => loadReview().catch((error) => showBanner(error.message)));
  $("saveReview").addEventListener("click", () => persistReview("/api/review/save").catch((error) => showBanner(error.message)));
  $("exportReview").addEventListener("click", () => persistReview("/api/review/export").catch((error) => showBanner(error.message)));
  $("pushReview").addEventListener("click", () => persistReview("/api/review/push-anki").catch((error) => showBanner(error.message)));
  $("reviewFilter").addEventListener("change", renderReviewList);
  $("reviewEpisode").addEventListener("change", () => {
    const item = reviewable.find((row) => row.slug === $("reviewEpisode").value);
    if (item) $("reviewDeck").value = item.default_deck || "";
  });
  $("reviewList").addEventListener("click", (event) => {
    const row = event.target.closest("[data-card]");
    if (row) selectCard(Number(row.dataset.card));
  });
  $("acceptNext").addEventListener("click", () => decide("accept"));
  $("needsNext").addEventListener("click", () => decide("needs_review"));
  $("rejectNext").addEventListener("click", () => decide("reject"));
  ["ukrainian", "english", "example", "target", "decision"].forEach((id) => {
    $(id).addEventListener("input", () => {
      clearTimeout(validateTimer);
      validateTimer = setTimeout(() => validateCurrent().catch(() => {}), 250);
    });
  });
}

async function init() {
  wireEvents();
  await refreshStatus();
  await refreshEpisodes(true);
  await refreshTranscripts();
  await refreshReviewable();
  await refreshJobs();
  setInterval(() => {
    refreshStatus().catch(() => {});
    refreshJobs().catch(() => {});
  }, 8000);
}

init().catch((error) => showBanner(error.message));
