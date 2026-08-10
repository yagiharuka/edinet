"use strict";

const METRICS = {
  revenue: { label: "売上高等", group: "業績" },
  operating_profit: { label: "営業利益", group: "業績" },
  ordinary_profit: { label: "経常利益", group: "業績" },
  net_income: { label: "当期純利益", group: "業績" },
  total_assets: { label: "総資産", group: "財政状態" },
  equity: { label: "純資産・資本", group: "財政状態" },
  operating_cash_flow: { label: "営業キャッシュフロー", group: "キャッシュフロー" },
  investing_cash_flow: { label: "投資キャッシュフロー", group: "キャッシュフロー" },
  financing_cash_flow: { label: "財務キャッシュフロー", group: "キャッシュフロー" },
  free_cash_flow: { label: "フリーキャッシュフロー", group: "キャッシュフロー" },
  research_and_development: { label: "研究開発費", group: "参考" },
  eps: { label: "1株当たり利益", group: "1株指標" },
  diluted_eps: { label: "希薄化後1株当たり利益", group: "1株指標" },
  operating_margin: { label: "営業利益率", group: "比率" },
  equity_ratio: { label: "自己資本比率（簡易）", group: "比率" },
};

const METRIC_ORDER = Object.keys(METRICS);
const COMPANY_COLORS = ["#0b4f79", "#08705a", "#9a5b17", "#76508c"];
const COMPANY_DASHES = ["", "10 6", "3 5", "13 5 3 5"];
const PAGE_SIZE = 40;
const COMPARE_STORAGE_KEY = "edinet-financial-viewer:compare";

const state = {
  payload: null,
  master: null,
  companies: [],
  companyById: new Map(),
  query: "",
  industry: "",
  standard: "",
  dataFilter: "available",
  sort: "ticker",
  visibleCount: PAGE_SIZE,
  route: "home",
  companyId: "",
  chartMetric: "revenue",
  compareMetric: "revenue",
  compare: new Set(readStoredCompare()),
  isDemo: false,
};

