// Plain JS client: EventSource for SSE, textContent only (no innerHTML), buttons for approval and cancel.
"use strict";
const $ = (id) => document.getElementById(id);
const state = { session: null, run: null, source: null, timing: [] };
window.agentTiming = state.timing; // read by the Playwright test

function line(cls, text) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  $("log").appendChild(div);
  return div;
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  if (!r.ok) throw new Error("request failed: " + r.status);
  return r.json();
}

function stream(runId) {
  state.run = runId;
  $("cancel").disabled = false;
  $("send").disabled = true;
  const t0 = performance.now();
  const rec = { run: runId, ttft_ms: null, total_ms: null, status: "streaming" };
  state.timing.push(rec);
  const out = line("agent", "");
  const es = new EventSource(`/api/runs/${runId}/stream`);
  state.source = es;
  const finish = (status) => {
    rec.total_ms = performance.now() - t0;
    rec.status = status;
    es.close();
    state.source = null;
    state.run = null;
    $("cancel").disabled = true;
    $("send").disabled = false;
    $("status").textContent = status;
  };
  es.addEventListener("token", (e) => {
    if (rec.ttft_ms === null) rec.ttft_ms = performance.now() - t0;
    out.textContent = (out.textContent + JSON.parse(e.data).text).trimStart();
  });
  es.addEventListener("tool_call", (e) => line("tool", "tool call: " + e.data));
  es.addEventListener("tool_result", (e) => line("tool", "tool result: " + e.data));
  es.addEventListener("guard", (e) => line("guard", "guard: " + JSON.parse(e.data).rule));
  es.addEventListener("approval_required", (e) => {
    const d = JSON.parse(e.data);
    $("approval-text").textContent = "Approval needed: " + d.summary;
    $("approval").hidden = false;
  });
  es.addEventListener("done", () => finish("done"));
  es.addEventListener("cancelled", () => { line("guard", "stopped by user"); finish("cancelled"); });
  es.onerror = () => { if (state.source === es) finish("error"); };
}

async function ensureSession() {
  if (!state.session) state.session = (await post("/api/session")).session_id;
  return state.session;
}

$("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = $("msg").value.trim();
  if (!text || state.run) return;
  $("msg").value = "";
  line("user", "you: " + text);
  const sid = await ensureSession();
  stream((await post("/api/chat", { session_id: sid, message: text })).run_id);
});

async function decide(approve) {
  $("approval").hidden = true;
  line("user", approve ? "you pressed Approve" : "you pressed Reject");
  stream((await post("/api/decision", { session_id: state.session, approve })).run_id);
}
$("approve").addEventListener("click", () => decide(true));
$("reject").addEventListener("click", () => decide(false));
$("cancel").addEventListener("click", async () => { if (state.run) await post(`/api/runs/${state.run}/cancel`); });
