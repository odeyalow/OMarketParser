const searchInput = document.getElementById("search-query-input");
const maxPagesInput = document.getElementById("max-pages-input");
const allPagesButton = document.getElementById("all-pages-button");
const runButton = document.getElementById("run-parser-button");
const stopButton = document.getElementById("stop-parser-button");
const resetButton = document.getElementById("reset-parser-button");
const statusText = document.getElementById("status-text");
const progressText = document.getElementById("progress-text");
const resultsSummary = document.getElementById("results-summary");
const exportExcelButton = document.getElementById("export-excel-button");
const currentProductBox = document.getElementById("current-product-box");
const currentProductLink = document.getElementById("current-product-link");
const emptyState = document.getElementById("empty-state");
const resultsTableWrap = document.getElementById("results-table-wrap");
const resultsBody = document.getElementById("results-body");
const resultsPagination = document.getElementById("results-pagination");
const resultsPrevButton = document.getElementById("results-prev-button");
const resultsNextButton = document.getElementById("results-next-button");
const resultsPageIndicator = document.getElementById("results-page-indicator");
const finishModal = document.getElementById("finish-modal");
const finishModalTitle = document.getElementById("finish-modal-title");
const finishModalText = document.getElementById("finish-modal-text");
const finishModalQuery = document.getElementById("finish-modal-query");
const finishModalPages = document.getElementById("finish-modal-pages");
const finishModalProducts = document.getElementById("finish-modal-products");
const finishModalRows = document.getElementById("finish-modal-rows");
const finishModalClose = document.getElementById("finish-modal-close");

const ROWS_PER_PAGE = 15;
const ACTIVE_STATUSES = new Set(["running", "stopping"]);
const FINAL_STATUSES = new Set(["completed", "stopped", "error"]);

let pollTimer = null;
let allPagesMode = true;
let currentResultsPage = 1;
let lastQuery = "";
let lastRowCount = 0;
let latestRows = [];
let hasInitialState = false;
let previousStatus = "idle";
let lastCompletionSignature = "";

function escapeHtml(value) {
  return String(value).replace(/[&<>\"']/g, (symbol) => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[symbol];
  });
}

function isActiveStatus(status) {
  return ACTIVE_STATUSES.has(status);
}

function isFinalStatus(status) {
  return FINAL_STATUSES.has(status);
}

function getResultsPageCount(rows) {
  return Math.max(1, Math.ceil(rows.length / ROWS_PER_PAGE));
}

function updateResultsPagination(rows) {
  const totalPages = getResultsPageCount(rows);
  currentResultsPage = Math.min(currentResultsPage, totalPages);
  currentResultsPage = Math.max(currentResultsPage, 1);

  if (!rows.length || totalPages === 1) {
    resultsPagination.classList.add("hidden");
  } else {
    resultsPagination.classList.remove("hidden");
  }

  resultsPrevButton.disabled = currentResultsPage === 1 || !rows.length;
  resultsNextButton.disabled = currentResultsPage === totalPages || !rows.length;
  resultsPageIndicator.textContent = `Страница ${currentResultsPage} из ${totalPages}`;
}

function renderRows(rows) {
  if (!rows.length) {
    currentResultsPage = 1;
    resultsTableWrap.classList.add("hidden");
    resultsPagination.classList.add("hidden");
    emptyState.classList.remove("hidden");
    resultsPageIndicator.textContent = "Страница 1 из 1";
    resultsBody.innerHTML = "";
    return;
  }

  emptyState.classList.add("hidden");
  resultsTableWrap.classList.remove("hidden");
  updateResultsPagination(rows);

  const startIndex = (currentResultsPage - 1) * ROWS_PER_PAGE;
  const visibleRows = rows.slice(startIndex, startIndex + ROWS_PER_PAGE);

  resultsBody.innerHTML = visibleRows
    .map((row) => {
      const safeProductName = escapeHtml(row.product_name);
      const safeProductUrl = escapeHtml(row.product_url);
      const safeKztin = escapeHtml(row.product_kztin || "-");
      const safeEnstruCode = escapeHtml(row.enstru_code);
      const safeEnstruName = escapeHtml(row.enstru_name);

      return `
        <tr>
          <td>
            <a class="product-link" href="${safeProductUrl}" target="_blank" rel="noreferrer">
              ${safeProductName}
            </a>
          </td>
          <td>${safeKztin}</td>
          <td>${safeEnstruCode}</td>
          <td>${safeEnstruName}</td>
        </tr>
      `;
    })
    .join("");
}

