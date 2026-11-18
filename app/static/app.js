/* DirMover 前端：原生 JS，无框架 */
"use strict";

const PRESETS = [
  { label: "每 5 分钟", value: "*/5 * * * *" },
  { label: "每 15 分钟", value: "*/15 * * * *" },
  { label: "每 30 分钟", value: "*/30 * * * *" },
  { label: "每小时整点", value: "0 * * * *" },
  { label: "每 6 小时", value: "0 */6 * * *" },
  { label: "每天 00:00", value: "0 0 * * *" },
  { label: "每天 02:00", value: "0 2 * * *" },
  { label: "每周一 02:00", value: "0 2 * * 1" },
  { label: "每月 1 日 02:00", value: "0 2 1 * *" },
];

const CONFLICT_HINTS = {
  skip: "目标目录已有同名内容时跳过，原内容保留在源目录，并记录警告。",
  overwrite: "目标目录已有同名内容时先删除再移动，操作不可恢复，请谨慎使用。",
  rename: "目标目录已有同名内容时自动改名，如 a.txt → a_1.txt。",
};

const state = {
  tasks: [],
  editing: null,
  busy: new Set(),
  loading: false,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {
    data = null;
  }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) {
      detail = detail.map((item) => item.msg || JSON.stringify(item)).join("；");
    }
    throw new Error(detail || `请求失败（HTTP ${res.status}）`);
  }
  return data;
}

/* ---------------- 时间格式化 ---------------- */

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function relTime(iso) {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return fmtDate(iso);
}

function nextRunText(task) {
  if (!task.enabled) return "已暂停";
  if (!task.next_run) return "等待调度";
  const diff = (new Date(task.next_run).getTime() - Date.now()) / 1000;
  if (diff <= 0) return "即将执行";
  if (diff < 3600) return `约 ${Math.max(1, Math.round(diff / 60))} 分钟后`;
  if (diff < 86400) return `${Math.round(diff / 3600)} 小时后`;
  return fmtDate(task.next_run);
}

function isToday(iso) {
  const d = new Date(iso);
  const now = new Date();
  return d.toDateString() === now.toDateString();
}

/* ---------------- 列表渲染 ---------------- */

function cardHtml(task) {
  const busy = state.busy.has(task.id);
  const last = task.last_run;
  let lastHtml = '<span class="muted">尚未执行</span>';
  if (last) {
    const badge = last.ok
      ? '<span class="badge ok">成功</span>'
      : '<span class="badge bad">失败</span>';
    const counts = `移动 ${last.moved_entries} 项` + (last.skipped ? ` · 跳过 ${last.skipped}` : "");
    lastHtml = `${badge}<span title="${escapeHtml(fmtDate(last.at))}">${relTime(last.at)} · ${counts}</span>`;
  }

  return `
    <article class="card ${task.enabled ? "" : "disabled"}" data-id="${task.id}">
      <div class="card-main">
        <div class="card-head">
          <span class="dot ${task.enabled ? "on" : "off"}"></span>
          <h3 title="${escapeHtml(task.name)}">${escapeHtml(task.name)}</h3>
          <span class="pill">${escapeHtml(task.cron)}</span>
          <span class="pill ${task.enabled ? "ok" : ""}">${task.enabled ? "已启用" : "已禁用"}</span>
        </div>
        <div class="paths" title="${escapeHtml(task.source)} → ${escapeHtml(task.target)}">
          <span class="path">${escapeHtml(task.source)}</span>
          <span class="arrow">→</span>
          <span class="path dest">${escapeHtml(task.target)}</span>
        </div>
        <div class="meta">
          <span>上次执行：${lastHtml}</span>
          <span>下次执行：${nextRunText(task)}</span>
        </div>
      </div>
      <div class="card-side">
        <label class="switch" title="${task.enabled ? "点击禁用" : "点击启用"}">
          <input type="checkbox" data-action="toggle" ${task.enabled ? "checked" : ""} ${busy ? "disabled" : ""} aria-label="启用任务" />
          <span class="track"></span>
        </label>
        <button class="btn small primary" data-action="run" ${busy ? "disabled" : ""}>${busy ? "执行中…" : "立即执行"}</button>
        <button class="btn small ghost" data-action="dry" ${busy ? "disabled" : ""}>试运行</button>
        <button class="btn small ghost" data-action="history">记录</button>
        <button class="btn small ghost" data-action="edit">编辑</button>
        <button class="btn small danger" data-action="delete">删除</button>
      </div>
    </article>`;
}

