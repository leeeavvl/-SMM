const state = {
  platforms: [],
  content: [],
  assistantTools: [],
  assistantMode: "tools",
  assistantCategory: "all",
};

const STATUS_LABELS = {
  draft: "Черновик",
  publishing: "Публикуется",
  scheduled: "Запланировано",
  published: "Опубликовано",
  queued: "В очереди",
  failed: "Ошибка",
};

function statusLabel(s) {
  return STATUS_LABELS[s] || s;
}

function toast(msg, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "toast show" + (isError ? " error" : "");
  setTimeout(() => el.classList.remove("show"), 3000);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Ошибка запроса");
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---------- Navigation ----------
document.querySelectorAll(".nav-item, .nav-link[data-view]").forEach((btn) => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});

function switchView(view) {
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  if (view === "dashboard") loadDashboard();
  if (view === "assistant") loadAssistant();
  if (view === "content") loadContent();
  if (view === "posts") loadPosts();
  if (view === "analysis") loadAnalysis();
  if (view === "plan") loadPlan();
  if (view === "publish") loadPublishQueue();
  if (view === "platforms") loadPlatforms();
  if (view === "sales") loadSales();
  if (view === "settings") loadSettings();
}

// ---------- Dashboard ----------
async function loadDashboard() {
  const settings = await api("/api/settings");
  document.getElementById("hero-brand-name").textContent = settings.brand_name;
  document.getElementById("hero-desc").textContent = `${settings.brand_name} — ${settings.brand_description}`;

  const summary = await api("/api/analytics/summary");
  const cards = document.getElementById("dashboard-cards");
  cards.innerHTML = "";
  const total = summary.total_content;
  const published = summary.content_by_status.published || 0;
  const scheduled = (summary.content_by_status.scheduled || 0) + (summary.content_by_status.publishing || 0);
  const revenue = summary.sales_totals.revenue || 0;

  [
    ["Материалов всего", total],
    ["Опубликовано", published],
    ["В очереди / запланировано", scheduled],
    ["Выручка (всего)", revenue.toLocaleString("ru-RU") + " ₽"],
  ].forEach(([label, value]) => {
    cards.insertAdjacentHTML(
      "beforeend",
      `<div class="card"><div class="value">${value}</div><div class="label">${label}</div></div>`
    );
  });

  await fetchPlatforms();
  const checks = document.getElementById("hero-platform-checks");
  checks.innerHTML = "";
  state.platforms.forEach((p) => {
    checks.insertAdjacentHTML(
      "beforeend",
      `<label class="check-item"><input type="checkbox" value="${p.id}"> ${p.name}</label>`
    );
  });
}

function platformInitials(name) {
  return name.replace(/[^\p{L}\p{N}]/gu, "").slice(0, 2).toUpperCase();
}

function platformFileCard(p, stats) {
  stats = stats || {};
  const hasBrief = !!(p.brief || p.channel_url);
  return `<div class="file-card" data-platform="${p.id}">
    <div class="file-icon" style="background:${p.color}">${platformInitials(p.name)}</div>
    <div class="fname">${p.name}</div>
    <div class="fsub">опубл.: ${stats.published || 0} · в очереди: ${stats.queued || 0} · ошибок: ${stats.failed || 0}</div>
    <div class="file-badge-row">
      <span class="file-badge ${p.connected ? "connected" : ""}">${p.connected ? "Подключено" : "Не подключено"}</span>
      ${hasBrief ? `<span class="file-badge connected">ТЗ задано</span>` : ""}
    </div>
    <button class="btn small" data-brief-btn="${p.id}">📝 ТЗ и канал</button>
  </div>`;
}

// ---------- Content ----------
function renderContentCard(c, { fullText } = {}) {
  const platformBadges = c.platforms
    .map((cp) => `<span class="status-badge ${cp.status}">${cp.platform_id}: ${statusLabel(cp.status)}</span>`)
    .join(" ");
  const bodyHtml = fullText
    ? `<div class="cbody cbody-full">${escapeHtml(c.body)}</div>`
    : `<div class="cbody">${escapeHtml(c.body).slice(0, 160)}${c.body.length > 160 ? "…" : ""}</div>`;
  return `<div class="content-item">
      <div>
        <div class="ctitle">${escapeHtml(c.title)} <span class="status-badge ${c.status}">${statusLabel(c.status)}</span></div>
        ${bodyHtml}
        <div class="cmeta">${platformBadges}</div>
      </div>
      <div class="content-actions">
        <button class="btn small" onclick="openPublishModal(${c.id})">Опубликовать</button>
        <button class="btn small" onclick="openContentModal(${c.id})">Изменить</button>
        <button class="btn small danger" onclick="deleteContent(${c.id})">Удалить</button>
      </div>
    </div>`;
}

async function loadContent() {
  const kbList = document.getElementById("kb-list");
  const entries = await api("/api/knowledge-base");
  kbList.innerHTML = "";
  if (!entries.length) {
    kbList.innerHTML = `<div class="cbody">Пока пусто. Добавьте материал выше или сохраните идеи из «Анализа».</div>`;
  } else {
    entries.forEach((e) => {
      kbList.insertAdjacentHTML(
        "beforeend",
        `<div class="content-item">
          <div>
            <div class="ctitle">${e.title ? escapeHtml(e.title) : "Без названия"} <span class="status-badge ${e.source === "idea" ? "queued" : "published"}">${e.source === "idea" ? "идея" : "материал"}</span></div>
            <div class="cbody">${escapeHtml(e.content)}</div>
          </div>
          <div class="content-actions">
            <button class="btn small danger" onclick="deleteKbEntry(${e.id})">Удалить</button>
          </div>
        </div>`
      );
    });
  }

  await fetchPlatforms();
  const grid = document.getElementById("content-platforms");
  grid.innerHTML = "";
  state.platforms.forEach((p) => {
    grid.insertAdjacentHTML("beforeend", platformFileCard(p));
  });
  grid.querySelectorAll(".file-card").forEach((el) => {
    el.addEventListener("click", () => switchView("platforms"));
  });
  grid.querySelectorAll("[data-brief-btn]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openPlatformBriefModal(btn.dataset.briefBtn);
    });
  });
}

// ---------- Knowledge base ----------
document.getElementById("kb-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const status = document.getElementById("kb-file-status");
  const addBtn = document.getElementById("btn-add-kb");
  const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");

  addBtn.disabled = true;
  status.textContent = isPdf ? "Извлекаю текст из PDF..." : "Читаю файл...";

  if (!isPdf) {
    const reader = new FileReader();
    reader.onload = () => {
      document.getElementById("kb-content").value = reader.result;
      status.textContent = `Загружено из файла: ${file.name}`;
      addBtn.disabled = false;
    };
    reader.onerror = () => {
      toast("Не удалось прочитать файл", true);
      status.textContent = "";
      addBtn.disabled = false;
    };
    reader.readAsText(file, "utf-8");
    return;
  }

  try {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/platforms/extract-brief-text", { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Ошибка загрузки файла");
    }
    const data = await res.json();
    document.getElementById("kb-content").value = data.text;
    status.textContent = `Загружено из файла: ${file.name}`;
  } catch (err) {
    status.textContent = "";
    toast(err.message, true);
  } finally {
    addBtn.disabled = false;
  }
});

document.getElementById("btn-add-kb").addEventListener("click", async () => {
  const content = document.getElementById("kb-content").value.trim();
  if (!content) {
    toast("Вставьте текст или загрузите файл", true);
    return;
  }
  const payload = { title: document.getElementById("kb-title").value.trim(), content };
  try {
    await api("/api/knowledge-base", { method: "POST", body: JSON.stringify(payload) });
    document.getElementById("kb-title").value = "";
    document.getElementById("kb-content").value = "";
    document.getElementById("kb-file").value = "";
    document.getElementById("kb-file-status").textContent = "";
    toast("Материал добавлен в базу");
    loadContent();
  } catch (e) {
    toast(e.message, true);
  }
});

async function deleteKbEntry(id) {
  if (!confirm("Удалить материал из базы?")) return;
  await api(`/api/knowledge-base/${id}`, { method: "DELETE" });
  loadContent();
}