function renderCurrentProduct(state) {
  if (!state.current_product_url) {
    currentProductBox.classList.add("hidden");
    currentProductLink.textContent = "";
    currentProductLink.href = "#";
    return;
  }

  currentProductBox.classList.remove("hidden");
  currentProductLink.href = state.current_product_url;
  currentProductLink.textContent = state.current_product_preview || state.current_product_url;
}

function setAllPagesMode(enabled) {
  allPagesMode = enabled;
  allPagesButton.classList.toggle("is-active", enabled);
  allPagesButton.setAttribute("aria-pressed", String(enabled));
  if (enabled) {
    maxPagesInput.value = "";
    maxPagesInput.removeAttribute("aria-invalid");
  }
}

function syncResultsPager(state) {
  if (!state.results?.length) {
    currentResultsPage = 1;
  }

  if (state.query !== lastQuery || state.row_count < lastRowCount) {
    currentResultsPage = 1;
  }

  lastQuery = state.query;
  lastRowCount = state.row_count;
}

function formatPagesLabel(state) {
  if (!state.query) {
    return "0";
  }

  if (state.max_pages) {
    return `${state.visited_pages} из ${state.max_pages}`;
  }

  return `${state.visited_pages} из всех`;
}

function closeFinishModal() {
  finishModal.classList.add("hidden");
  document.body.classList.remove("modal-open");
}

function openFinishModal(state) {
  let title = "Парсинг завершен";
  let text = state.message || "Работа парсера завершена.";

  if (state.status === "stopped") {
    title = "Парсер остановлен";
  } else if (state.status === "error") {
    title = "Ошибка парсинга";
  }

  finishModalTitle.textContent = title;
  finishModalText.textContent = text;
  finishModalQuery.textContent = state.query || "-";
  finishModalPages.textContent = formatPagesLabel(state);
  finishModalProducts.textContent = `${state.processed_products} / ${state.total_products || 0}`;
  finishModalRows.textContent = String(state.row_count || 0);

  finishModal.classList.remove("hidden");
  document.body.classList.add("modal-open");
}

function maybeShowFinishModal(state) {
  const completionSignature = [
    state.status,
    state.query,
    state.visited_pages,
    state.processed_products,
    state.row_count,
    state.message,
  ].join("|");

  if (
    hasInitialState &&
    isActiveStatus(previousStatus) &&
    isFinalStatus(state.status) &&
    completionSignature !== lastCompletionSignature
  ) {
    openFinishModal(state);
    lastCompletionSignature = completionSignature;
  }

  previousStatus = state.status;
  hasInitialState = true;
}

function renderState(state) {
  const active = isActiveStatus(state.status);
  const stopping = state.status === "stopping";

  statusText.textContent = state.message || "Нет данных.";
  progressText.textContent = state.query
    ? `Страницы: ${formatPagesLabel(state)}. Обработано товаров: ${state.processed_products} из ${state.total_products}. Сохранено строк: ${state.row_count}.`
    : "Пока нет сохраненных строк.";
  resultsSummary.textContent = state.query
    ? `Запрос: ${state.query}. Лимит страниц: ${state.max_pages || "все"}.`
    : "Данных пока нет.";

  searchInput.disabled = active;
  maxPagesInput.disabled = active;
  allPagesButton.disabled = active;
  runButton.disabled = active;
  stopButton.disabled = !active;
  resetButton.disabled = active;
  stopButton.textContent = stopping ? "Остановка..." : "Остановить";
  exportExcelButton.disabled = !(state.results && state.results.length);

  latestRows = state.results || [];
  syncResultsPager(state);
  renderCurrentProduct(state);
  renderRows(latestRows);
  maybeShowFinishModal(state);
}