function render() {
  const overview = $("#overview");
  const total = state.tasks.length;
  if (total) {
    const enabled = state.tasks.filter((t) => t.enabled).length;
    const today = state.tasks.filter((t) => t.last_run && isToday(t.last_run.at)).length;
    const failed = state.tasks.filter((t) => t.last_run && !t.last_run.ok).length;
    overview.innerHTML = `
      <div class="stat">任务 <b>${total}</b></div>
      <div class="stat">已启用 <b>${enabled}</b></div>
      <div class="stat">今日执行 <b>${today}</b></div>
      ${failed ? `<div class="stat">最近失败 <b>${failed}</b></div>` : ""}`;
  } else {
    overview.innerHTML = "";
  }

  const list = $("#taskList");
  if (!total) {
    list.innerHTML = `
      <div class="empty">
        <h2>还没有迁移任务</h2>
        <p>创建第一个任务，把源目录里的内容按 cron 计划搬到目标目录。</p>
        <button class="btn primary" data-action="add">＋ 新增任务</button>
      </div>`;
    return;
  }
  list.innerHTML = state.tasks.map(cardHtml).join("");
}

async function loadTasks(quiet = false) {
  if (state.loading) return;
  state.loading = true;
  try {
    const data = await api("/api/tasks");
    state.tasks = data.tasks || [];
    render();
  } catch (err) {
    if (!quiet) toast(err.message, "error");
  } finally {
    state.loading = false;
  }
}

/* ---------------- 操作 ---------------- */

async function runTask(id, dryRun) {
  const task = state.tasks.find((t) => t.id === id);
  state.busy.add(id);
  render();
  try {
    const data = await api(`/api/tasks/${id}/run`, {
      method: "POST",
      body: JSON.stringify({ dry_run: dryRun }),
    });
    const run = data.run;
    await loadTasks(true);

    if (run.error) {
      toast(`执行失败：${run.error}`, "error");
      openHistory(id);
      return;
    }
    if (dryRun) {
      toast(`试运行完成：将移动 ${run.moved_entries} 项`, run.failures.length ? "warn" : "ok");
      openHistory(id);
      return;
    }
    if (run.ok) {
      const extra = run.skipped ? `，跳过 ${run.skipped}` : "";
      toast(`「${task ? task.name : "任务"}」完成：移动 ${run.moved_entries} 项${extra}`, "ok");
    } else {
      toast(`「${task ? task.name : "任务"}」有 ${run.failures.length} 项失败`, "error");
      openHistory(id);
    }
  } catch (err) {
    toast(err.message, "error");
  } finally {
    state.busy.delete(id);
    render();
  }
}