// ---------- Analysis ----------
async function loadAnalysis() {
  const items = await api("/api/analysis");
  const list = document.getElementById("analysis-list");
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = `<div class="cbody">Постов пока нет — создайте их на дашборде или в «Контент-плане».</div>`;
    return;
  }
  items.forEach((it) => {
    const wrapper = document.createElement("div");
    wrapper.className = "content-item";
    const scoreBadge = it.score != null ? `<span class="perf-badge ${it.score >= 70 ? "above" : it.score >= 40 ? "average" : "below"}">${it.score}/100</span>` : "";
    const suggestionsHtml = (it.suggestions || []).length
      ? `<ul class="analysis-suggestions">${it.suggestions.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>`
      : "";
    const statsHtml = (it.stats || []).length
      ? `<div class="cmeta" style="margin-top:6px;">${it.stats
          .map((s) => {
            const parts = [];
            if (s.views != null) parts.push(`👁 ${s.views}`);
            if (s.likes != null) parts.push(`❤️ ${s.likes}`);
            if (s.comments != null) parts.push(`💬 ${s.comments}`);
            return `<span class="status-badge">${escapeHtml(s.platform_name)}: ${parts.join(" ") || "нет данных"}</span>`;
          })
          .join(" ")}</div>`
      : `<div class="hint" style="margin-top:6px;">Метрик пока нет — оценка будет по тексту.</div>`;
    wrapper.innerHTML = `
      <div>
        <div class="ctitle">${escapeHtml(it.title)} ${scoreBadge}</div>
        <div class="cbody">${escapeHtml(it.body).slice(0, 200)}${it.body.length > 200 ? "…" : ""}</div>
        ${statsHtml}
        ${it.feedback ? `<div class="cmeta" style="margin-top:6px;">${escapeHtml(it.feedback)}</div>` : ""}
        ${suggestionsHtml}
      </div>
      <div class="content-actions"></div>
    `;
    const actions = wrapper.querySelector(".content-actions");

    const analyzeBtn = document.createElement("button");
    analyzeBtn.className = "btn small primary";
    analyzeBtn.textContent = it.score != null ? "Проанализировать заново" : "Анализировать";
    analyzeBtn.addEventListener("click", () => runAnalysis(it.content_id, analyzeBtn));
    actions.appendChild(analyzeBtn);

    if ((it.suggestions || []).length) {
      const saveBtn = document.createElement("button");
      saveBtn.className = "btn small";
      saveBtn.textContent = "Добавить идеи в базу";
      saveBtn.addEventListener("click", () => saveIdeasToBase(it.content_id, saveBtn));
      actions.appendChild(saveBtn);
    }

    list.appendChild(wrapper);
  });
}

async function runAnalysis(contentId, btn) {
  btn.disabled = true;
  btn.textContent = "Анализирую...";
  try {
    await api(`/api/analysis/${contentId}`, { method: "POST" });
    toast("Анализ готов");
    loadAnalysis();
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
    btn.textContent = "Анализировать";
  }
}

async function saveIdeasToBase(contentId, btn) {
  btn.disabled = true;
  try {
    const data = await api(`/api/analysis/${contentId}/save-ideas`, { method: "POST" });
    toast(data.added > 0 ? `Добавлено идей: ${data.added}` : "Идей нет");
    btn.textContent = "Добавлено ✓";
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
  }
}

// ---------- Posts (full text) ----------
async function loadPosts() {
  state.content = await api("/api/content");
  const list = document.getElementById("posts-list");
  list.innerHTML = "";
  if (!state.content.length) {
    list.innerHTML = `<div class="cbody">Постов пока нет. Создайте их на дашборде или в «Контент-плане».</div>`;
    return;
  }
  state.content.forEach((c) => {
    list.insertAdjacentHTML("beforeend", renderContentCard(c, { fullText: true }));
  });
}

// ---------- Platform ТЗ / channel ----------
function openPlatformBriefModal(platformId) {
  const p = state.platforms.find((x) => x.id === platformId);
  if (!p) return;
  document.getElementById("platform-brief-id").value = platformId;
  document.getElementById("platform-brief-title").textContent = `ТЗ и канал: ${p.name}`;
  document.getElementById("platform-brief-channel").value = p.channel_url || "";
  document.getElementById("platform-brief-text").value = p.brief || "";
  document.getElementById("platform-brief-file").value = "";
  document.getElementById("platform-brief-file-status").textContent = "";
  document.getElementById("modal-platform-brief").classList.add("active");
}

document.getElementById("btn-cancel-platform-brief").addEventListener("click", () => {
  document.getElementById("modal-platform-brief").classList.remove("active");
});

document.getElementById("platform-brief-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const status = document.getElementById("platform-brief-file-status");
  const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");

  if (!isPdf) {
    const reader = new FileReader();
    reader.onload = () => {
      document.getElementById("platform-brief-text").value = reader.result;
      status.textContent = `Загружено из файла: ${file.name}`;
    };
    reader.onerror = () => toast("Не удалось прочитать файл", true);
    reader.readAsText(file, "utf-8");
    return;
  }

  status.textContent = "Извлекаю текст из PDF...";
  try {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/platforms/extract-brief-text", { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Ошибка загрузки файла");
    }
    const data = await res.json();
    document.getElementById("platform-brief-text").value = data.text;
    status.textContent = `Загружено из файла: ${file.name}`;
  } catch (err) {
    status.textContent = "";
    toast(err.message, true);
  }
});

document.getElementById("btn-save-platform-brief").addEventListener("click", async () => {
  const id = document.getElementById("platform-brief-id").value;
  const payload = {
    brief: document.getElementById("platform-brief-text").value,
    channel_url: document.getElementById("platform-brief-channel").value,
  };
  try {
    await api(`/api/platforms/${id}/settings`, { method: "POST", body: JSON.stringify(payload) });
    document.getElementById("modal-platform-brief").classList.remove("active");
    toast("Сохранено");
    loadContent();
  } catch (e) {
    toast(e.message, true);
  }
});

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

const EMOJI_SET = ["😀","😂","🔥","🚀","✨","💡","👍","🎉","❤️","📣","📈","✅","👀","🙌","😉","💬"];

function updateCharCounter() {
  const len = document.getElementById("content-body").value.length;
  document.getElementById("char-counter").textContent = String(len);
}