async function refreshState() {
  try {
    const response = await fetch("/api/status");
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      return;
    }
    renderState(payload.data);
  } catch (error) {
    console.error(error);
  }
}

async function runParser() {
  const query = searchInput.value.trim();
  if (!query) {
    searchInput.setAttribute("aria-invalid", "true");
    searchInput.focus();
    return;
  }

  let maxPages = null;
  if (!allPagesMode) {
    maxPages = Number.parseInt(maxPagesInput.value.trim(), 10);
    if (!Number.isInteger(maxPages) || maxPages < 1) {
      maxPagesInput.setAttribute("aria-invalid", "true");
      maxPagesInput.focus();
      return;
    }
  }

  searchInput.removeAttribute("aria-invalid");
  maxPagesInput.removeAttribute("aria-invalid");
  currentResultsPage = 1;
  lastCompletionSignature = "";
  closeFinishModal();

  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ query, max_pages: maxPages }),
    });
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "Не удалось запустить парсер.");
    }

    renderState(payload.data);
  } catch (error) {
    statusText.textContent = error.message;
  }
}

async function stopParser() {
  try {
    const response = await fetch("/api/stop", {
      method: "POST",
    });
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "Не удалось остановить парсер.");
    }

    renderState(payload.data);
  } catch (error) {
    statusText.textContent = error.message;
  }
}

async function resetParser() {
  closeFinishModal();

  try {
    const response = await fetch("/api/reset", {
      method: "POST",
    });
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "Не удалось сбросить данные парсера.");
    }

    searchInput.value = "";
    maxPagesInput.value = "";
    setAllPagesMode(true);
    currentResultsPage = 1;
    lastQuery = "";
    lastRowCount = 0;
    latestRows = [];
    previousStatus = "idle";
    lastCompletionSignature = "";
    renderState(payload.data);
  } catch (error) {
    statusText.textContent = error.message;
  }
}

function getDownloadFilename(disposition) {
  if (!disposition) {
    return "omarket-rezultaty.xlsx";
  }

  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    return decodeURIComponent(utf8Match[1]);
  }

  const asciiMatch = disposition.match(/filename=\"?([^\";]+)\"?/i);
  if (asciiMatch) {
    return asciiMatch[1];
  }

  return "omarket-rezultaty.xlsx";
}

async function exportExcel() {
  try {
    const response = await fetch("/api/export.xlsx");
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new Error(payload?.error || "Не удалось выгрузить Excel.");
    }

    const blob = await response.blob();
    const filename = getDownloadFilename(response.headers.get("Content-Disposition"));
    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(downloadUrl);
  } catch (error) {
    statusText.textContent = error.message;
  }
}

runButton.addEventListener("click", runParser);
stopButton.addEventListener("click", stopParser);
resetButton.addEventListener("click", resetParser);
exportExcelButton.addEventListener("click", exportExcel);
resultsPrevButton.addEventListener("click", () => {
  currentResultsPage = Math.max(currentResultsPage - 1, 1);
  renderRows(latestRows);
});
resultsNextButton.addEventListener("click", () => {
  currentResultsPage = Math.min(currentResultsPage + 1, getResultsPageCount(latestRows));
  renderRows(latestRows);
});
allPagesButton.addEventListener("click", () => {
  setAllPagesMode(true);
});
finishModalClose.addEventListener("click", closeFinishModal);
finishModal.addEventListener("click", (event) => {
  if (event.target === finishModal) {
    closeFinishModal();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !finishModal.classList.contains("hidden")) {
    closeFinishModal();
  }
});
searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    runParser();
  }
});
maxPagesInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    runParser();
  }
});
searchInput.addEventListener("input", () => {
  searchInput.removeAttribute("aria-invalid");
});
maxPagesInput.addEventListener("input", () => {
  maxPagesInput.removeAttribute("aria-invalid");
  if (maxPagesInput.value.trim()) {
    setAllPagesMode(false);
    return;
  }
  setAllPagesMode(true);
});

pollTimer = window.setInterval(refreshState, 1000);
refreshState();