async function toggleTask(id, enabled) {
  state.busy.add(id);
  render();
  try {
    await api(`/api/tasks/${id}/toggle`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
    toast(enabled ? "任务已启用" : "任务已禁用", "ok");
    await loadTasks(true);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    state.busy.delete(id);
    render();
  }
}

async function deleteTask(id) {
  const task = state.tasks.find((t) => t.id === id);
  if (!task) return;
  if (!window.confirm(`确定删除任务「${task.name}」？\n执行记录会一并删除，已迁移的文件不受影响。`)) return;
  state.busy.add(id);
  render();
  try {
    await api(`/api/tasks/${id}`, { method: "DELETE" });
    toast("任务已删除", "ok");
    await loadTasks(true);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    state.busy.delete(id);
    render();
  }
}

/* ---------------- 新增 / 编辑弹窗 ---------------- */

const taskModal = $("#taskModal");
const historyModal = $("#historyModal");
const form = $("#taskForm");
const presetSelect = $("#presetSelect");

function openModal(modal) {
  modal.hidden = false;
  const focusable = modal.querySelector("input, select, button");
  if (focusable) setTimeout(() => focusable.focus(), 40);
}

function closeModal(modal) {
  modal.hidden = true;
}

function updateConflictHint() {
  const value = form.elements.conflict.value;
  $("#conflictHint").textContent = CONFLICT_HINTS[value] || "";
}

function syncPresetFromCron() {
  const cron = form.elements.cron.value.trim();
  presetSelect.value = PRESETS.some((p) => p.value === cron) ? cron : "";
}

function openCreate() {
  state.editing = null;
  $("#modalTitle").textContent = "新增任务";
  form.reset();
  form.elements.cron.value = "*/5 * * * *";
  form.elements.conflict.value = "skip";
  form.elements.enabled.checked = true;
  presetSelect.value = "*/5 * * * *";
  updateConflictHint();
  openModal(taskModal);
}

function openEdit(id) {
  const task = state.tasks.find((t) => t.id === id);
  if (!task) return;
  state.editing = id;
  $("#modalTitle").textContent = "编辑任务";
  form.elements.name.value = task.name;
  form.elements.source.value = task.source;
  form.elements.target.value = task.target;
  form.elements.cron.value = task.cron;
  form.elements.conflict.value = task.conflict;
  form.elements.enabled.checked = task.enabled;
  syncPresetFromCron();
  updateConflictHint();
  openModal(taskModal);
}

async function submitForm(event) {
  event.preventDefault();
  if (!form.reportValidity()) return;

  const payload = {
    name: form.elements.name.value.trim(),
    source: form.elements.source.value.trim(),
    target: form.elements.target.value.trim(),
    cron: form.elements.cron.value.trim(),
    conflict: form.elements.conflict.value,
    enabled: form.elements.enabled.checked,
  };
  if (!payload.name || !payload.source || !payload.target || !payload.cron) {
    toast("请把表单填写完整", "warn");
    return;
  }

  const saveBtn = $("#saveBtn");
  saveBtn.disabled = true;
  saveBtn.textContent = "保存中…";
  try {
    if (state.editing) {
      await api(`/api/tasks/${state.editing}`, { method: "PUT", body: JSON.stringify(payload) });
      toast("任务已更新，调度已生效", "ok");
    } else {
      await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
      toast("任务已创建", "ok");
    }
    closeModal(taskModal);
    await loadTasks(true);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = "保存";
  }
}

/* ---------------- 执行记录 ---------------- */

function runHtml(run) {
  const status = run.dry_run
    ? '<span class="badge warn">试运行</span>'
    : run.ok
      ? '<span class="badge ok">成功</span>'
      : '<span class="badge bad">失败</span>';
  const trigger = run.trigger === "schedule" ? "定时" : "手动";
  const parts = [`移动 ${run.moved_entries} 项`];
  if (run.files || run.dirs) parts.push(`${run.files} 文件 / ${run.dirs} 目录`);
  if (run.skipped) parts.push(`跳过 ${run.skipped}`);
  if (run.overwritten) parts.push(`覆盖 ${run.overwritten}`);
  if (run.renamed) parts.push(`重命名 ${run.renamed}`);
  if (run.duration_ms != null) parts.push(`${run.duration_ms} ms`);

  const failures = (run.failures || []).length
    ? `<ul>${run.failures.map((f) => `<li>失败：<code>${escapeHtml(f.name)}</code> — ${escapeHtml(f.error)}</li>`).join("")}</ul>`
    : "";
  const warnings = (run.warnings || []).length
    ? `<details><summary>跳过 / 警告 ${run.warnings.length} 条</summary><ul>${run.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul></details>`
    : "";
  const items = run.dry_run && (run.items || []).length
    ? `<details><summary>将移动的条目 ${run.items.length}${run.items_truncated ? "+" : ""}</summary><ul>${run.items
        .map((item) => `<li>${escapeHtml(item.type)} <code>${escapeHtml(item.name)}</code>${item.action !== "move" ? `（${item.action === "rename" ? "重命名为 " : "覆盖 "}${escapeHtml(item.target_name)}）` : ""}</li>`)
        .join("")}</ul></details>`
    : "";
  const error = run.error ? `<div class="err">${escapeHtml(run.error)}</div>` : "";

  return `
    <div class="run">
      <div class="run-head">
        ${status}
        <span class="time">${escapeHtml(fmtDate(run.at))}</span>
        <span class="muted">${trigger} · ${run.dry_run ? "试运行" : "实际执行"}</span>
      </div>
      <div class="run-detail">${parts.join(" · ")}</div>
      ${error}${failures}${warnings}${items}
    </div>`;
}

async function openHistory(id) {
  const task = state.tasks.find((t) => t.id === id);
  $("#historyTitle").textContent = task ? `执行记录 · ${task.name}` : "执行记录";
  const body = $("#historyBody");
  body.innerHTML = '<p class="hint">加载中…</p>';
  openModal(historyModal);
  try {
    const data = await api(`/api/tasks/${id}/runs?limit=20`);
    body.innerHTML = data.runs.length
      ? data.runs.map(runHtml).join("")
      : '<p class="hint">还没有执行记录。</p>';
  } catch (err) {
    body.innerHTML = `<p class="err">${escapeHtml(err.message)}</p>`;
  }
}

/* ---------------- Toast ---------------- */

function toast(message, type = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity .2s";
    setTimeout(() => el.remove(), 220);
  }, 3600);
}