function highlightTags(text) {
  return escapeHtml(text).replace(/#[\p{L}\d_]+/gu, (m) => `<span class="tag">${m}</span>`);
}

function renderPreview() {
  const brandName = document.getElementById("hero-brand-name").textContent.trim() || "Ваш бренд";
  document.getElementById("ppc-avatar").textContent = brandName.charAt(0).toUpperCase() || "К";
  document.getElementById("ppc-name").textContent = brandName;

  const title = document.getElementById("content-title").value.trim();
  document.getElementById("ppc-title").textContent = title;
  document.getElementById("ppc-title").hidden = !title;

  const body = document.getElementById("content-body").value;
  document.getElementById("ppc-body").innerHTML = body
    ? highlightTags(body)
    : '<span class="hint">Пока нечего показать</span>';

  const mediaUrl = document.getElementById("content-media").value.trim();
  const mediaBox = document.getElementById("ppc-media");
  if (mediaUrl) {
    const isVideo = /\.(mp4|mov|webm)$/i.test(mediaUrl);
    mediaBox.innerHTML = isVideo ? `<video src="${mediaUrl}" controls></video>` : `<img src="${mediaUrl}" alt="">`;
    mediaBox.hidden = false;
  } else {
    mediaBox.hidden = true;
    mediaBox.innerHTML = "";
  }

  const tags = document.getElementById("content-tags").value.trim();
  document.getElementById("ppc-tags").textContent = tags
    ? tags.split(",").map((t) => `#${t.trim()}`).filter((t) => t !== "#").join("  ")
    : "";

  const platformsBox = document.getElementById("preview-platforms");
  const checked = Array.from(document.querySelectorAll("#content-platform-checks input:checked"));
  platformsBox.innerHTML = checked.length
    ? checked
        .map((i) => {
          const p = state.platforms.find((pl) => pl.id === i.value);
          return `<span class="preview-platform-pill">${p ? p.name : i.value}</span>`;
        })
        .join("")
    : '<span class="preview-platform-pill">Платформа не выбрана</span>';
}

function setMediaPreview(url) {
  const box = document.getElementById("media-preview");
  if (!url) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  const isVideo = /\.(mp4|mov|webm)$/i.test(url);
  box.innerHTML = `
    ${isVideo ? `<video src="${url}" controls></video>` : `<img src="${url}" alt="">`}
    <button type="button" class="media-remove" id="btn-remove-media">Убрать</button>
  `;
  box.hidden = false;
  document.getElementById("btn-remove-media").addEventListener("click", () => {
    document.getElementById("content-media").value = "";
    setMediaPreview("");
  });
}

async function uploadMediaFile(file) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch("/api/content/upload-media", { method: "POST", body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Ошибка загрузки" }));
    throw new Error(err.detail || "Ошибка загрузки");
  }
  return res.json();
}

function openContentModal(id) {
  const modal = document.getElementById("modal-content");
  document.getElementById("content-id").value = id || "";
  if (id) {
    const c = state.content.find((x) => x.id === id);
    document.getElementById("content-modal-title").textContent = "Изменить пост";
    document.getElementById("content-title").value = c.title;
    document.getElementById("content-body").value = c.body;
    document.getElementById("content-media").value = c.media_url || "";
    document.getElementById("content-tags").value = c.tags || "";
    setMediaPreview(c.media_url || "");
  } else {
    document.getElementById("content-modal-title").textContent = "Новый пост";
    ["content-title", "content-body", "content-media", "content-tags"].forEach((f) => (document.getElementById(f).value = ""));
    setMediaPreview("");
  }
  document.getElementById("content-schedule").value = "";
  document.getElementById("content-preview").hidden = true;
  document.getElementById("content-body").hidden = false;
  document.getElementById("btn-toggle-preview").classList.remove("active");
  document.getElementById("post-settings-panel").hidden = true;
  document.getElementById("post-settings-caret").textContent = "▾";
  document.getElementById("emoji-picker").hidden = true;
  updateCharCounter();

  if (!state.platforms.length) fetchPlatforms().then(renderContentPlatformChecks);
  else renderContentPlatformChecks();

  modal.classList.add("active");
}

function renderContentPlatformChecks() {
  const id = document.getElementById("content-id").value;
  const c = id ? state.content.find((x) => x.id === Number(id)) : null;
  const selected = new Set((c?.platforms || []).map((p) => p.platform_id));
  const grid = document.getElementById("content-platform-checks");
  grid.innerHTML = state.platforms
    .map(
      (p) =>
        `<label class="check-item"><input type="checkbox" value="${p.id}" ${selected.has(p.id) ? "checked" : ""} ${p.connected ? "" : "disabled"}> ${p.name}${p.connected ? "" : " (не подключено)"}</label>`
    )
    .join("");
}

document.getElementById("btn-new-content").addEventListener("click", () => openContentModal(null));
document.getElementById("btn-close-content").addEventListener("click", () => document.getElementById("modal-content").classList.remove("active"));

document.getElementById("content-body").addEventListener("input", updateCharCounter);

function refreshPreviewIfVisible() {
  if (!document.getElementById("content-preview").hidden) renderPreview();
}
["content-title", "content-body", "content-media", "content-tags"].forEach((id) =>
  document.getElementById(id).addEventListener("input", refreshPreviewIfVisible)
);
document.getElementById("content-platform-checks").addEventListener("change", refreshPreviewIfVisible);

document.getElementById("btn-toggle-preview").addEventListener("click", () => {
  const textarea = document.getElementById("content-body");
  const preview = document.getElementById("content-preview");
  const showingPreview = !preview.hidden;
  if (showingPreview) {
    preview.hidden = true;
    textarea.hidden = false;
    document.getElementById("btn-toggle-preview").classList.remove("active");
  } else {
    renderPreview();
    preview.hidden = false;
    textarea.hidden = true;
    document.getElementById("btn-toggle-preview").classList.add("active");
  }
});

document.getElementById("tool-hashtag").addEventListener("click", () => {
  const textarea = document.getElementById("content-body");
  const pos = textarea.selectionStart || textarea.value.length;
  const before = textarea.value.slice(0, pos);
  const needsSpace = before && !/\s$/.test(before);
  textarea.value = before + (needsSpace ? " #" : "#") + textarea.value.slice(pos);
  textarea.focus();
  updateCharCounter();
});

document.getElementById("tool-list").addEventListener("click", () => {
  const textarea = document.getElementById("content-body");
  const pos = textarea.selectionStart || textarea.value.length;
  const before = textarea.value.slice(0, pos);
  const prefix = before && !before.endsWith("\n") ? "\n• " : "• ";
  textarea.value = before + prefix + textarea.value.slice(pos);
  textarea.focus();
  updateCharCounter();
});

document.getElementById("tool-uppercase").addEventListener("click", () => {
  const textarea = document.getElementById("content-body");
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  if (start === end) return;
  const selected = textarea.value.slice(start, end);
  const upper = selected === selected.toUpperCase();
  const replaced = upper ? selected.toLowerCase() : selected.toUpperCase();
  textarea.value = textarea.value.slice(0, start) + replaced + textarea.value.slice(end);
  textarea.focus();
  updateCharCounter();
});

document.getElementById("tool-emoji").addEventListener("click", () => {
  const picker = document.getElementById("emoji-picker");
  if (!picker.hidden) {
    picker.hidden = true;
    return;
  }
  picker.innerHTML = EMOJI_SET.map((e) => `<span>${e}</span>`).join("");
  picker.hidden = false;
  picker.querySelectorAll("span").forEach((span) => {
    span.addEventListener("click", () => {
      const textarea = document.getElementById("content-body");
      const pos = textarea.selectionStart || textarea.value.length;
      textarea.value = textarea.value.slice(0, pos) + span.textContent + textarea.value.slice(pos);
      textarea.focus();
      updateCharCounter();
      picker.hidden = true;
    });
  });
});

document.getElementById("btn-toggle-post-settings").addEventListener("click", () => {
  const panel = document.getElementById("post-settings-panel");
  panel.hidden = !panel.hidden;
  document.getElementById("post-settings-caret").textContent = panel.hidden ? "▾" : "▴";
});

const mediaDropzone = document.getElementById("media-dropzone");
const mediaFileInput = document.getElementById("media-file-input");

mediaDropzone.addEventListener("click", () => mediaFileInput.click());
mediaDropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  mediaDropzone.classList.add("dragover");
});
mediaDropzone.addEventListener("dragleave", () => mediaDropzone.classList.remove("dragover"));
mediaDropzone.addEventListener("drop", async (e) => {
  e.preventDefault();
  mediaDropzone.classList.remove("dragover");
  const file = e.dataTransfer.files?.[0];
  if (file) await handleMediaFile(file);
});
mediaFileInput.addEventListener("change", async () => {
  const file = mediaFileInput.files?.[0];
  if (file) await handleMediaFile(file);
  mediaFileInput.value = "";
});

async function handleMediaFile(file) {
  try {
    toast("Загрузка файла...");
    const { url } = await uploadMediaFile(file);
    document.getElementById("content-media").value = url;
    setMediaPreview(url);
    toast("Файл загружен");
  } catch (e) {
    toast(e.message, true);
  }
}

document.getElementById("content-media").addEventListener("change", (e) => setMediaPreview(e.target.value));

function collectPostPayload() {
  const title = document.getElementById("content-title").value.trim();
  return {
    title,
    body: document.getElementById("content-body").value,
    media_url: document.getElementById("content-media").value || null,
    tags: document.getElementById("content-tags").value,
  };
}

function selectedContentPlatformIds() {
  return Array.from(document.querySelectorAll("#content-platform-checks input:checked")).map((i) => i.value);
}

