"use strict";

const labels = { running: "진행 중", waiting: "확인 대기", completed: "응답 완료", interrupted: "중단됨", error: "오류", stale: "기록 지연", idle: "기록 없음" };
const colors = { running: "green", waiting: "amber", completed: "blue", interrupted: "gray", error: "red", stale: "amber", idle: "gray" };
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
let snapshot = null;
let filter = "all";
let selectedId = null;
let connected = false;
let lastReceived = 0;
let receivedMonotonic = 0;
let serverTime = Date.now() / 1000;
let fallbackTimer = null;
let source = null;

function now() { return serverTime + (performance.now() - receivedMonotonic) / 1000; }
function relative(at) {
  if (!at) return "기록 없음";
  const seconds = Math.max(0, now() - at);
  if (seconds < 5) return "방금 전";
  if (seconds < 60) return `${Math.floor(seconds)}초 전`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`;
  return `${Math.floor(seconds / 86400)}일 전`;
}
function dateTime(at) { return at ? new Date(at * 1000).toLocaleString("ko-KR", { hour12: false }) : "기록 없음"; }
function clock(at) { return new Date(at * 1000).toLocaleTimeString("ko-KR", { hour12: false }); }
function number(value) { return value == null ? "—" : value.toLocaleString("ko-KR"); }
function statusButton(chat) {
  return `<button class="status-button ${esc(chat.state)}" data-chat="${esc(chat.id)}" aria-label="${esc(chat.title)}: ${esc(labels[chat.state])}, 상세 보기"><span class="dot"></span>${esc(labels[chat.state] || chat.state)}</button>`;
}
function baseChats() {
  return (snapshot?.chats || []).filter((chat) => ($("show-agents").checked || !chat.parent_id) && ($("show-archived").checked || !chat.archived));
}
function setFilter(state) {
  filter = state;
  document.querySelectorAll("[data-state]").forEach((button) => {
    const active = button.dataset.state === state;
    button.classList.toggle("selected", active);
    button.setAttribute("aria-pressed", String(active));
  });
  renderChats();
}
function renderChats() {
  if (!snapshot) return;
  const chats = baseChats();
  $("count-all").textContent = chats.length;
  for (const state of ["running", "waiting", "completed"]) $("count-" + state).textContent = chats.filter((chat) => chat.state === state).length;
  const query = $("search").value.trim().toLocaleLowerCase();
  const rows = chats.filter((chat) => {
    const stateMatch = filter === "all" || chat.state === filter || (filter === "attention" && ["stale", "error", "interrupted"].includes(chat.state));
    return stateMatch && [chat.title, chat.project, chat.cwd, chat.model].some((v) => String(v).toLocaleLowerCase().includes(query));
  });
  const rank = { waiting: 0, running: 1, error: 2, stale: 3, interrupted: 4, completed: 5, idle: 6 };
  const sort = $("sort").value;
  rows.sort((a, b) => sort === "title" ? a.title.localeCompare(b.title, "ko") : sort === "status" ? rank[a.state] - rank[b.state] || b.last_at - a.last_at : b.last_at - a.last_at);
  $("visible-count").textContent = rows.length;
  const html = rows.map((chat) => `<tr><td><button class="chat-title-button" data-chat="${esc(chat.id)}" title="${esc(chat.title)}">${esc(chat.title)}</button><div class="chat-project" title="${esc(chat.cwd)}"><span class="project-icon" aria-hidden="true">▱</span><span>${esc(chat.project)}${chat.parent_id ? " · 하위 에이전트" : ""}${chat.archived ? " · 보관됨" : ""}</span></div></td><td>${statusButton(chat)}</td><td><span class="model-label" title="${esc(chat.model)}">${esc(chat.model)}</span><span class="origin-label">${esc(chat.originator)}</span></td><td class="token-count" title="${chat.total_tokens == null ? "누적 토큰 기록이 없습니다" : "누적 입력 + 출력 · 캐시 입력 중복 합산 없음"}">${number(chat.total_tokens)}</td><td class="activity-time" data-time="${chat.last_at}" title="${esc(dateTime(chat.last_at))}">${relative(chat.last_at)}</td></tr>`).join("");
  // Keep focus and scroll stable when another chat's snapshot changes.
  if ($("chat-list").innerHTML !== html) {
    const focused = document.activeElement;
    const focusId = focused?.dataset.chat;
    const wasStatus = focused?.classList.contains("status-button");
    $("chat-list").innerHTML = html;
    if (focusId) Array.from($("chat-list").querySelectorAll("[data-chat]")).find((button) => button.dataset.chat === focusId && button.classList.contains("status-button") === wasStatus)?.focus({ preventScroll: true });
  }
  $("empty").hidden = rows.length !== 0;
  $("loading").hidden = true;
  $("empty-description").textContent = chats.length ? "검색어나 상태 필터를 바꿔 보세요." : "Codex에서 채팅을 시작하면 로컬 기록이 여기에 나타납니다.";
  $("table-note").textContent = `${rows.length}개 표시 / ${chats.length}개 채팅 · 상태 기록 기준`;
  $("file-count").textContent = `${snapshot.source.files}개 세션 관측`;
}
function renderQuota() {
  const quotas = snapshot.quota || [];
  const fresh = connected ? quotas.filter((q) => !q.stale && !q.expired) : [];
  const fast = fresh.some((q) => q.fast);
  const normal = fresh.some((q) => q.state === "normal");
  const signal = $("quota-signal");
  signal.className = `signal-button ${fast ? "red" : normal ? "green" : "gray"}`;
  $("signal-title").textContent = !connected ? "로컬 연결 끊김" : fast ? "소모 속도가 빨라요" : normal ? "사용량 신호 정상" : fresh.length ? "소모 속도 관측 중" : quotas.length ? "새 기록을 기다립니다" : "할당량 기록 없음";
  $("signal-caption").textContent = fast ? "최근 소모 속도가 설정 기준을 넘었습니다" : normal ? "현재 빠른 소모 신호가 없습니다" : fresh.length ? "속도를 판단할 기록을 모으고 있습니다" : "마지막 관측값은 아래에서 확인하세요";
  $("header-signal").innerHTML = `<span class="dot ${fast ? "red glow" : normal ? "green glow" : "gray"}"></span><span>${fast ? "빠른 소모" : normal ? "사용량 정상" : "사용량 관측 중"}</span>`;
  $("signal-explanation").textContent = `불빛은 이 페이지 안에서만 표시됩니다. 빨강은 빠른 소모, 초록은 정상 범위, 회색은 관측 중이거나 오래된 기록입니다. 빨강 진입 기준은 ${Math.round(snapshot.source.fast_seconds_per_percent / 60 * 10) / 10}분에 1%이며, ${Math.round(snapshot.source.release_seconds_per_percent / 60 * 10) / 10}분에 1% 이하로 느려지면 해제됩니다.`;
  $("quota-list").innerHTML = quotas.length ? quotas.map((q) => {
    const window = q.minutes >= 1440 ? `${Math.round(q.minutes / 1440)}일 한도` : `${q.minutes / 60}시간 한도`;
    const state = q.expired ? "갱신 시각 지남" : q.stale ? "오래된 기록" : q.fast ? "빠른 소모" : "최근 관측";
    return `<div class="quota-item${q.fast && !q.stale ? " fast" : ""}"><div class="quota-row"><span>${esc(window)}${q.limit_id === "codex" ? "" : ` · ${esc(q.limit_id)}`}</span><span class="quota-value"><strong>${number(q.remaining)}%</strong> 남음</span></div><progress max="100" value="${q.remaining}" aria-label="${esc(window)} 잔여량 ${q.remaining}%"></progress><div class="quota-sub"><span>${esc(state)} · <span data-time="${q.observed_at}">${relative(q.observed_at)}</span></span><span title="${esc(dateTime(q.resets_at))}">갱신 ${esc(new Date(q.resets_at * 1000).toLocaleDateString("ko-KR", { month: "short", day: "numeric" }))}</span></div></div>`;
  }).join("") : '<p class="muted-empty">Codex가 할당량을 기록하면<br>남은 비율이 표시됩니다.</p>';
}
function renderAttribution() {
  const usage = snapshot.attribution;
  $("attribution-list").innerHTML = usage.rows.length ? usage.rows.map((row) => `<div class="contributor"><div class="contributor-row"><span class="contributor-name" title="${esc(row.title)}">${esc(row.title)}</span><span class="contributor-value">${row.percent}%</span></div><progress max="100" value="${row.percent}" aria-label="${esc(row.title)} 최근 사용 비중 ${row.percent}%"></progress><span class="contributor-model">${esc(row.models)}</span></div>`).join("") : '<p class="muted-empty">최근 5분간 관측된 사용 기록이 없습니다.</p>';
  $("attribution-note").textContent = `${usage.basis === "tokens" ? "토큰 활동" : "표준 크레딧 요율 추정"} 기준 · 실제 할당량 배분과 다릅니다.${usage.other_percent ? ` 기타 채팅 ${usage.other_percent}%.` : ""} 속도 모드·다른 기기 사용은 미반영.${usage.partial ? " 일부 기록만 집계되었습니다." : ""}`;
}
function renderAttention() {
  $("attention-list").innerHTML = snapshot.attention.length ? snapshot.attention.map((row) => `<div class="attention-row"><span class="dot ${row.color === "red" ? "red" : "blue"}"></span><div class="attention-content"><strong title="${esc(row.title)}">${esc(row.title)}</strong><p>${esc(row.message)}</p></div><time data-time="${row.at}" title="${esc(dateTime(row.at))}">${relative(row.at)}</time></div>`).join("") : '<p class="muted-empty">아직 알림 기록이 없습니다.</p>';
}
function updateDetail() {
  const chat = snapshot?.chats.find((c) => c.id === selectedId);
  if (!chat) return;
  $("detail-title").textContent = chat.title;
  const fields = [["프로젝트", chat.cwd || "없음"], ["모델", chat.model], ["브랜치", chat.branch || "기록 없음"], ["작업 시작", dateTime(chat.started_at)], ["작업 종료", dateTime(chat.ended_at)], ["누적 토큰", number(chat.total_tokens)], ["입력 토큰", number(chat.input_tokens)], ["캐시 입력", number(chat.cached_input_tokens)], ["출력 토큰", number(chat.output_tokens)], ["채팅 ID", chat.id]];
  $("detail-body").innerHTML = `<div class="detail-status"><span class="dot ${colors[chat.state]}"></span><strong>${esc(labels[chat.state])}</strong><span>${esc(chat.activity)}</span></div><dl class="detail-fields">${fields.map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>${chat.partial ? '<p class="detail-note">시작 시 큰 세션은 끝부분만 읽습니다. 앞선 작업 기록·토큰 정보가 생략될 수 있습니다.</p>' : ""}<ul class="detail-events">${chat.events.map((event) => `<li><time>${clock(event.at)}</time><span>${esc(event.activity)}</span></li>`).join("")}</ul>`;
}
function setConnection(ok, label) {
  const changed = connected !== ok;
  connected = ok;
  $("connection").classList.toggle("offline", !ok);
  $("connection").innerHTML = `<span class="dot ${ok ? "green glow" : "amber"}"></span><span>${esc(label)}</span>`;
  if (changed && snapshot) renderQuota();
}
function received(data) {
  serverTime = data.checked_at || Date.now() / 1000;
  receivedMonotonic = performance.now();
  lastReceived = performance.now();
  setConnection(true, fallbackTimer ? "폴링으로 연결됨" : "실시간 연결됨");
}
function applySnapshot(data) {
  const unchanged = snapshot?.version === data.version;
  snapshot = data;
  received(data);
  if (unchanged) return;
  const warnings = [];
  if (!data.source.available) warnings.push("Codex 세션 폴더를 찾지 못했습니다. 실행할 때 --home 경로를 확인하세요.");
  if (data.source.warning) warnings.push(data.source.warning);
  if (data.error) warnings.push(data.error);
  if (!data.watcher.fresh || data.watcher.state !== "running") {
    $("source-description").textContent = "웹 자체 감시 중 · 외부 전송 없음";
  } else {
    $("source-description").textContent = data.watcher.edge_active ? "기존 화면 알림 감시기가 별도로 실행 중입니다" : "로컬 기록 감시 중 · 외부 전송 없음";
  }
  $("source-warning").hidden = warnings.length === 0;
  $("source-warning").textContent = warnings.join(" ");
  renderChats(); renderQuota(); renderAttribution(); renderAttention();
  if ($("chat-detail").open) updateDetail();
}
async function fetchSnapshot() {
  try {
    const response = await fetch("/api/snapshot", { cache: "no-store", signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error(String(response.status));
    applySnapshot(await response.json());
  } catch {
    setConnection(false, "연결 끊김 · 재시도 중");
  }
}
function startPolling() {
  if (!fallbackTimer) fallbackTimer = setInterval(fetchSnapshot, 5000);
}
function connect() {
  if (!("EventSource" in window)) { startPolling(); fetchSnapshot(); return; }
  source = new EventSource("/api/events");
  source.addEventListener("snapshot", (event) => {
    if (fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer = null; }
    applySnapshot(JSON.parse(event.data));
  });
  source.addEventListener("heartbeat", (event) => {
    if (fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer = null; }
    received(JSON.parse(event.data));
  });
  source.onerror = () => { setConnection(false, "다시 연결 중"); startPolling(); };
}

document.querySelectorAll("[data-state]").forEach((button) => button.addEventListener("click", () => setFilter(button.dataset.state)));
$("search").addEventListener("input", renderChats);
for (const id of ["sort", "show-agents", "show-archived"]) $(id).addEventListener("change", renderChats);
$("reset-filters").addEventListener("click", () => { $("search").value = ""; setFilter("all"); });
$("chat-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-chat]");
  if (!button) return;
  selectedId = button.dataset.chat;
  $("copy-feedback").textContent = "";
  updateDetail(); $("chat-detail").showModal();
});
$("close-detail").addEventListener("click", () => $("chat-detail").close());
$("chat-detail").addEventListener("click", (event) => { if (event.target === $("chat-detail")) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close(); } });
$("copy-id").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(selectedId); $("copy-feedback").textContent = "복사했습니다"; }
  catch { $("copy-feedback").textContent = "위 채팅 ID를 선택해 복사하세요"; }
});
$("quota-signal").addEventListener("click", () => {
  const expanded = $("quota-signal").getAttribute("aria-expanded") !== "true";
  $("quota-signal").setAttribute("aria-expanded", String(expanded));
  $("signal-explanation").hidden = !expanded;
});
$("header-signal").addEventListener("click", () => { $("quota-panel").scrollIntoView({ behavior: "smooth", block: "start" }); $("quota-signal").focus({ preventScroll: true }); });
setInterval(() => {
  $("today").textContent = new Date().toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "short" });
  if (snapshot) {
    document.querySelectorAll("[data-time]").forEach((element) => { element.textContent = relative(Number(element.dataset.time)); });
    $("last-checked").textContent = `마지막 연결 확인 ${Math.max(0, Math.floor((performance.now() - lastReceived) / 1000))}초 전`;
    if (connected && performance.now() - lastReceived > 25000) setConnection(false, "기록 연결 확인 중");
  }
}, 1000);
fetchSnapshot();
connect();