/* ---------------- 事件绑定 ---------------- */

function init() {
  // 常用调度预设
  PRESETS.forEach((preset) => {
    const option = document.createElement("option");
    option.value = preset.value;
    option.textContent = preset.label;
    presetSelect.appendChild(option);
  });

  presetSelect.addEventListener("change", () => {
    if (presetSelect.value) form.elements.cron.value = presetSelect.value;
  });
  form.elements.cron.addEventListener("input", syncPresetFromCron);
  form.elements.conflict.addEventListener("change", updateConflictHint);
  form.addEventListener("submit", submitForm);

  $("#addBtn").addEventListener("click", openCreate);
  $("#refreshBtn").addEventListener("click", () => loadTasks());

  // 卡片按钮（事件委托）
  $("#taskList").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "add") {
      openCreate();
      return;
    }
    const card = btn.closest(".card");
    if (!card) return;
    const id = card.dataset.id;

    if (action === "run") runTask(id, false);
    else if (action === "dry") runTask(id, true);
    else if (action === "history") openHistory(id);
    else if (action === "edit") openEdit(id);
    else if (action === "delete") deleteTask(id);
  });

  $("#taskList").addEventListener("change", (event) => {
    const input = event.target.closest('input[data-action="toggle"]');
    if (!input) return;
    toggleTask(input.closest(".card").dataset.id, input.checked);
  });

  // 弹窗关闭：按钮、遮罩、Esc
  $$("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => closeModal(btn.closest(".modal-mask")));
  });
  $$(".modal-mask").forEach((mask) => {
    mask.addEventListener("click", (event) => {
      if (event.target === mask) closeModal(mask);
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const open = $$(".modal-mask").find((mask) => !mask.hidden);
    if (open) closeModal(open);
  });

  loadTasks();
  // 每 30 秒静默刷新状态（有弹窗或执行中时跳过）
  setInterval(() => {
    if (document.hidden || state.busy.size) return;
    if ($$(".modal-mask").some((mask) => !mask.hidden)) return;
    loadTasks(true);
  }, 30000);
}

document.addEventListener("DOMContentLoaded", init);