async function savePostContent() {
  const id = document.getElementById("content-id").value;
  const payload = collectPostPayload();
  if (!payload.title) {
    toast("Введите заголовок", true);
    return null;
  }
  if (id) {
    return api(`/api/content/${id}`, { method: "PUT", body: JSON.stringify(payload) });
  }
  return api("/api/content", { method: "POST", body: JSON.stringify(payload) });
}

document.getElementById("btn-save-draft").addEventListener("click", async () => {
  try {
    const saved = await savePostContent();
    if (!saved) return;
    document.getElementById("modal-content").classList.remove("active");
    toast("Сохранено как черновик");
    loadContent();
  } catch (e) {
    toast(e.message, true);
  }
});

document.getElementById("btn-publish-now").addEventListener("click", async () => {
  const platformIds = selectedContentPlatformIds();
  if (!platformIds.length) {
    toast("Выберите платформу в «Настройках поста»", true);
    document.getElementById("post-settings-panel").hidden = false;
    document.getElementById("post-settings-caret").textContent = "▴";
    return;
  }
  try {
    const saved = await savePostContent();
    if (!saved) return;
    await api(`/api/content/${saved.id}/publish`, {
      method: "POST",
      body: JSON.stringify({ platform_ids: platformIds, scheduled_at: null }),
    });
    document.getElementById("modal-content").classList.remove("active");
    toast("Отправлено на публикацию");
    loadContent();
  } catch (e) {
    toast(e.message, true);
  }
});

document.getElementById("btn-schedule-post").addEventListener("click", async () => {
  const platformIds = selectedContentPlatformIds();
  const scheduleVal = document.getElementById("content-schedule").value;
  if (!platformIds.length || !scheduleVal) {
    toast("Выберите платформу и дату в «Настройках поста»", true);
    document.getElementById("post-settings-panel").hidden = false;
    document.getElementById("post-settings-caret").textContent = "▴";
    return;
  }
  try {
    const saved = await savePostContent();
    if (!saved) return;
    await api(`/api/content/${saved.id}/publish`, {
      method: "POST",
      body: JSON.stringify({
        platform_ids: platformIds,
        scheduled_at: new Date(scheduleVal).toISOString().slice(0, 19).replace("T", " "),
      }),
    });
    document.getElementById("modal-content").classList.remove("active");
    toast("Запланировано");
    loadContent();
  } catch (e) {
    toast(e.message, true);
  }
});

async function deleteContent(id) {
  if (!confirm("Удалить материал?")) return;
  await api(`/api/content/${id}`, { method: "DELETE" });
  toast("Удалено");
  loadContent();
}

// ---------- Publish ----------
async function openPublishModal(contentId) {
  if (!state.platforms.length) await fetchPlatforms();
  document.getElementById("publish-content-id").value = contentId;
  const grid = document.getElementById("publish-platform-checks");
  grid.innerHTML = "";
  state.platforms.forEach((p) => {
    grid.insertAdjacentHTML(
      "beforeend",
      `<label class="check-item"><input type="checkbox" value="${p.id}" ${p.connected ? "" : "disabled"}> ${p.name}${p.connected ? "" : " (не подключено)"}</label>`
    );
  });
  document.getElementById("publish-schedule").value = "";
  document.getElementById("modal-publish").classList.add("active");
}

document.getElementById("btn-cancel-publish").addEventListener("click", () => document.getElementById("modal-publish").classList.remove("active"));

document.getElementById("btn-confirm-publish").addEventListener("click", async () => {
  const contentId = document.getElementById("publish-content-id").value;
  const platformIds = Array.from(document.querySelectorAll("#publish-platform-checks input:checked")).map((i) => i.value);
  if (!platformIds.length) {
    toast("Выберите хотя бы одну платформу", true);
    return;
  }
  const scheduleVal = document.getElementById("publish-schedule").value;
  const payload = {
    platform_ids: platformIds,
    scheduled_at: scheduleVal ? new Date(scheduleVal).toISOString().slice(0, 19).replace("T", " ") : null,
  };
  try {
    await api(`/api/content/${contentId}/publish`, { method: "POST", body: JSON.stringify(payload) });
    document.getElementById("modal-publish").classList.remove("active");
    toast(payload.scheduled_at ? "Запланировано" : "Отправлено на публикацию");
    loadContent();
  } catch (e) {
    toast(e.message, true);
  }
});