const elements = {
  globalStatus: document.querySelector("#global-status"),
  globalStatusText: document.querySelector("#global-status-text"),
  setupBanner: document.querySelector("#setup-banner"),
  syncWarning: document.querySelector("#sync-warning"),
  summaryCompanies: document.querySelector("#summary-companies"),
  summaryPeriods: document.querySelector("#summary-periods"),
  summaryDate: document.querySelector("#summary-date"),
  searchForm: document.querySelector("#search-form"),
  searchInput: document.querySelector("#company-search"),
  results: document.querySelector("#company-results"),
  resultsSummary: document.querySelector("#results-summary"),
  loadMore: document.querySelector("#load-more"),
  industryFilter: document.querySelector("#industry-filter"),
  standardFilter: document.querySelector("#standard-filter"),
  dataFilter: document.querySelector("#data-filter"),
  sortFilter: document.querySelector("#sort-filter"),
  companyContent: document.querySelector("#company-content"),
  compareContent: document.querySelector("#compare-content"),
  aboutContent: document.querySelector("#about-content"),
  compareDock: document.querySelector("#compare-dock"),
  compareChips: document.querySelector("#compare-chips"),
  compareCount: document.querySelector("#nav-compare-count"),
  openCompare: document.querySelector("#open-compare"),
  liveRegion: document.querySelector("#live-region"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizeText(value) {
  return String(value ?? "")
    .normalize("NFKC")
    .toLocaleLowerCase("ja")
    .replace(/株式会社|（株）|\(株\)|有限会社/g, "")
    .replace(/[\s・･.,，。\-ー_]/g, "");
}

function readStoredCompare() {
  try {
    const parsed = JSON.parse(localStorage.getItem(COMPARE_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.slice(0, 4) : [];
  } catch {
    return [];
  }
}

function storeCompare() {
  try {
    localStorage.setItem(COMPARE_STORAGE_KEY, JSON.stringify([...state.compare]));
  } catch {
    // Local storage is an enhancement only.
  }
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function loadData() {
  const query = new URLSearchParams(location.search);
  const localHost = ["localhost", "127.0.0.1", "terminal.local"].includes(location.hostname);
  state.isDemo = localHost && query.get("demo") === "1";
  const financialUrl = state.isDemo ? "tests/fixtures/site_data.json" : "data/financials.json";
  const [financialResult, masterResult] = await Promise.allSettled([
    fetchJson(financialUrl),
    fetchJson("data/company_master.json"),
  ]);
  if (financialResult.status === "rejected") {
    throw new Error(`財務データを読み込めませんでした: ${financialResult.reason.message}`);
  }
  state.payload = financialResult.value;
  state.master = masterResult.status === "fulfilled" ? masterResult.value : { companies: [] };
  mergeCompanies();
  updateDataSummary();
  populateFilters();
  parseRouteFromUrl();
  renderRoute({ moveFocus: false });
}

function mergeCompanies() {
  const financialCompanies = state.payload?.companies || [];
  const financialByTicker = new Map(
    financialCompanies.filter((company) => company.ticker).map((company) => [String(company.ticker), company]),
  );
  const masterCompanies = state.master?.companies || [];
  const merged = [];
  const seen = new Set();

  for (const master of masterCompanies) {
    const ticker = String(master.ticker || "");
    const financial = financialByTicker.get(ticker);
    const company = {
      ...master,
      ...(financial || {}),
      hasData: Boolean(financial?.periods?.length),
    };
    company.id = company.edinet_code || company.ticker;
    if (!company.id) continue;
    company.searchText = normalizeText(
      [company.name, company.name_kana, company.name_en, company.ticker, company.sec_code, company.edinet_code].join(" "),
    );
    merged.push(company);
    seen.add(company.id);
  }

  for (const financial of financialCompanies) {
    const id = financial.edinet_code || financial.ticker;
    if (!id || seen.has(id)) continue;
    merged.push({
      ...financial,
      id,
      hasData: Boolean(financial.periods?.length),
      searchText: normalizeText(
        [financial.name, financial.name_kana, financial.name_en, financial.ticker, financial.sec_code, financial.edinet_code].join(" "),
      ),
    });
  }

  merged.sort((a, b) => String(a.ticker || "99999").localeCompare(String(b.ticker || "99999"), "ja"));
  state.companies = merged;
  state.companyById = new Map();
  for (const company of merged) {
    state.companyById.set(String(company.id), company);
    if (company.edinet_code) state.companyById.set(String(company.edinet_code), company);
    if (company.ticker) state.companyById.set(String(company.ticker), company);
  }
  state.compare = new Set([...state.compare].filter((id) => state.companyById.get(id)?.hasData).slice(0, 4));
  if (!financialCompanies.length) state.dataFilter = "all";
  elements.dataFilter.value = state.dataFilter;
}

function updateDataSummary() {
  const meta = state.payload?.meta || {};
  const coverage = meta.coverage || {};
  const companyCount = Number(coverage.companies ?? state.payload?.companies?.length ?? 0);
  const periodCount = Number(
    coverage.periods ?? (state.payload?.companies || []).reduce((sum, company) => sum + (company.periods?.length || 0), 0),
  );
  elements.summaryCompanies.textContent = companyCount.toLocaleString("ja-JP");
  elements.summaryPeriods.textContent = periodCount.toLocaleString("ja-JP");
  elements.summaryDate.textContent = meta.generated_at ? formatDateTime(meta.generated_at, true) : "初回取得前";

  const setupRequired = meta.status === "setup_required" || companyCount === 0;
  const warnings = Array.isArray(meta.warnings) ? meta.warnings : [];
  const partial = meta.status === "partial" || warnings.length > 0 || Boolean(meta.master_refresh_error);
  elements.setupBanner.hidden = !setupRequired;
  elements.syncWarning.hidden = setupRequired || !partial;
  elements.globalStatus.classList.toggle("is-setup", setupRequired);
  elements.globalStatus.classList.toggle("is-stale", partial);

  if (state.isDemo) {
    elements.globalStatusText.textContent = "ローカル表示確認用のデモデータです";
    elements.globalStatus.classList.add("is-setup");
    return;
  }
  if (setupRequired) {
    elements.globalStatusText.textContent = "企業名簿を準備中・財務データは初回取得前です";
    return;
  }
  const generated = meta.generated_at ? new Date(meta.generated_at) : null;
  const stale = generated && Date.now() - generated.getTime() > 4 * 24 * 60 * 60 * 1000;
  elements.globalStatus.classList.toggle("is-stale", Boolean(stale || partial));
  const statusNote = partial ? "（一部取得失敗・前回値を保持）" : stale ? "（更新が遅れています）" : "";
  elements.globalStatusText.textContent = `${formatDateTime(meta.generated_at, true)}更新 ・ ${companyCount.toLocaleString("ja-JP")}社を収録${statusNote}`;
}

function populateFilters() {
  const industries = [...new Set(state.companies.map((company) => company.industry).filter(Boolean))].sort((a, b) =>
    String(a).localeCompare(String(b), "ja"),
  );
  elements.industryFilter.innerHTML = '<option value="">すべての業種</option>' + industries
    .map((industry) => `<option value="${escapeHtml(industry)}">${escapeHtml(industry)}</option>`)
    .join("");
}

function parseRouteFromUrl() {
  const params = new URLSearchParams(location.search);
  if (params.get("company")) {
    state.route = "company";
    state.companyId = params.get("company");
  } else if (params.get("compare")) {
    state.route = "compare";
    const ids = params
      .get("compare")
      .split(",")
      .map((value) => value.trim())
      .filter((id) => state.companyById.get(id)?.hasData)
      .slice(0, 4);
    if (ids.length) state.compare = new Set(ids);
  } else if (params.get("about") === "1") {
    state.route = "about";
  } else {
    state.route = "home";
  }
  storeCompare();
}

function navigate(route, value = "") {
  state.route = route;
  if (route === "company") state.companyId = value;
  const url = new URL(location.href);
  url.searchParams.delete("company");
  url.searchParams.delete("compare");
  url.searchParams.delete("about");
  if (route === "company") url.searchParams.set("company", value);
  if (route === "compare" && state.compare.size) url.searchParams.set("compare", [...state.compare].join(","));
  if (route === "about") url.searchParams.set("about", "1");
  history.pushState({}, "", url);
  renderRoute({ moveFocus: true });
}

function renderRoute({ moveFocus = false } = {}) {
  for (const view of document.querySelectorAll(".view")) view.hidden = true;
  const view = document.querySelector(`#view-${state.route}`) || document.querySelector("#view-home");
  view.hidden = false;
  for (const button of document.querySelectorAll(".nav-button")) {
    button.classList.toggle("is-current", button.dataset.route === state.route);
  }

  if (state.route === "home") renderHome();
  if (state.route === "company") renderCompany();
  if (state.route === "compare") renderCompare();
  if (state.route === "about") renderAbout();
  renderCompareDock();

  if (moveFocus) {
    window.scrollTo({ top: 0, behavior: "auto" });
    document.querySelector("#main-content")?.focus({ preventScroll: true });
  }
}

function filteredCompanies() {
  const query = normalizeText(state.query);
  const items = state.companies.filter((company) => {
    if (query && !company.searchText.includes(query)) return false;
    if (state.industry && company.industry !== state.industry) return false;
    if (state.standard && company.accounting_standard !== state.standard) return false;
    if (state.dataFilter === "available" && !company.hasData) return false;
    return true;
  });

  const compareNumber = (company, metricKey) => {
    const fact = latestPeriod(company)?.metrics?.[metricKey];
    return fact && Number.isFinite(Number(fact.value)) ? Number(fact.value) : Number.NEGATIVE_INFINITY;
  };
  items.sort((a, b) => {
    if (state.sort === "name") return String(a.name || "").localeCompare(String(b.name || ""), "ja");
    if (state.sort === "revenue") return compareNumber(b, "revenue") - compareNumber(a, "revenue");
    if (state.sort === "margin") return compareNumber(b, "operating_margin") - compareNumber(a, "operating_margin");
    return String(a.ticker || "99999").localeCompare(String(b.ticker || "99999"), "ja");
  });
  return items;
}

function renderHome() {
  document.title = "EDINET 財務ダッシュボード";
  elements.searchInput.value = state.query;
  elements.industryFilter.value = state.industry;
  elements.standardFilter.value = state.standard;
  elements.dataFilter.value = state.dataFilter;
  elements.sortFilter.value = state.sort;
  renderResults();
}

function renderResults() {
  const companies = filteredCompanies();
  const visible = companies.slice(0, state.visibleCount);
  const hasQueryOrFilter = Boolean(state.query || state.industry || state.standard);
  elements.resultsSummary.textContent = `${companies.length.toLocaleString("ja-JP")}社${hasQueryOrFilter ? "が条件に一致" : "を表示できます"}`;
  elements.results.setAttribute("aria-busy", "false");

  if (!companies.length) {
    const title = state.payload?.companies?.length ? "条件に合う会社がありません" : "財務データは初回取得前です";
    const body = state.payload?.companies?.length
      ? "検索語や絞り込み条件を変えてお試しください。欠損データは0として表示していません。"
      : "初回同期後、主要企業の直近5期がここに表示されます。企業名簿が取得済みの場合は「収録状況」を「企業名簿すべて」に変えると会社を探せます。";
    elements.results.innerHTML = `
      <div class="empty-state">
        <div><div class="empty-state-mark" aria-hidden="true">—</div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(body)}</p></div>
      </div>`;
    elements.loadMore.hidden = true;
    return;
  }

  elements.results.innerHTML = `
    <div class="company-table-wrap">
      <table class="company-table">
        <thead><tr>
          <th scope="col">会社</th>
          <th scope="col">最新決算期</th>
          <th scope="col" class="number-cell">売上高等</th>
          <th scope="col" class="number-cell">営業利益</th>
          <th scope="col"><span class="sr-only">操作</span></th>
        </tr></thead>
        <tbody>${visible.map(renderCompanyRow).join("")}</tbody>
      </table>
    </div>
    <div class="company-card-list">${visible.map(renderCompanyCard).join("")}</div>`;
  elements.loadMore.hidden = visible.length >= companies.length;
}

function renderCompanyRow(company) {
  const latest = latestPeriod(company);
  const selected = state.compare.has(String(company.id));
  return `<tr>
    <td class="company-cell">
      <button class="company-name-button" type="button" data-company-id="${escapeHtml(company.id)}">${escapeHtml(company.name || "名称不明")}</button>
      <div class="company-meta">
        ${company.ticker ? `<span>証券 ${escapeHtml(company.ticker)}</span>` : ""}
        ${company.industry ? `<span>${escapeHtml(company.industry)}</span>` : ""}
        ${company.accounting_standard ? `<span class="badge">${escapeHtml(company.accounting_standard)}</span>` : ""}
        ${company.hasData ? "" : '<span class="badge badge-muted">財務未収録</span>'}
      </div>
    </td>
    <td>${latest ? formatFiscalYear(latest.period_end) : '<span class="missing">未収録</span>'}</td>
    <td class="number-cell">${renderNumber(latest?.metrics?.revenue)}</td>
    <td class="number-cell">${renderNumber(latest?.metrics?.operating_profit)}</td>
    <td class="action-cell">
      <button class="small-button" type="button" data-company-id="${escapeHtml(company.id)}">見る</button>
      <button class="small-button${selected ? " is-selected" : ""}" type="button" data-compare-id="${escapeHtml(company.id)}" ${company.hasData ? "" : "disabled"}>${selected ? "追加済み" : "比較に追加"}</button>
    </td>
  </tr>`;
}

function renderCompanyCard(company) {
  const latest = latestPeriod(company);
  const selected = state.compare.has(String(company.id));
  return `<article class="company-result-card">
    <button class="company-name-button" type="button" data-company-id="${escapeHtml(company.id)}">${escapeHtml(company.name || "名称不明")}</button>
    <div class="company-meta">
      ${company.ticker ? `<span>証券 ${escapeHtml(company.ticker)}</span>` : ""}
      ${company.industry ? `<span>${escapeHtml(company.industry)}</span>` : ""}
      ${company.accounting_standard ? `<span class="badge">${escapeHtml(company.accounting_standard)}</span>` : ""}
    </div>
    <div class="company-result-numbers">
      <div class="company-result-number"><span>売上高等</span><strong>${formatValue(latest?.metrics?.revenue)}</strong></div>
      <div class="company-result-number"><span>営業利益</span><strong>${formatValue(latest?.metrics?.operating_profit)}</strong></div>
    </div>
    <div class="card-actions">
      <button class="small-button" type="button" data-company-id="${escapeHtml(company.id)}">詳細を見る</button>
      <button class="small-button${selected ? " is-selected" : ""}" type="button" data-compare-id="${escapeHtml(company.id)}" ${company.hasData ? "" : "disabled"}>${selected ? "追加済み" : "比較に追加"}</button>
    </div>
  </article>`;
}

function latestPeriod(company) {
  const periods = [...(company?.periods || [])].sort((a, b) => String(a.period_end).localeCompare(String(b.period_end)));
  return periods.at(-1) || null;
}

function orderedPeriods(company) {
  return [...(company?.periods || [])].sort((a, b) => String(a.period_end).localeCompare(String(b.period_end)));
}

function periodLengthDays(period) {
  if (!period?.period_start || !period?.period_end) return null;
  const start = new Date(`${period.period_start}T00:00:00Z`);
  const end = new Date(`${period.period_end}T00:00:00Z`);
  const days = Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1;
  return Number.isFinite(days) && days > 0 ? days : null;
}

function isRegularAnnualPeriod(period) {
  const days = periodLengthDays(period);
  return days !== null && days >= 350 && days <= 380;
}

function periodRangeLabel(period) {
  if (!period?.period_end) return "期間不明";
  const days = periodLengthDays(period);
  const range = period.period_start ? `${period.period_start}〜${period.period_end}` : period.period_end;
  return `${range}${days ? `（${days}日）` : ""}`;
}

function renderNumber(metric) {
  if (!metric || metric.value === null || metric.value === undefined) return '<span class="missing">記載なし</span>';
  const formatted = formatValue(metric);
  return `<span class="number-main">${escapeHtml(formatted)}</span>${metric.label ? `<span class="number-sub" title="${escapeHtml(metric.label)}">${escapeHtml(shortLabel(metric.label))}</span>` : ""}`;
}

function shortLabel(label) {
  const value = String(label || "");
  return value.length > 18 ? `${value.slice(0, 18)}…` : value;
}

function filingUrl(docId, suppliedUrl = "") {
  if (suppliedUrl) return suppliedUrl;
  return docId ? `https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?${encodeURIComponent(docId)}` : "https://disclosure2.edinet-fsa.go.jp/";
}

function metricSources(metric) {
  const sources = Array.isArray(metric?.source_documents) ? metric.source_documents : [];
  if (sources.length) return sources.filter((source) => source?.doc_id);
  if (!metric?.source_doc_id) return [];
  return [
    {
      doc_id: metric.source_doc_id,
      submitted_at: metric.source_submitted_at,
      description: metric.source_description,
      url: metric.source_url,
    },
  ];
}

function renderMetricSources(metric) {
  const sources = metricSources(metric);
  if (!sources.length) return "";
  const context = [metric.scope === "separate" ? "単体" : metric.scope === "consolidated" ? "連結" : "", metric.accounting_standard || ""]
    .filter(Boolean)
    .join("・");
  return `<span class="metric-sources">${sources
    .map(
      (source) => `<a href="${escapeHtml(filingUrl(source.doc_id, source.url))}" target="_blank" rel="noreferrer" title="${escapeHtml(`${source.description || "EDINET開示書類"}${source.submitted_at ? `・${source.submitted_at}` : ""}${context ? `・${context}` : ""}`)}">原本 ${escapeHtml(source.doc_id)} ↗</a>`,
    )
    .join("")}</span>`;
}

function formatValue(metric, { axis = false } = {}) {
  if (!metric || metric.value === null || metric.value === undefined || metric.value === "") return "—";
  const value = Number(metric.value);
  if (!Number.isFinite(value)) return "—";
  const unit = metric.unit || "";
  if (unit === "PERCENT") {
    const digits = axis ? nonZeroPrecision(value, 0, 3) : nonZeroPrecision(value, 1, 4);
    return `${formatNumber(value, digits)}%`;
  }
  if (unit === "JPY_PER_SHARE") {
    const absolute = Math.abs(value);
    return `${value < 0 ? "−" : ""}¥${formatNumber(absolute, nonZeroPrecision(absolute, 2, 6))}`;
  }
  if (unit === "JPY") {
    const absolute = Math.abs(value);
    const sign = value < 0 ? "−" : "";
    if (absolute >= 100_000_000) {
      const oku = absolute / 100_000_000;
      const digits = oku >= 1_000 ? 0 : oku >= 100 ? 1 : 2;
      return `${sign}${formatNumber(oku, digits)}億円`;
    }
    if (absolute >= 10_000) {
      const man = absolute / 10_000;
      const digits = man >= 1_000 ? 0 : man >= 100 ? 1 : 2;
      return `${sign}${formatNumber(man, digits)}万円`;
    }
    return `${sign}${formatNumber(absolute, nonZeroPrecision(absolute, 0, 4))}円`;
  }
  if (unit === "USD") return `${value < 0 ? "−" : ""}${formatNumber(Math.abs(value), 0)} USD`;
  if (unit === "SHARES") return `${value < 0 ? "−" : ""}${formatNumber(Math.abs(value), 0)}株`;
  return `${value < 0 ? "−" : ""}${formatNumber(Math.abs(value), 2)}${unit ? ` ${unit}` : ""}`;
}

function formatNumber(value, maximumFractionDigits = 0) {
  return new Intl.NumberFormat("ja-JP", {
    maximumFractionDigits,
    minimumFractionDigits: 0,
  }).format(value);
}

function nonZeroPrecision(value, preferredDigits, maximumDigits) {
  const absolute = Math.abs(Number(value));
  if (!absolute || !Number.isFinite(absolute)) return preferredDigits;
  const threshold = 10 ** -preferredDigits;
  if (absolute >= threshold) return preferredDigits;
  return Math.min(maximumDigits, Math.ceil(-Math.log10(absolute)) + 1);
}

function formatFiscalYear(value) {
  if (!value) return "—";
  const [year, month] = String(value).split("-");
  return `${year}年${Number(month)}月期`;
}

function formatDateTime(value, dateOnly = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(dateOnly ? {} : { hour: "2-digit", minute: "2-digit" }),
  }).format(date);
}

function renderCompany() {
  const company = state.companyById.get(String(state.companyId));
  if (!company) {
    elements.companyContent.innerHTML = renderEmpty("会社が見つかりません", "企業一覧から選び直してください。");
    return;
  }
  document.title = `${company.name || "企業"} | EDINET 財務ビューア`;
  if (!company.hasData) {
    elements.companyContent.innerHTML = `
      ${renderCompanyHeader(company)}
      <div class="panel">${renderEmpty("財務データはまだ収録されていません", "企業名簿には掲載されていますが、対象期間の有価証券報告書をまだ取得していません。日次更新で順次反映します。")}</div>`;
    return;
  }

  const periods = orderedPeriods(company);
  const latest = periods.at(-1);
  const previous = periods.at(-2);
  const availableMetrics = METRIC_ORDER.filter((key) => periods.some((period) => period.metrics?.[key]));
  if (!availableMetrics.includes(state.chartMetric)) state.chartMetric = availableMetrics[0] || "revenue";
  const selected = state.compare.has(String(company.id));

  elements.companyContent.innerHTML = `
    ${renderCompanyHeader(company)}
    <div class="data-context">
      <span class="badge badge-teal">${company.scope === "separate" ? "単体" : "連結"}</span>
      <span class="badge">${escapeHtml(company.accounting_standard || "会計基準不明")}</span>
      <span class="badge badge-muted">${formatFiscalYear(latest.period_end)}まで</span>
      ${company.latest_filing?.is_amendment ? '<span class="badge badge-orange">訂正報告書を反映</span>' : ""}
    </div>
    <div class="company-actions company-actions-inline">
      <button class="secondary-button${selected ? " is-selected" : ""}" type="button" data-compare-id="${escapeHtml(company.id)}">${selected ? "比較リストから外す" : "比較リストに追加"}</button>
      <a class="primary-button button-link" href="${escapeHtml(filingUrl(company.latest_filing?.doc_id, company.latest_filing?.official_url))}" target="_blank" rel="noreferrer">採用書類をEDINETで確認 ↗</a>
    </div>
    <section aria-labelledby="latest-kpi-title">
      <h2 id="latest-kpi-title" class="sr-only">最新決算期の主要指標</h2>
      <div class="kpi-grid">
        ${renderKpi("売上高等", latest.metrics?.revenue, previous?.metrics?.revenue, latest, previous)}
        ${renderKpi("営業利益", latest.metrics?.operating_profit, previous?.metrics?.operating_profit, latest, previous)}
        ${renderKpi("当期純利益", latest.metrics?.net_income, previous?.metrics?.net_income, latest, previous)}
        ${renderKpi("総資産", latest.metrics?.total_assets, previous?.metrics?.total_assets, latest, previous)}
      </div>
    </section>
    <section class="panel" aria-labelledby="trend-title">
      <div class="panel-header">
        <div><h2 id="trend-title">財務推移</h2><p>欠損値は線で補間せず、記載のある決算期だけを表示します。</p></div>
        <label class="chart-controls"><span class="control-label">表示する指標</span>
          <select id="company-chart-metric">${availableMetrics
            .map((key) => `<option value="${key}"${key === state.chartMetric ? " selected" : ""}>${escapeHtml(METRICS[key]?.label || key)}</option>`)
            .join("")}</select>
        </label>
      </div>
      <div id="company-chart">${renderBarChart(periods, state.chartMetric)}</div>
    </section>
    <section class="panel" aria-labelledby="financial-table-title">
      <div class="panel-header"><div><h2 id="financial-table-title">決算期別の数値</h2><p>表示単位は各セルに記載。提出書類にない値は「—」です。</p></div></div>
      ${renderFinancialTable(periods, company)}
    </section>
    <section class="panel" aria-labelledby="source-title">
      <div class="panel-header"><div><h2 id="source-title">提出書類・出典</h2><p>採用した書類と加工条件を確認できます。</p></div></div>
      ${renderSource(company)}
    </section>`;

  document.querySelector("#company-chart-metric")?.addEventListener("change", (event) => {
    state.chartMetric = event.target.value;
    document.querySelector("#company-chart").innerHTML = renderBarChart(periods, state.chartMetric);
  });
}

function renderCompanyHeader(company) {
  return `<header class="company-header">
    <div>
      <p class="section-kicker">COMPANY FINANCIALS</p>
      <h1 id="company-title">${escapeHtml(company.name || "名称不明")}</h1>
      <p class="company-identifiers">
        ${company.ticker ? `<span>証券コード ${escapeHtml(company.ticker)}</span>` : ""}
        ${company.edinet_code ? `<span>EDINET ${escapeHtml(company.edinet_code)}</span>` : ""}
        ${company.industry ? `<span>${escapeHtml(company.industry)}</span>` : ""}
        ${company.fiscal_year_end ? `<span>決算日 ${escapeHtml(company.fiscal_year_end)}</span>` : ""}
      </p>
    </div>
  </header>`;
}

function renderKpi(label, current, previous, currentPeriod, previousPeriod) {
  const change = calculateChange(current, previous, currentPeriod, previousPeriod);
  const sourceLabel = current?.label && current.label !== label ? shortLabel(current.label) : formatFiscalYear(currentPeriod?.period_end);
  return `<article class="kpi-card">
    <p class="kpi-label">${escapeHtml(label)}</p>
    <p class="kpi-value">${escapeHtml(formatValue(current))}</p>
    <p class="kpi-foot"><span title="${escapeHtml(current?.label || "")}">${escapeHtml(sourceLabel || "記載なし")}</span>${renderChange(change)}</p>
  </article>`;
}

function calculateChange(current, previous, currentPeriod, previousPeriod) {
  if (!current || !previous) return { value: null, reason: "missing" };
  if (!isRegularAnnualPeriod(currentPeriod) || !isRegularAnnualPeriod(previousPeriod)) {
    return { value: null, reason: "irregular_period" };
  }
  const currentValue = Number(current.value);
  const previousValue = Number(previous.value);
  if (!Number.isFinite(currentValue) || !Number.isFinite(previousValue) || previousValue === 0) {
    return { value: null, reason: "not_calculable" };
  }
  return { value: ((currentValue - previousValue) / Math.abs(previousValue)) * 100, reason: null };
}

function renderChange(change) {
  if (!change || change.value === null || !Number.isFinite(change.value)) {
    const label = change?.reason === "irregular_period" ? "変則期のため算出不可" : "前期比 —";
    return `<span class="missing">${label}</span>`;
  }
  const direction = change.value > 0 ? "↑" : change.value < 0 ? "↓" : "→";
  const className = change.value > 0 ? "change-up" : change.value < 0 ? "change-down" : "";
  return `<span class="change ${className}">${direction} ${formatNumber(Math.abs(change.value), 1)}%</span>`;
}

function renderBarChart(periods, metricKey) {
  const entries = periods.map((period) => ({ period, metric: period.metrics?.[metricKey] || null }));
  const values = entries.map((entry) => Number(entry.metric?.value)).filter(Number.isFinite);
  if (!values.length) return renderEmpty("この指標の推移はありません", "提出書類に比較可能な値がありません。");
  const width = 900;
  const height = 320;
  const margin = { top: 32, right: 28, bottom: 56, left: 92 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  let min = Math.min(0, ...values);
  let max = Math.max(0, ...values);
  if (max === min) max = min + 1;
  const padding = (max - min) * 0.08;
  if (max > 0) max += padding;
  if (min < 0) min -= padding;
  const scaleY = (value) => margin.top + ((max - value) / (max - min)) * plotHeight;
  const zeroY = scaleY(0);
  const slot = plotWidth / entries.length;
  const barWidth = Math.min(76, slot * 0.56);
  const sampleMetric = entries.find((entry) => entry.metric)?.metric;
  const grid = [];
  for (let index = 0; index <= 4; index += 1) {
    const value = max - ((max - min) * index) / 4;
    const y = scaleY(value);
    grid.push(`<line class="chart-grid-line" x1="${margin.left}" x2="${width - margin.right}" y1="${y}" y2="${y}" />`);
    grid.push(`<text class="chart-axis-text" x="${margin.left - 12}" y="${y + 4}" text-anchor="end">${escapeHtml(formatValue({ value, unit: sampleMetric?.unit }, { axis: true }))}</text>`);
  }
  const bars = entries
    .map((entry, index) => {
      const x = margin.left + slot * index + (slot - barWidth) / 2;
      const labelX = margin.left + slot * index + slot / 2;
      const periodLabel = formatFiscalYear(entry.period.period_end).replace("年", "/").replace("月期", "");
      if (!entry.metric || !Number.isFinite(Number(entry.metric.value))) {
        return `<text class="chart-axis-text" x="${labelX}" y="${height - 25}" text-anchor="middle">${escapeHtml(periodLabel)}</text><text class="chart-axis-text" x="${labelX}" y="${zeroY - 8}" text-anchor="middle">—</text>`;
      }
      const value = Number(entry.metric.value);
      const y = value >= 0 ? scaleY(value) : zeroY;
      const barHeight = Math.max(1, Math.abs(scaleY(value) - zeroY));
      const valueY = value >= 0 ? y - 8 : y + barHeight + 16;
      return `<rect class="chart-bar${value < 0 ? " negative" : ""}" x="${x}" y="${y}" width="${barWidth}" height="${barHeight}" rx="4"><title>${escapeHtml(`${formatFiscalYear(entry.period.period_end)} ${formatValue(entry.metric)}`)}</title></rect><text class="chart-value-text" x="${labelX}" y="${valueY}" text-anchor="middle">${escapeHtml(formatValue(entry.metric, { axis: true }))}</text><text class="chart-axis-text" x="${labelX}" y="${height - 25}" text-anchor="middle">${escapeHtml(periodLabel)}</text>`;
    })
    .join("");
  const label = METRICS[metricKey]?.label || metricKey;
  return `<div class="chart-wrap"><svg class="financial-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(`${label}の決算期別推移。正確な値は次の表にも掲載しています。`)}">${grid.join("")}<line class="chart-zero-line" x1="${margin.left}" x2="${width - margin.right}" y1="${zeroY}" y2="${zeroY}" />${bars}</svg></div>`;
}

function renderFinancialTable(periods, company) {
  const keys = METRIC_ORDER.filter((key) => periods.some((period) => period.metrics?.[key]));
  return `<div class="table-scroll"><table class="financial-table">
    <thead><tr><th scope="col">指標</th>${periods.map((period) => `<th scope="col" class="number-cell">${formatFiscalYear(period.period_end)}<span class="number-sub">${period.scope === "separate" ? "単体" : "連結"}・${escapeHtml(period.accounting_standard || company.accounting_standard || "基準不明")}</span></th>`).join("")}</tr></thead>
    <tbody>${keys
      .map((key) => {
        const source = [...periods].reverse().find((period) => period.metrics?.[key])?.metrics?.[key];
        return `<tr><th scope="row"><span class="metric-label">${escapeHtml(METRICS[key]?.label || key)}</span>${source?.label && source.label !== METRICS[key]?.label ? `<span class="metric-source-label" title="${escapeHtml(source.label)}">提出科目：${escapeHtml(source.label)}</span>` : ""}${source?.calculated ? '<span class="metric-source-label">本サイト計算</span>' : ""}</th>${periods
          .map((period) => {
            const metric = period.metrics?.[key];
            return `<td>${metric ? `${escapeHtml(formatValue(metric))}${renderMetricSources(metric)}` : '<span class="missing" aria-label="記載なし">—</span>'}</td>`;
          })
          .join("")}</tr>`;
      })
      .join("")}</tbody>
  </table></div>`;
}

function renderSource(company) {
  const filing = company.latest_filing || {};
  return `<div class="source-grid">
    <article class="source-card"><h3>採用した開示書類</h3><dl>
      <dt>書類</dt><dd>${escapeHtml(filing.description || "有価証券報告書")}</dd>
      <dt>提出日時</dt><dd>${escapeHtml(filing.submitted_at || "—")}</dd>
      <dt>対象期間</dt><dd>${escapeHtml(filing.period_start || "—")} 〜 ${escapeHtml(filing.period_end || "—")}</dd>
      <dt>docID</dt><dd>${filing.doc_id ? `<a href="${escapeHtml(filingUrl(filing.doc_id, filing.official_url))}" target="_blank" rel="noreferrer">${escapeHtml(filing.doc_id)} ↗</a>` : "—"}</dd>
      <dt>訂正</dt><dd>${filing.is_amendment ? `訂正報告書を反映${filing.parent_doc_id ? `（親 ${escapeHtml(filing.parent_doc_id)}）` : ""}` : "訂正報告書ではありません"}</dd>
    </dl></article>
    <article class="source-card"><h3>加工方法</h3><dl>
      <dt>出典</dt><dd>金融庁 EDINET API v2</dd>
      <dt>取得形式</dt><dd>XBRLからEDINETが変換したCSV</dd>
      <dt>表示範囲</dt><dd>${company.scope === "separate" ? "単体" : "連結"}を優先、最大10期</dd>
      <dt>欠損</dt><dd>0に置き換えず「—」で表示</dd>
      <dt>公式確認</dt><dd><a href="${escapeHtml(filingUrl(filing.doc_id, filing.official_url))}" target="_blank" rel="noreferrer">採用書類をEDINETで開く ↗</a></dd>
    </dl></article>
  </div>`;
}

function toggleCompare(id) {
  const company = state.companyById.get(String(id));
  if (!company?.hasData) return;
  const key = String(company.id);
  if (state.compare.has(key)) {
    state.compare.delete(key);
    announce(`${company.name}を比較リストから外しました`);
  } else if (state.compare.size >= 4) {
    announce("比較できるのは最大4社です");
    return;
  } else {
    state.compare.add(key);
    announce(`${company.name}を比較リストに追加しました`);
  }
  storeCompare();
  renderCompareDock();
  if (state.route === "home") renderResults();
  if (state.route === "company") renderCompany();
  if (state.route === "compare") {
    const url = new URL(location.href);
    if (state.compare.size) url.searchParams.set("compare", [...state.compare].join(","));
    else url.searchParams.delete("compare");
    history.replaceState({}, "", url);
    renderCompare();
  }
}

function renderCompareDock() {
  const companies = [...state.compare].map((id) => state.companyById.get(id)).filter(Boolean);
  elements.compareCount.textContent = String(companies.length);
  elements.compareDock.hidden = companies.length === 0 || state.route === "compare";
  document.body.classList.toggle("has-compare-dock", !elements.compareDock.hidden);
  elements.compareChips.innerHTML = companies
    .map(
      (company) => `<span class="compare-chip">${escapeHtml(company.name)}<button type="button" data-remove-compare="${escapeHtml(company.id)}" aria-label="${escapeHtml(company.name)}を比較から外す">×</button></span>`,
    )
    .join("");
  elements.openCompare.disabled = companies.length < 2;
  elements.openCompare.textContent = companies.length < 2 ? "もう1社選んで比較" : `${companies.length}社を比較`;
}

function renderCompare() {
  document.title = "企業比較 | EDINET 財務ビューア";
  const companies = [...state.compare].map((id) => state.companyById.get(id)).filter((company) => company?.hasData).slice(0, 4);
  if (companies.length < 2) {
    elements.compareContent.innerHTML = renderEmpty("比較する会社を2社以上選んでください", "企業一覧の「比較に追加」から最大4社を選べます。");
    return;
  }
  const standards = new Set(companies.map((company) => company.accounting_standard).filter(Boolean));
  const fiscalMonths = new Set(companies.map((company) => latestPeriod(company)?.period_end?.slice(5, 7)).filter(Boolean));
  const scopes = new Set(companies.map((company) => company.scope).filter(Boolean));
  const irregularCompanies = companies.filter((company) => !isRegularAnnualPeriod(latestPeriod(company)));
  const warnings = [];
  if (standards.size > 1) warnings.push("会計基準が異なります");
  if (fiscalMonths.size > 1) warnings.push("決算月が異なります");
  if (scopes.size > 1) warnings.push("連結と単体が混在しています");
  if (irregularCompanies.length) warnings.push("12か月ではない、または期間を確認できない決算期があります");
  if (companies.some((company) => /銀行|保険|証券|金融/.test(company.industry || ""))) warnings.push("金融業の収益概念は一般事業会社と異なります");
  const availableMetrics = METRIC_ORDER.filter((key) => companies.some((company) => company.periods?.some((period) => period.metrics?.[key])));
  if (!availableMetrics.includes(state.compareMetric)) state.compareMetric = availableMetrics[0] || "revenue";

  elements.compareContent.innerHTML = `
    <div class="compare-company-list">${companies
      .map(
        (company, index) => {
          const latest = latestPeriod(company);
          return `<article class="compare-company-card" style="--company-color:${COMPANY_COLORS[index]}"><button type="button" class="remove-compare" data-remove-compare="${escapeHtml(company.id)}" aria-label="${escapeHtml(company.name)}を比較から外す">×</button><h2>${escapeHtml(company.name)}</h2><p>${escapeHtml(company.ticker || "証券コードなし")} ・ ${escapeHtml(company.accounting_standard || "基準不明")} ・ ${company.scope === "separate" ? "単体" : "連結"}<br>${formatFiscalYear(latest?.period_end)}${isRegularAnnualPeriod(latest) ? "" : ' <span class="badge badge-orange">変則期・期間不明</span>'}<span class="compare-period-range">${escapeHtml(periodRangeLabel(latest))}</span></p></article>`;
        },
      )
      .join("")}</div>
    ${warnings.length ? `<div class="notice notice-warning"><span class="notice-icon" aria-hidden="true">!</span><div><strong>比較条件に違いがあります</strong><p>${escapeHtml(warnings.join("／"))}。単純な大小だけで判断せず、各社の原本も確認してください。</p></div></div>` : ""}
    <section class="panel" aria-labelledby="compare-trend-title">
      <div class="panel-header"><div><h2 id="compare-trend-title">推移を比較</h2><p>決算期を横軸に、各社の提出値を表示します。</p></div><label class="chart-controls"><span class="control-label">表示する指標</span><select id="compare-chart-metric">${availableMetrics
        .map((key) => `<option value="${key}"${key === state.compareMetric ? " selected" : ""}>${escapeHtml(METRICS[key]?.label || key)}</option>`)
        .join("")}</select></label></div>
      <div id="comparison-chart">${renderComparisonChart(companies, state.compareMetric)}</div>
    </section>
    <section class="panel" aria-labelledby="latest-compare-title">
      <div class="panel-header"><div><h2 id="latest-compare-title">各社の最新決算期</h2><p>決算期が異なる場合があります。列見出しの年月をご確認ください。</p></div></div>
      ${renderComparisonTable(companies)}
    </section>`;
  document.querySelector("#compare-chart-metric")?.addEventListener("change", (event) => {
    state.compareMetric = event.target.value;
    document.querySelector("#comparison-chart").innerHTML = renderComparisonChart(companies, state.compareMetric);
  });
}

function renderComparisonChart(companies, metricKey) {
  const series = companies.map((company, index) => {
    const entries = orderedPeriods(company).map((period) => {
      const metric = period.metrics?.[metricKey];
      if (!metric || !Number.isFinite(Number(metric.value))) return null;
      return { date: new Date(`${period.period_end}T00:00:00Z`), period, metric };
    });
    return {
      company,
      color: COMPANY_COLORS[index],
      dash: COMPANY_DASHES[index],
      markerIndex: index,
      entries,
      points: entries.filter(Boolean),
    };
  });
  const points = series.flatMap((item) => item.points);
  if (!points.length) return renderEmpty("比較できる値がありません", "この指標は選択した会社の提出書類にありません。");
  const width = 900;
  const height = 330;
  const margin = { top: 35, right: 28, bottom: 54, left: 92 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const timestamps = points.map((point) => point.date.getTime());
  let timeMin = Math.min(...timestamps);
  let timeMax = Math.max(...timestamps);
  if (timeMin === timeMax) timeMax += 365 * 24 * 60 * 60 * 1000;
  const values = points.map((point) => Number(point.metric.value));
  let min = Math.min(0, ...values);
  let max = Math.max(0, ...values);
  if (min === max) max = min + 1;
  const padding = (max - min) * 0.08;
  if (max > 0) max += padding;
  if (min < 0) min -= padding;
  const x = (date) => margin.left + ((date.getTime() - timeMin) / (timeMax - timeMin)) * plotWidth;
  const y = (value) => margin.top + ((max - value) / (max - min)) * plotHeight;
  const sample = points[0].metric;
  const grid = [];
  for (let index = 0; index <= 4; index += 1) {
    const value = max - ((max - min) * index) / 4;
    const lineY = y(value);
    grid.push(`<line class="chart-grid-line" x1="${margin.left}" x2="${width - margin.right}" y1="${lineY}" y2="${lineY}"/><text class="chart-axis-text" x="${margin.left - 12}" y="${lineY + 4}" text-anchor="end">${escapeHtml(formatValue({ value, unit: sample.unit }, { axis: true }))}</text>`);
  }
  const yearMin = new Date(timeMin).getUTCFullYear();
  const yearMax = new Date(timeMax).getUTCFullYear();
  for (let year = yearMin; year <= yearMax; year += 1) {
    const date = new Date(Date.UTC(year, 11, 31));
    if (date.getTime() < timeMin || date.getTime() > timeMax) continue;
    grid.push(`<text class="chart-axis-text" x="${x(date)}" y="${height - 24}" text-anchor="middle">${year}</text>`);
  }
  const paths = series
    .map((item) => {
      let startsSegment = true;
      const path = item.entries
        .map((point) => {
          if (!point) {
            startsSegment = true;
            return "";
          }
          const command = startsSegment ? "M" : "L";
          startsSegment = false;
          return `${command}${x(point.date).toFixed(1)},${y(Number(point.metric.value)).toFixed(1)}`;
        })
        .join(" ");
      const circles = item.points
        .map((point) => renderChartMarker(item, point, x(point.date), y(Number(point.metric.value))))
        .join("");
      return `<path class="chart-line" style="--series-color:${item.color};stroke:${item.color};stroke-dasharray:${item.dash || "none"}" d="${path}"/>${circles}`;
    })
    .join("");
  const legend = `<div class="chart-legend">${series
    .map((item) => `<span class="legend-item"><span class="legend-marker marker-${item.markerIndex}" style="--series-color:${item.color}" aria-hidden="true"></span>${escapeHtml(item.company.name)}（${item.markerIndex === 0 ? "実線" : "破線"}）</span>`)
    .join("")}</div>`;
  return `<div class="chart-wrap"><svg class="financial-chart comparison-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(`${METRICS[metricKey]?.label || metricKey}の企業別推移。欠損期間は線を切っています。正確な値は続く数値表で確認できます。`)}">${grid.join("")}<line class="chart-zero-line" x1="${margin.left}" x2="${width - margin.right}" y1="${y(0)}" y2="${y(0)}"/>${paths}</svg></div>${legend}${renderComparisonHistoryTable(companies, metricKey)}`;
}

function renderChartMarker(series, point, x, y) {
  const title = `<title>${escapeHtml(`${series.company.name} ${formatFiscalYear(point.period.period_end)} ${formatValue(point.metric)}`)}</title>`;
  const style = `--series-color:${series.color}`;
  if (series.markerIndex === 1) return `<rect class="chart-point" style="${style}" x="${x - 5}" y="${y - 5}" width="10" height="10" rx="1">${title}</rect>`;
  if (series.markerIndex === 2) return `<path class="chart-point" style="${style}" d="M${x},${y - 6} L${x + 6},${y} L${x},${y + 6} L${x - 6},${y} Z">${title}</path>`;
  if (series.markerIndex === 3) return `<path class="chart-point" style="${style}" d="M${x},${y - 6} L${x + 6},${y + 5} L${x - 6},${y + 5} Z">${title}</path>`;
  return `<circle class="chart-point" style="${style}" cx="${x}" cy="${y}" r="5">${title}</circle>`;
}

function renderComparisonHistoryTable(companies, metricKey) {
  const periodEnds = [...new Set(companies.flatMap((company) => orderedPeriods(company).map((period) => period.period_end)))].sort();
  return `<details class="chart-data-details"><summary>推移の数値表を表示</summary><div class="table-scroll"><table class="comparison-history-table"><thead><tr><th scope="col">決算期</th>${companies
    .map((company) => `<th scope="col">${escapeHtml(company.name)}</th>`)
    .join("")}</tr></thead><tbody>${periodEnds
    .map(
      (periodEnd) => `<tr><th scope="row">${formatFiscalYear(periodEnd)}</th>${companies
        .map((company) => {
          const period = company.periods?.find((candidate) => candidate.period_end === periodEnd);
          const metric = period?.metrics?.[metricKey];
          return `<td>${metric ? `${escapeHtml(formatValue(metric))}${renderMetricSources(metric)}` : '<span class="missing" aria-label="記載なし">—</span>'}</td>`;
        })
        .join("")}</tr>`,
    )
    .join("")}</tbody></table></div></details>`;
}

function renderComparisonTable(companies) {
  const keys = METRIC_ORDER.filter((key) => companies.some((company) => latestPeriod(company)?.metrics?.[key]));
  return `<div class="table-scroll"><table class="comparison-table"><thead><tr><th scope="col">指標</th>${companies
    .map((company) => `<th scope="col" class="number-cell">${escapeHtml(company.name)}<span class="number-sub">${formatFiscalYear(latestPeriod(company)?.period_end)}<br>${escapeHtml(periodRangeLabel(latestPeriod(company)))}</span></th>`)
    .join("")}</tr></thead><tbody>${keys
    .map((key) => `<tr><th scope="row">${escapeHtml(METRICS[key]?.label || key)}</th>${companies
      .map((company) => {
        const metric = latestPeriod(company)?.metrics?.[key];
        return `<td>${metric ? `<strong>${escapeHtml(formatValue(metric))}</strong>${metric.label && metric.label !== METRICS[key]?.label ? `<span class="number-sub" title="${escapeHtml(metric.label)}">${escapeHtml(shortLabel(metric.label))}</span>` : ""}${renderMetricSources(metric)}` : '<span class="missing" aria-label="記載なし">—</span>'}</td>`;
      })
      .join("")}</tr>`)
    .join("")}</tbody></table></div>`;
}

function renderAbout() {
  document.title = "データについて | EDINET 財務ビューア";
  const meta = state.payload?.meta || {};
  const coverage = meta.coverage || {};
  const definitions = [
    ["売上高等", "売上高、売上収益、営業収益、経常収益など、提出者が使用する科目を表示。金融業等は概念が異なります。"],
    ["営業利益", "日本基準の営業利益、IFRSの営業利益等。提出がない場合は空欄です。"],
    ["経常利益", "主に日本基準の経常利益。IFRSには対応する標準概念がないため、原則として空欄です。"],
    ["当期純利益", "連結では親会社の所有者・株主に帰属する利益を優先します。"],
    ["営業利益率", "本サイト計算：営業利益 ÷ 売上高等 × 100。概念が異なる会社間では単純比較できません。"],
    ["自己資本比率（簡易）", "本サイト計算：純資産・資本 ÷ 総資産 × 100。厳密な自己資本比率と一致しない場合があります。"],
    ["フリーキャッシュフロー", "本サイト計算：営業キャッシュフロー ＋ 投資キャッシュフロー。"],
  ];
  elements.aboutContent.innerHTML = `
    <div class="about-grid">
      <article class="about-card"><h2>データの取得</h2><p>GitHub ActionsがEDINET API v2から日別の書類一覧と有価証券報告書のXBRL変換CSVを取得し、ブラウザ用の静的JSONへ加工します。APIキーはブラウザや公開ファイルへ出しません。</p><p><a href="https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html" target="_blank" rel="noreferrer">EDINET API公式資料 ↗</a></p></article>
      <article class="about-card"><h2>更新と収録範囲</h2><p>最終生成：${escapeHtml(meta.generated_at ? formatDateTime(meta.generated_at) : "初回取得前")}<br>財務データ：${Number(coverage.companies || 0).toLocaleString("ja-JP")}社・${Number(coverage.periods || 0).toLocaleString("ja-JP")}期<br>取得対象：有価証券報告書と訂正有価証券報告書</p><p>会社検索APIがないため、初回は主要上場企業から収録し、日次更新で差分を反映します。</p></article>
      <article class="about-card"><h2>連結・単体と期間</h2><p>連結財務諸表がある場合は連結を優先します。連結がない場合は単体を表示し、画面上に明示します。通期の決算期だけを同じ系列に並べ、半期や旧制度の四半期は混ぜません。</p></article>
      <article class="about-card"><h2>欠損と訂正</h2><p>提出書類に値がない場合は「—」と表示し、0へ置き換えません。EDINETの変換CSVに単独の「-」が記録された場合だけ、公式仕様に従い明示的な0として扱います。訂正報告書に財務データがある場合は新しい提出値を優先します。</p></article>
      <article class="about-card about-wide"><h2>指標の定義</h2><div class="table-scroll"><table class="definition-table"><thead><tr><th scope="col">表示名</th><th scope="col">定義・注意</th></tr></thead><tbody>${definitions
        .map(([label, description]) => `<tr><th scope="row">${escapeHtml(label)}</th><td>${escapeHtml(description)}</td></tr>`)
        .join("")}</tbody></table></div></article>
      <article class="about-card about-wide"><h2>利用上の注意</h2><ul><li>本サイトは金融庁またはEDINETの公式サービスではありません。</li><li>会計基準、決算期、連結範囲、業種固有の科目が異なる数値を単純比較しないでください。</li><li>提出者別の独自要素は自動抽出できず、値が欠ける場合があります。</li><li>正確な内容はdocIDと提出日を確認し、EDINETの原本を参照してください。</li><li>本サイトの表示は投資助言・投資判断を目的としません。</li></ul></article>
    </div>`;
}

function renderEmpty(title, description) {
  return `<div class="empty-state"><div><div class="empty-state-mark" aria-hidden="true">—</div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(description)}</p></div></div>`;
}

function announce(message) {
  elements.liveRegion.textContent = "";
  window.setTimeout(() => {
    elements.liveRegion.textContent = message;
  }, 30);
}

function showLoadError(error) {
  elements.globalStatus.classList.add("is-setup");
  elements.globalStatusText.textContent = "データの読み込みに失敗しました";
  elements.results.setAttribute("aria-busy", "false");
  elements.results.innerHTML = `<div class="error-state"><div><div class="empty-state-mark" aria-hidden="true">!</div><h3>財務データを読み込めませんでした</h3><p>${escapeHtml(error.message)}。しばらくしてから再読み込みしてください。</p></div></div>`;
  elements.resultsSummary.textContent = "読み込みエラー";
}

document.addEventListener("click", (event) => {
  const routeButton = event.target.closest("[data-route]");
  if (routeButton) {
    event.preventDefault();
    navigate(routeButton.dataset.route);
    return;
  }
  const companyButton = event.target.closest("[data-company-id]");
  if (companyButton) {
    navigate("company", companyButton.dataset.companyId);
    return;
  }
  const compareButton = event.target.closest("[data-compare-id]");
  if (compareButton && !compareButton.disabled) {
    toggleCompare(compareButton.dataset.compareId);
    return;
  }
  const removeButton = event.target.closest("[data-remove-compare]");
  if (removeButton) toggleCompare(removeButton.dataset.removeCompare);
});

elements.searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  state.query = elements.searchInput.value;
  state.visibleCount = PAGE_SIZE;
  if (state.route !== "home") navigate("home");
  else renderResults();
});

let searchTimer = null;
elements.searchInput.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    state.query = elements.searchInput.value;
    state.visibleCount = PAGE_SIZE;
    renderResults();
  }, 160);
});

for (const [element, key] of [
  [elements.industryFilter, "industry"],
  [elements.standardFilter, "standard"],
  [elements.dataFilter, "dataFilter"],
  [elements.sortFilter, "sort"],
]) {
  element.addEventListener("change", () => {
    state[key] = element.value;
    state.visibleCount = PAGE_SIZE;
    renderResults();
  });
}

elements.loadMore.addEventListener("click", () => {
  state.visibleCount += PAGE_SIZE;
  renderResults();
});

elements.openCompare.addEventListener("click", () => {
  if (state.compare.size >= 2) navigate("compare");
});

window.addEventListener("popstate", () => {
  parseRouteFromUrl();
  renderRoute({ moveFocus: true });
});

elements.results.innerHTML = `<div class="loading-state"><div class="skeleton" aria-label="読み込み中"><div class="skeleton-line"></div><div class="skeleton-line"></div><div class="skeleton-line"></div></div></div>`;
loadData().catch(showLoadError);