async function loadPublishQueue() {
  const rows = await api("/api/publish/queue");
  const tbody = document.getElementById("publish-queue");
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="5">Очередь пуста</td></tr>`;
    return;
  }
  rows.forEach((r) => {
    tbody.insertAdjacentHTML(
      "beforeend",
      `<tr>
        <td>${escapeHtml(r.title)}</td>
        <td>${r.platform_id}</td>
        <td><span class="status-badge ${r.status}">${statusLabel(r.status)}</span></td>
        <td>${r.scheduled_at || r.published_at || "—"}</td>
        <td>${r.url ? `<a href="${r.url}" target="_blank">ссылка</a>` : (r.error || "—")}</td>
      </tr>`
    );
  });
}

// ---------- Platforms ----------
async function fetchPlatforms() {
  state.platforms = await api("/api/platforms");
}

async function loadPlatforms() {
  await fetchPlatforms();
  const grid = document.getElementById("platforms-list");
  grid.innerHTML = "";
  state.platforms.forEach((p) => {
    grid.insertAdjacentHTML(
      "beforeend",
      `<div class="file-card">
        <div class="file-icon" style="background:${p.color}">${platformInitials(p.name)}</div>
        <div class="fname">${p.name}</div>
        <div class="fsub">${p.connected ? "Подключено" : "Не подключено"}</div>
        <span class="file-badge ${p.connected ? "connected" : ""}" style="margin-bottom:12px;display:block;width:fit-content;">${p.connected ? "Активно" : "Требует настройки"}</span>
        <div class="content-actions">
          <button class="btn small" onclick="openPlatformModal('${p.id}')">${p.connected ? "Изменить" : "Подключить"}</button>
          ${p.connected ? `<button class="btn small danger" onclick="disconnectPlatform('${p.id}')">Отключить</button>` : ""}
        </div>
      </div>`
    );
  });
}

function openPlatformModal(id) {
  const p = state.platforms.find((x) => x.id === id);
  document.getElementById("platform-id").value = id;
  document.getElementById("platform-modal-title").textContent = `Подключить: ${p.name}`;
  const fields = document.getElementById("platform-fields");
  fields.innerHTML = "";
  p.credential_fields.forEach((f) => {
    fields.insertAdjacentHTML(
      "beforeend",
      `<label>${f}</label><input type="text" data-field="${f}" placeholder="${f}">`
    );
  });
  document.getElementById("modal-platform").classList.add("active");
}

document.getElementById("btn-cancel-platform").addEventListener("click", () => document.getElementById("modal-platform").classList.remove("active"));

document.getElementById("btn-save-platform").addEventListener("click", async () => {
  const id = document.getElementById("platform-id").value;
  const credentials = {};
  document.querySelectorAll("#platform-fields input").forEach((inp) => {
    if (inp.value) credentials[inp.dataset.field] = inp.value;
  });
  try {
    await api(`/api/platforms/${id}/connect`, { method: "POST", body: JSON.stringify({ credentials }) });
    document.getElementById("modal-platform").classList.remove("active");
    toast("Платформа подключена");
    loadPlatforms();
  } catch (e) {
    toast(e.message, true);
  }
});

async function disconnectPlatform(id) {
  await api(`/api/platforms/${id}/disconnect`, { method: "POST" });
  toast("Отключено");
  loadPlatforms();
}

// ---------- Sales ----------
async function loadSales() {
  if (!state.platforms.length) await fetchPlatforms();
  const select = document.getElementById("sale-platform");
  select.innerHTML = state.platforms.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");

  const [sales, summary] = await Promise.all([api("/api/sales"), api("/api/analytics/summary")]);

  const cards = document.getElementById("sales-cards");
  cards.innerHTML = `
    <div class="card"><div class="value">${(summary.sales_totals.revenue || 0).toLocaleString("ru-RU")} ₽</div><div class="label">Выручка всего</div></div>
    <div class="card"><div class="value">${summary.sales_totals.orders || 0}</div><div class="label">Заказов всего</div></div>
  `;

  const tbody = document.getElementById("sales-list");
  tbody.innerHTML = "";
  if (!sales.length) {
    tbody.innerHTML = `<tr><td colspan="6">Записей пока нет</td></tr>`;
    return;
  }
  sales.forEach((s) => {
    tbody.insertAdjacentHTML(
      "beforeend",
      `<tr>
        <td>${s.date}</td>
        <td>${s.platform_id}</td>
        <td>${s.orders}</td>
        <td>${s.revenue.toLocaleString("ru-RU")} ₽</td>
        <td>${escapeHtml(s.notes)}</td>
        <td><button class="btn small danger" onclick="deleteSale(${s.id})">Удалить</button></td>
      </tr>`
    );
  });
}

document.getElementById("btn-new-sale").addEventListener("click", () => {
  document.getElementById("sale-date").value = new Date().toISOString().slice(0, 10);
  document.getElementById("sale-orders").value = 0;
  document.getElementById("sale-revenue").value = 0;
  document.getElementById("sale-notes").value = "";
  document.getElementById("modal-sale").classList.add("active");
});

document.getElementById("btn-cancel-sale").addEventListener("click", () => document.getElementById("modal-sale").classList.remove("active"));

document.getElementById("btn-save-sale").addEventListener("click", async () => {
  const payload = {
    platform_id: document.getElementById("sale-platform").value,
    date: document.getElementById("sale-date").value || null,
    orders: parseInt(document.getElementById("sale-orders").value || "0", 10),
    revenue: parseFloat(document.getElementById("sale-revenue").value || "0"),
    notes: document.getElementById("sale-notes").value,
  };
  try {
    await api("/api/sales", { method: "POST", body: JSON.stringify(payload) });
    document.getElementById("modal-sale").classList.remove("active");
    toast("Запись добавлена");
    loadSales();
  } catch (e) {
    toast(e.message, true);
  }
});

async function deleteSale(id) {
  await api(`/api/sales/${id}`, { method: "DELETE" });
  toast("Удалено");
  loadSales();
}

// ---------- Settings ----------
function toggleProviderBlocks(provider) {
  document.getElementById("settings-anthropic-block").style.display = provider === "anthropic" ? "" : "none";
  document.getElementById("settings-openai-block").style.display = provider === "openai" ? "" : "none";
  document.getElementById("settings-gemini-block").style.display = provider === "gemini" ? "" : "none";
  document.getElementById("settings-ollama-block").style.display = provider === "ollama" ? "" : "none";
}

async function loadSettings() {
  const s = await api("/api/settings");
  document.getElementById("settings-provider").value = s.ai_provider;
  toggleProviderBlocks(s.ai_provider);

  const anthropicStatus = document.getElementById("settings-anthropic-status");
  anthropicStatus.textContent = s.anthropic_api_key_set
    ? "Ключ сохранён и активен."
    : "Ключ ещё не задан — генерация через Claude недоступна.";
  document.getElementById("settings-api-key").value = "";

  const openaiStatus = document.getElementById("settings-openai-status");
  openaiStatus.textContent = s.openai_api_key_set
    ? "Ключ сохранён и активен."
    : "Ключ ещё не задан — генерация через ChatGPT недоступна.";
  document.getElementById("settings-openai-api-key").value = "";
  document.getElementById("settings-openai-model").value = s.openai_model;

  const geminiStatus = document.getElementById("settings-gemini-status");
  geminiStatus.textContent = s.gemini_api_key_set
    ? "Ключ сохранён и активен."
    : "Ключ ещё не задан — генерация через Gemini недоступна.";
  document.getElementById("settings-gemini-api-key").value = "";
  document.getElementById("settings-gemini-model").value = s.gemini_model;

  document.getElementById("settings-ollama-url").value = s.ollama_base_url;
  document.getElementById("settings-ollama-model").value = s.ollama_model;
  document.getElementById("settings-ollama-status").textContent = "";

  document.getElementById("settings-brand-name").value = s.brand_name;
  document.getElementById("settings-brand-description").value = s.brand_description;

  document.getElementById("settings-gsheet-key-path").value = s.google_service_account_path;
  document.getElementById("settings-gsheet-url").value = s.google_sheet_url;
  document.getElementById("settings-gsheet-worksheet").value = s.google_sheet_worksheet;
  document.getElementById("settings-gsheet-autosync").checked = s.google_sheets_auto_sync;
  document.getElementById("settings-gsheet-status").textContent = "";
}

document.getElementById("btn-save-gsheet").addEventListener("click", async () => {
  const payload = {
    google_service_account_path: document.getElementById("settings-gsheet-key-path").value,
    google_sheet_url: document.getElementById("settings-gsheet-url").value,
    google_sheet_worksheet: document.getElementById("settings-gsheet-worksheet").value,
    google_sheets_auto_sync: document.getElementById("settings-gsheet-autosync").checked,
  };
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    toast("Настройки Google Таблицы сохранены");
  } catch (e) {
    toast(e.message, true);
  }
});

document.getElementById("btn-check-gsheet").addEventListener("click", async () => {
  const status = document.getElementById("settings-gsheet-status");
  status.textContent = "Проверяю...";
  try {
    const data = await api("/api/settings/google-sheets-status");
    const detected = Object.keys(data.detected_columns);
    status.textContent = `Подключено: «${data.spreadsheet_title}» / лист «${data.worksheet_title}». Распознаны колонки: ${detected.join(", ") || "не найдены"}.`;
  } catch (e) {
    status.textContent = e.message;
  }
});

document.getElementById("btn-save-brand").addEventListener("click", async () => {
  const payload = {
    brand_name: document.getElementById("settings-brand-name").value,
    brand_description: document.getElementById("settings-brand-description").value,
  };
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    toast("Описание бренда сохранено");
    loadSettings();
  } catch (e) {
    toast(e.message, true);
  }
});

document.getElementById("settings-provider").addEventListener("change", (e) => {
  toggleProviderBlocks(e.target.value);
});

document.getElementById("btn-check-ollama").addEventListener("click", async () => {
  const status = document.getElementById("settings-ollama-status");
  status.textContent = "Проверяю...";
  try {
    const data = await api("/api/settings/ollama-status");
    if (data.reachable) {
      status.textContent = data.models.length
        ? `Ollama доступна. Установленные модели: ${data.models.join(", ")}`
        : "Ollama доступна, но моделей не установлено — выполните ollama pull.";
    } else {
      status.textContent = "Ollama не отвечает. Убедитесь, что она запущена (ollama serve).";
    }
  } catch (e) {
    status.textContent = "Не удалось проверить подключение.";
  }
});

document.getElementById("btn-save-settings").addEventListener("click", async () => {
  const provider = document.getElementById("settings-provider").value;
  const payload = { ai_provider: provider };

  if (provider === "anthropic") {
    const key = document.getElementById("settings-api-key").value.trim();
    if (key) payload.anthropic_api_key = key;
  } else if (provider === "openai") {
    const key = document.getElementById("settings-openai-api-key").value.trim();
    if (key) payload.openai_api_key = key;
    payload.openai_model = document.getElementById("settings-openai-model").value.trim() || "gpt-4o-mini";
  } else if (provider === "gemini") {
    const key = document.getElementById("settings-gemini-api-key").value.trim();
    if (key) payload.gemini_api_key = key;
    payload.gemini_model = document.getElementById("settings-gemini-model").value.trim() || "gemini-2.0-flash";
  } else {
    payload.ollama_base_url = document.getElementById("settings-ollama-url").value.trim() || "http://localhost:11434";
    payload.ollama_model = document.getElementById("settings-ollama-model").value.trim() || "qwen2.5:7b";
  }

  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    toast("Настройки сохранены");
    loadSettings();
  } catch (e) {
    toast(e.message, true);
  }
});

// ---------- AI generation (inline on dashboard) ----------
document.getElementById("hero-generate-btn").addEventListener("click", async () => {
  const topic = document.getElementById("hero-topic").value.trim();
  if (!topic) {
    toast("Укажите тему поста", true);
    return;
  }
  const platformIds = Array.from(document.querySelectorAll("#hero-platform-checks input:checked")).map((i) => i.value);
  if (!platformIds.length) {
    toast("Выберите хотя бы одну социальную сеть", true);
    return;
  }
  const lengthValue = document.getElementById("hero-length").value.trim();
  const payload = {
    topic,
    brief: document.getElementById("hero-brief").value,
    tone: document.getElementById("hero-tone").value,
    platform_ids: platformIds,
    variants: parseInt(document.getElementById("hero-variants").value, 10),
    length: lengthValue ? parseInt(lengthValue, 10) : null,
    provider: document.getElementById("hero-provider").value || null,
  };
  const results = document.getElementById("hero-ai-results");
  results.innerHTML = `<div class="spinner">Генерация... это может занять несколько секунд</div>`;
  try {
    const data = await api("/api/ai/generate", { method: "POST", body: JSON.stringify(payload) });
    renderAIVariants(data.variants);
    if (data.errors && data.errors.length) {
      toast(`Не удалось сгенерировать для: ${data.errors.join("; ")}`, true);
    }
  } catch (e) {
    results.innerHTML = "";
    toast(e.message, true);
  }
});

function renderAIVariants(variants) {
  const results = document.getElementById("hero-ai-results");
  results.innerHTML = "";
  if (!variants.length) {
    results.innerHTML = `<div class="spinner">Модель не вернула варианты</div>`;
    return;
  }

  const groups = new Map();
  variants.forEach((v) => {
    const key = v.platform_id || "";
    if (!groups.has(key)) groups.set(key, { name: v.platform_name || "", items: [] });
    groups.get(key).items.push(v);
  });

  groups.forEach((group) => {
    if (group.name) {
      results.insertAdjacentHTML("beforeend", `<h3 class="ai-platform-heading">${escapeHtml(group.name)}</h3>`);
    }
    group.items.forEach((v) => {
      const wrapper = document.createElement("div");
      wrapper.className = "ai-variant";
      wrapper.innerHTML = `
        <div class="avtitle">${escapeHtml(v.title)}</div>
        <div class="avbody">${escapeHtml(v.body)}</div>
        ${v.tags ? `<div class="avtags">#${escapeHtml(v.tags).replaceAll(", ", " #")}</div>` : ""}
        <button class="btn small primary">Использовать этот вариант</button>
      `;
      wrapper.querySelector("button").addEventListener("click", () => {
        openContentModal(null);
        document.getElementById("content-title").value = v.title;
        document.getElementById("content-body").value = v.body;
        document.getElementById("content-tags").value = v.tags;
      });
      results.appendChild(wrapper);
    });
  });
}

// ---------- Content plan ----------
const PLAN_STATUS_LABELS = { idea: "Идея", drafted: "Текст готов", approved: "Одобрено, в таблице" };

function updatePlanPeriodHint() {
  const start = document.getElementById("plan-start-date").value;
  const end = document.getElementById("plan-end-date").value;
  const hint = document.getElementById("plan-period-hint");
  if (!start || !end) {
    hint.textContent = "";
    return;
  }
  const days = Math.round((new Date(end) - new Date(start)) / 86400000) + 1;
  hint.textContent = days > 0 ? `Период: ${days} дн.` : "Дата «по» не может быть раньше даты «с»";
}

document.getElementById("plan-start-date").addEventListener("change", updatePlanPeriodHint);
document.getElementById("plan-end-date").addEventListener("change", updatePlanPeriodHint);

async function loadPlan() {
  if (!state.platforms.length) await fetchPlatforms();

  const platformSelect = document.getElementById("plan-platform");
  if (!platformSelect.dataset.filled) {
    platformSelect.innerHTML = state.platforms.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
    platformSelect.dataset.filled = "1";
  }

  if (!state.planOptions) {
    state.planOptions = await api("/api/plan/options");
  }
  if (!document.getElementById("plan-builder-rows").children.length) {
    addPlanRow();
  }

  const startInput = document.getElementById("plan-start-date");
  const endInput = document.getElementById("plan-end-date");
  if (!startInput.value) {
    const today = new Date();
    const in7days = new Date(today.getTime() + 6 * 86400000);
    startInput.value = today.toISOString().slice(0, 10);
    endInput.value = in7days.toISOString().slice(0, 10);
    updatePlanPeriodHint();
  }

  const [planData, statsData] = await Promise.all([api("/api/plan"), api("/api/stats")]);
  renderPlan(planData);
  renderStats(statsData);
}

function addPlanRow() {
  const opts = state.planOptions;
  const tbody = document.getElementById("plan-builder-rows");
  const tr = document.createElement("tr");
  const directionOptions = opts.directions.map((d) => `<option value="${d}">${d}</option>`).join("");
  const contentOptions = opts.content_types.map((c) => `<option value="${c}">${c}</option>`).join("");
  tr.innerHTML = `
    <td><select class="plan-row-direction">${directionOptions}</select></td>
    <td><select class="plan-row-content">${contentOptions}</select></td>
    <td><select class="plan-row-format"></select></td>
    <td><input type="number" class="plan-row-qty" value="3" min="1" max="30"></td>
    <td><button class="btn small danger" type="button">✕</button></td>
  `;
  tbody.appendChild(tr);

  const contentSelect = tr.querySelector(".plan-row-content");
  const formatSelect = tr.querySelector(".plan-row-format");
  function refreshFormats() {
    const formats = opts.formats_by_content_type[contentSelect.value] || [];
    formatSelect.innerHTML = formats.map((f) => `<option value="${f}">${f}</option>`).join("");
  }
  contentSelect.addEventListener("change", refreshFormats);
  refreshFormats();

  tr.querySelector("button").addEventListener("click", () => {
    if (tbody.children.length > 1) tr.remove();
  });
}

document.getElementById("btn-add-plan-row").addEventListener("click", addPlanRow);

function renderPlan(items) {
  const list = document.getElementById("plan-list");
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = `<div class="cbody">План пока пуст. Настройте строки выше и нажмите «Сгенерировать план».</div>`;
    return;
  }
  const byDate = new Map();
  items.forEach((it) => {
    if (!byDate.has(it.plan_date)) byDate.set(it.plan_date, []);
    byDate.get(it.plan_date).push(it);
  });

  Array.from(byDate.keys())
    .sort()
    .forEach((date) => {
      const dayItems = byDate.get(date);
      const dayEl = document.createElement("div");
      dayEl.className = "plan-day";
      dayEl.innerHTML = `<div class="plan-day-date">${date}</div>`;
      dayItems.forEach((it) => {
        const row = document.createElement("div");
        row.className = "plan-item";
        row.innerHTML = `
          <div class="plan-item-main">
            <span class="plan-item-platform">${escapeHtml(it.platform_id)}</span>
            <span class="plan-item-topic">${escapeHtml(it.topic)}</span>
            <div class="plan-item-format">${[it.direction, it.content_type, it.format].filter(Boolean).map(escapeHtml).join(" · ") || escapeHtml(it.format_hint || "")} — ${PLAN_STATUS_LABELS[it.status] || it.status}</div>
          </div>
          <div class="plan-item-actions"></div>
        `;
        const actions = row.querySelector(".plan-item-actions");
        if (it.status === "idea") {
          const writeBtn = document.createElement("button");
          writeBtn.className = "btn small primary";
          writeBtn.textContent = "Написать текст";
          writeBtn.addEventListener("click", () => writePlanItem(it.id, writeBtn));
          actions.appendChild(writeBtn);
        } else if (it.status === "drafted") {
          const openBtn = document.createElement("button");
          openBtn.className = "btn small";
          openBtn.textContent = "Открыть в Контенте";
          openBtn.addEventListener("click", () => switchView("content"));
          actions.appendChild(openBtn);

          const approveBtn = document.createElement("button");
          approveBtn.className = "btn small primary";
          approveBtn.textContent = "✅ Одобрить";
          approveBtn.addEventListener("click", () => approvePlanItem(it.id, approveBtn));
          actions.appendChild(approveBtn);
        } else {
          const openBtn = document.createElement("button");
          openBtn.className = "btn small";
          openBtn.textContent = "Открыть в Контенте";
          openBtn.addEventListener("click", () => switchView("content"));
          actions.appendChild(openBtn);
        }
        const delBtn = document.createElement("button");
        delBtn.className = "btn small danger";
        delBtn.textContent = "Удалить";
        delBtn.addEventListener("click", () => deletePlanItem(it.id));
        actions.appendChild(delBtn);
        dayEl.appendChild(row);
      });
      list.appendChild(dayEl);
    });
}

async function writePlanItem(id, btn) {
  btn.disabled = true;
  btn.textContent = "Пишу...";
  try {
    await api(`/api/plan/${id}/write`, { method: "POST", body: JSON.stringify({}) });
    toast("Текст готов, черновик создан в «Контенте»");
    loadPlan();
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
    btn.textContent = "Написать текст";
  }
}

async function approvePlanItem(id, btn) {
  btn.disabled = true;
  btn.textContent = "Одобряю...";
  try {
    await api(`/api/plan/${id}/approve`, { method: "POST" });
    toast("Одобрено — текст выгружен в Google Таблицу");
    loadPlan();
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
    btn.textContent = "✅ Одобрить";
  }
}

async function deletePlanItem(id) {
  if (!confirm("Удалить пункт плана?")) return;
  await api(`/api/plan/${id}`, { method: "DELETE" });
  loadPlan();
}

document.getElementById("btn-generate-plan").addEventListener("click", async () => {
  const rows = Array.from(document.getElementById("plan-builder-rows").children).map((tr) => ({
    direction: tr.querySelector(".plan-row-direction").value,
    content_type: tr.querySelector(".plan-row-content").value,
    format: tr.querySelector(".plan-row-format").value,
    quantity: parseInt(tr.querySelector(".plan-row-qty").value, 10) || 1,
  }));
  if (!rows.length) {
    toast("Добавьте хотя бы одну строку плана", true);
    return;
  }
  const startDate = document.getElementById("plan-start-date").value;
  const endDate = document.getElementById("plan-end-date").value;
  if (!startDate || !endDate) {
    toast("Укажите период — с и по", true);
    return;
  }
  if (endDate < startDate) {
    toast("Дата «по» не может быть раньше даты «с»", true);
    return;
  }
  const payload = {
    platform_id: document.getElementById("plan-platform").value,
    rows,
    start_date: startDate,
    end_date: endDate,
    tone: document.getElementById("plan-tone").value,
  };
  const btn = document.getElementById("btn-generate-plan");
  btn.disabled = true;
  btn.textContent = "Генерирую план...";
  try {
    const data = await api("/api/plan/generate", { method: "POST", body: JSON.stringify(payload) });
    renderPlan(data.items);
    toast("План сгенерирован");
    if (data.errors && data.errors.length) {
      toast(`Не удалось для: ${data.errors.join("; ")}`, true);
    }
    if (data.sheet_errors && data.sheet_errors.length) {
      toast(`Google Таблица: ${data.sheet_errors.join("; ")}`, true);
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "📅 Сгенерировать план";
  }
});

document.getElementById("btn-sync-gsheet").addEventListener("click", async () => {
  const btn = document.getElementById("btn-sync-gsheet");
  btn.disabled = true;
  btn.textContent = "Выгружаю...";
  try {
    const data = await api("/api/plan/sync-sheet", { method: "POST" });
    toast(data.synced > 0 ? `Выгружено строк: ${data.synced}` : "Нечего выгружать — всё уже синхронизировано");
    if (data.errors && data.errors.length) {
      toast(data.errors.join("; "), true);
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "📤 Выгрузить в Google Таблицу";
  }
});

// ---------- Performance stats ----------
const PERF_LABELS = { above: "🔥 Выше среднего", below: "📉 Ниже среднего", average: "Средне" };

function renderStats(data) {
  const tbody = document.getElementById("stats-list");
  tbody.innerHTML = "";
  if (!data.items.length) {
    tbody.innerHTML = `<tr><td colspan="7">Опубликованных постов пока нет</td></tr>`;
    return;
  }
  data.items.forEach((r) => {
    const perf = r.performance ? `<span class="perf-badge ${r.performance}">${PERF_LABELS[r.performance]}</span>` : "—";
    tbody.insertAdjacentHTML(
      "beforeend",
      `<tr>
        <td>${escapeHtml(r.title)}</td>
        <td>${escapeHtml(r.platform_id)}</td>
        <td>${r.views ?? "—"}</td>
        <td>${r.likes ?? "—"}</td>
        <td>${r.comments ?? "—"}</td>
        <td>${perf}</td>
        <td><button class="btn small" onclick="openStatsModal(${r.content_platform_id}, ${r.views ?? "null"}, ${r.likes ?? "null"}, ${r.comments ?? "null"})">Внести вручную</button></td>
      </tr>`
    );
  });
}

document.getElementById("btn-refresh-telegram-stats").addEventListener("click", async () => {
  try {
    const res = await api("/api/stats/telegram/refresh", { method: "POST" });
    toast(res.message || `Обновлено постов: ${res.updated}`);
    loadPlan();
  } catch (e) {
    toast(e.message, true);
  }
});

function openStatsModal(contentPlatformId, views, likes, comments) {
  document.getElementById("stats-content-platform-id").value = contentPlatformId;
  document.getElementById("stats-views").value = views ?? "";
  document.getElementById("stats-likes").value = likes ?? "";
  document.getElementById("stats-comments").value = comments ?? "";
  document.getElementById("modal-stats").classList.add("active");
}

document.getElementById("btn-cancel-stats").addEventListener("click", () => {
  document.getElementById("modal-stats").classList.remove("active");
});

document.getElementById("btn-save-stats").addEventListener("click", async () => {
  const id = document.getElementById("stats-content-platform-id").value;
  const toNum = (v) => (v === "" ? null : parseInt(v, 10));
  const payload = {
    views: toNum(document.getElementById("stats-views").value),
    likes: toNum(document.getElementById("stats-likes").value),
    comments: toNum(document.getElementById("stats-comments").value),
  };
  try {
    await api(`/api/stats/${id}/manual`, { method: "POST", body: JSON.stringify(payload) });
    document.getElementById("modal-stats").classList.remove("active");
    toast("Сохранено");
    loadPlan();
  } catch (e) {
    toast(e.message, true);
  }
});

// ---------- AI assistant ----------
const ASSISTANT_FAVORITES_KEY = "assistant_favorites";

function getAssistantFavorites() {
  try {
    return new Set(JSON.parse(localStorage.getItem(ASSISTANT_FAVORITES_KEY) || "[]"));
  } catch (e) {
    return new Set();
  }
}

function setAssistantFavorites(set) {
  try {
    localStorage.setItem(ASSISTANT_FAVORITES_KEY, JSON.stringify(Array.from(set)));
  } catch (e) {
    // localStorage недоступен (приватный режим и т.п.) — просто не сохраняем избранное
  }
}

function toggleAssistantFavorite(toolId) {
  const favs = getAssistantFavorites();
  if (favs.has(toolId)) favs.delete(toolId);
  else favs.add(toolId);
  setAssistantFavorites(favs);
  renderAssistantGrid();
}

async function loadAssistant() {
  if (!state.assistantTools.length) {
    state.assistantTools = await api("/api/ai/assistant/tools");
  }
  renderAssistantGrid();
}

function renderAssistantGrid() {
  const grid = document.getElementById("assistant-grid");
  const favs = getAssistantFavorites();
  let tools = state.assistantTools;
  if (state.assistantMode === "favorites") {
    tools = tools.filter((t) => favs.has(t.id));
  }
  if (state.assistantCategory !== "all") {
    tools = tools.filter((t) => t.category === state.assistantCategory);
  }

  if (!tools.length) {
    grid.innerHTML = `<div class="spinner">${
      state.assistantMode === "favorites" ? "Вы ещё не добавили карточки в избранное — нажмите ♡ на нужном инструменте." : "Ничего не найдено"
    }</div>`;
    return;
  }

  grid.innerHTML = tools
    .map((t) => {
      const isFav = favs.has(t.id);
      const tagLabel = t.category === "photo" ? "Фото" : "AI текст";
      return `
        <div class="assistant-card ${t.available ? "" : "unavailable"}" data-tool-id="${t.id}">
          <div class="assistant-card-top">
            <span class="assistant-card-tag">${tagLabel}</span>
            <button type="button" class="assistant-fav-btn ${isFav ? "active" : ""}" data-fav-id="${t.id}">${isFav ? "♥" : "♡"}</button>
          </div>
          <div class="assistant-card-icon">${t.icon}</div>
          <div class="assistant-card-title">${escapeHtml(t.title)}</div>
          <div class="assistant-card-desc">${escapeHtml(t.description)}</div>
          ${t.available ? "" : `<div class="assistant-card-unavailable-note">${escapeHtml(t.unavailable_reason)}</div>`}
        </div>
      `;
    })
    .join("");

  grid.querySelectorAll(".assistant-fav-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleAssistantFavorite(btn.dataset.favId);
    });
  });
  grid.querySelectorAll(".assistant-card").forEach((card) => {
    card.addEventListener("click", () => {
      const tool = state.assistantTools.find((t) => t.id === card.dataset.toolId);
      if (tool && tool.available) openAssistantToolModal(tool);
    });
  });
}

document.getElementById("assistant-mode-tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".assistant-tab");
  if (!btn) return;
  document.querySelectorAll("#assistant-mode-tabs .assistant-tab").forEach((b) => b.classList.toggle("active", b === btn));
  state.assistantMode = btn.dataset.mode;
  renderAssistantGrid();
});

document.getElementById("assistant-category-filters").addEventListener("click", (e) => {
  const btn = e.target.closest(".assistant-filter");
  if (!btn) return;
  document.querySelectorAll("#assistant-category-filters .assistant-filter").forEach((b) => b.classList.toggle("active", b === btn));
  state.assistantCategory = btn.dataset.category;
  renderAssistantGrid();
});

function assistantFieldHtml(field) {
  const req = field.required ? "required" : "";
  if (field.type === "textarea") {
    return `
      <div class="assistant-field">
        <label>${escapeHtml(field.label)}${field.required ? " *" : ""}</label>
        <textarea data-field-key="${field.key}" rows="4" placeholder="${escapeHtml(field.placeholder)}" ${req}></textarea>
      </div>`;
  }
  if (field.type === "number") {
    return `
      <div class="assistant-field">
        <label>${escapeHtml(field.label)}${field.required ? " *" : ""}</label>
        <input type="number" data-field-key="${field.key}" placeholder="${escapeHtml(field.placeholder)}" ${req}>
      </div>`;
  }
  if (field.type === "image") {
    return `
      <div class="assistant-field">
        <label>${escapeHtml(field.label)}${field.required ? " *" : ""}</label>
        <input type="hidden" data-field-key="${field.key}" value="">
        <div class="assistant-dropzone" data-image-field="${field.key}">
          <div>⬆ Перетащите изображение или нажмите, чтобы выбрать файл</div>
        </div>
        <div class="assistant-image-preview" data-image-preview="${field.key}" hidden></div>
        <input type="file" accept="image/*" hidden data-image-input="${field.key}">
      </div>`;
  }
  return `
    <div class="assistant-field">
      <label>${escapeHtml(field.label)}${field.required ? " *" : ""}</label>
      <input type="text" data-field-key="${field.key}" placeholder="${escapeHtml(field.placeholder)}" ${req}>
    </div>`;
}

let currentAssistantTool = null;

function openAssistantToolModal(tool) {
  currentAssistantTool = tool;
  document.getElementById("assistant-tool-title").textContent = `${tool.icon} ${tool.title}`;
  document.getElementById("assistant-tool-description").textContent = tool.description;
  document.getElementById("assistant-tool-fields").innerHTML = tool.fields.map(assistantFieldHtml).join("");
  document.getElementById("assistant-tool-provider").value = "";
  document.getElementById("assistant-tool-result-box").hidden = true;
  document.getElementById("assistant-tool-result").innerHTML = "";

  document.querySelectorAll('#assistant-tool-fields [data-image-field]').forEach((zone) => {
    const key = zone.dataset.imageField;
    const fileInput = document.querySelector(`[data-image-input="${key}"]`);
    const hiddenInput = document.querySelector(`[data-field-key="${key}"]`);
    const preview = document.querySelector(`[data-image-preview="${key}"]`);

    const handleFile = async (file) => {
      try {
        toast("Загрузка изображения...");
        const formData = new FormData();
        formData.append("file", file);
        const res = await fetch("/api/content/upload-media", { method: "POST", body: formData });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Ошибка загрузки");
        const { url } = await res.json();
        hiddenInput.value = url;
        preview.innerHTML = `<img src="${url}" alt="">`;
        preview.hidden = false;
        toast("Изображение загружено");
      } catch (err) {
        toast(err.message, true);
      }
    };

    zone.addEventListener("click", () => fileInput.click());
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
      const file = e.dataTransfer.files?.[0];
      if (file) handleFile(file);
    });
    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      if (file) handleFile(file);
      fileInput.value = "";
    });
  });

  document.getElementById("modal-assistant-tool").classList.add("active");
}

document.getElementById("btn-close-assistant-tool").addEventListener("click", () => {
  document.getElementById("modal-assistant-tool").classList.remove("active");
});

document.getElementById("btn-run-assistant-tool").addEventListener("click", async () => {
  if (!currentAssistantTool) return;
  const inputs = {};
  document.querySelectorAll("#assistant-tool-fields [data-field-key]").forEach((el) => {
    inputs[el.dataset.fieldKey] = el.value;
  });
  const missing = currentAssistantTool.fields.filter((f) => f.required && !String(inputs[f.key] || "").trim());
  if (missing.length) {
    toast(`Заполните обязательные поля: ${missing.map((f) => f.label).join(", ")}`, true);
    return;
  }

  const btn = document.getElementById("btn-run-assistant-tool");
  const provider = document.getElementById("assistant-tool-provider").value || null;
  btn.disabled = true;
  btn.textContent = "Генерация...";
  try {
    const data = await api("/api/ai/assistant/run", {
      method: "POST",
      body: JSON.stringify({ tool_id: currentAssistantTool.id, inputs, provider }),
    });
    const resultBox = document.getElementById("assistant-tool-result-box");
    const resultEl = document.getElementById("assistant-tool-result");
    if (data.image_url) {
      resultEl.innerHTML = `<img src="${data.image_url}" alt="">`;
    } else {
      resultEl.textContent = data.result || "";
    }
    resultBox.hidden = false;
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "✨ Сгенерировать";
  }
});

document.getElementById("btn-copy-assistant-result").addEventListener("click", async () => {
  const resultEl = document.getElementById("assistant-tool-result");
  const img = resultEl.querySelector("img");
  const text = img ? img.src : resultEl.textContent;
  try {
    await navigator.clipboard.writeText(text);
    toast("Скопировано");
  } catch (e) {
    toast("Не удалось скопировать", true);
  }
});

document.getElementById("btn-use-assistant-result").addEventListener("click", () => {
  const resultEl = document.getElementById("assistant-tool-result");
  const img = resultEl.querySelector("img");
  document.getElementById("modal-assistant-tool").classList.remove("active");
  openContentModal(null);
  if (img) {
    document.getElementById("content-media").value = img.src;
    setMediaPreview(img.src);
  } else {
    document.getElementById("content-body").value = resultEl.textContent;
    updateCharCounter();
  }
  if (currentAssistantTool) {
    document.getElementById("content-title").value = currentAssistantTool.title;
  }
});

// ---------- Modals: закрытие по клику на фон и по Escape ----------
document.querySelectorAll(".modal").forEach((modal) => {
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.remove("active");
  });
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  document.querySelectorAll(".modal.active").forEach((modal) => modal.classList.remove("active"));
});

// ---------- Init ----------
loadDashboard();
