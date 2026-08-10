#!/usr/bin/env python3
"""Build a compact financial dataset from the EDINET API v2.

The browser never receives the EDINET API key.  This script is intended to run
in GitHub Actions, download EDINET's XBRL-to-CSV archives, and publish only a
small, static JSON dataset for GitHub Pages.

Only Python's standard library is required.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
COMPANY_MASTER_URL = (
    "https://disclosure2dl.edinet-fsa.go.jp/"
    "searchdocument/codelist/Edinetcode.zip"
)
OFFICIAL_GUIDE_URL = (
    "https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/"
    "WZEK0110.html"
)
OFFICIAL_VIEWER_URL = "https://disclosure2.edinet-fsa.go.jp/"
ANNUAL_REPORT_CODES = {"120", "130"}
VALID_LEGAL_STATUSES = {"1", "2"}
DERIVED_METRIC_KEYS = {"operating_margin", "equity_ratio", "free_cash_flow"}


def official_filing_url(doc_id: str | None) -> str:
    return f"{OFFICIAL_VIEWER_URL}WZEK0040.aspx?{doc_id}" if doc_id else OFFICIAL_VIEWER_URL


def normalize_sec_code(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).upper()
    return "".join(character for character in normalized if character.isascii() and character.isalnum())


def ticker_from_sec_code(value: Any) -> str:
    sec_code = normalize_sec_code(value)
    return sec_code[:4] if len(sec_code) >= 4 else ""


def local_name(element_id: str) -> str:
    """Return the QName local part, tolerating Clark notation."""
    value = (element_id or "").strip()
    if "}" in value:
        return value.rsplit("}", 1)[-1]
    return value.rsplit(":", 1)[-1]


@dataclass(frozen=True)
class MetricSpec:
    key: str
    priority: int
    value_type: str = "currency"


# Lower priority wins.  Summary-of-business-results facts are preferred because
# a single annual report commonly contains five years of consistent facts.
METRIC_TAGS: dict[str, MetricSpec] = {}


def _register(
    key: str,
    names: Iterable[str],
    *,
    priority: int,
    value_type: str = "currency",
) -> None:
    for name in names:
        METRIC_TAGS[name] = MetricSpec(key, priority, value_type)


_register(
    "revenue",
    [
        "NetSalesSummaryOfBusinessResults",
        "RevenueIFRSSummaryOfBusinessResults",
        "RevenuesUSGAAPSummaryOfBusinessResults",
        "RevenueUSGAAPSummaryOfBusinessResults",
        "OperatingRevenue1SummaryOfBusinessResults",
        "OperatingRevenue2SummaryOfBusinessResults",
        "GrossOperatingRevenueSummaryOfBusinessResults",
        "OrdinaryIncomeSummaryOfBusinessResults",
    ],
    priority=10,
)
_register(
    "operating_profit",
    [
        "OperatingIncomeLossSummaryOfBusinessResults",
        "OperatingProfitLossIFRSSummaryOfBusinessResults",
        "OperatingIncomeLossUSGAAPSummaryOfBusinessResults",
    ],
    priority=10,
)
_register(
    "ordinary_profit",
    ["OrdinaryIncomeLossSummaryOfBusinessResults"],
    priority=10,
)
_register(
    "net_income",
    [
        "ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
        "ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "NetIncomeLossAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",
        "NetIncomeLossSummaryOfBusinessResults",
        "ProfitLossSummaryOfBusinessResults",
    ],
    priority=10,
)
_register(
    "total_assets",
    [
        "TotalAssetsSummaryOfBusinessResults",
        "TotalAssetsIFRSSummaryOfBusinessResults",
        "TotalAssetsUSGAAPSummaryOfBusinessResults",
    ],
    priority=10,
)
_register(
    "equity",
    [
        "NetAssetsSummaryOfBusinessResults",
        "TotalEquityIFRSSummaryOfBusinessResults",
        "EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "EquityAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",
        "EquityIncludingPortionAttributableToNonControllingInterestUSGAAPSummaryOfBusinessResults",
        "StockholdersEquityUSGAAPSummaryOfBusinessResults",
    ],
    priority=10,
)
_register(
    "operating_cash_flow",
    [
        "NetCashProvidedByUsedInOperatingActivitiesSummaryOfBusinessResults",
        "NetCashProvidedByUsedInOperatingActivitiesIFRSSummaryOfBusinessResults",
        "NetCashProvidedByUsedInOperatingActivitiesUSGAAPSummaryOfBusinessResults",
        "CashFlowsFromUsedInOperatingActivitiesSummaryOfBusinessResults",
        "CashFlowsFromUsedInOperatingActivitiesIFRSSummaryOfBusinessResults",
        "CashFlowsFromUsedInOperatingActivitiesUSGAAPSummaryOfBusinessResults",
    ],
    priority=10,
)
_register(
    "investing_cash_flow",
    [
        "NetCashProvidedByUsedInInvestingActivitiesSummaryOfBusinessResults",
        "NetCashProvidedByUsedInInvestmentActivitiesSummaryOfBusinessResults",
        "NetCashProvidedByUsedInInvestingActivitiesIFRSSummaryOfBusinessResults",
        "NetCashProvidedByUsedInInvestingActivitiesUSGAAPSummaryOfBusinessResults",
        "CashFlowsFromUsedInInvestingActivitiesSummaryOfBusinessResults",
        "CashFlowsFromUsedInInvestingActivitiesIFRSSummaryOfBusinessResults",
        "CashFlowsFromUsedInInvestingActivitiesUSGAAPSummaryOfBusinessResults",
    ],
    priority=10,
)
_register(
    "financing_cash_flow",
    [
        "NetCashProvidedByUsedInFinancingActivitiesSummaryOfBusinessResults",
        "NetCashProvidedByUsedInFinancingActivitiesIFRSSummaryOfBusinessResults",
        "NetCashProvidedByUsedInFinancingActivitiesUSGAAPSummaryOfBusinessResults",
        "CashFlowsFromUsedInFinancingActivitiesSummaryOfBusinessResults",
        "CashFlowsFromUsedInFinancingActivitiesIFRSSummaryOfBusinessResults",
        "CashFlowsFromUsedInFinancingActivitiesUSGAAPSummaryOfBusinessResults",
    ],
    priority=10,
)
_register(
    "eps",
    [
        "BasicEarningsLossPerShareSummaryOfBusinessResults",
        "BasicEarningsLossPerShareIFRSSummaryOfBusinessResults",
        "BasicEarningsLossPerShareUSGAAPSummaryOfBusinessResults",
        "NetIncomeLossPerShareSummaryOfBusinessResults",
    ],
    priority=10,
    value_type="per_share",
)
_register(
    "diluted_eps",
    [
        "DilutedEarningsPerShareSummaryOfBusinessResults",
        "DilutedEarningsLossPerShareIFRSSummaryOfBusinessResults",
        "DilutedEarningsLossPerShareUSGAAPSummaryOfBusinessResults",
    ],
    priority=10,
    value_type="per_share",
)

# Current-year fallbacks from the financial statements.
_register("revenue", ["NetSales", "Revenue", "Revenues", "RevenueIFRS", "Revenue2IFRS"], priority=100)
_register(
    "operating_profit",
    ["OperatingIncome", "OperatingIncomeLoss", "OperatingProfitLossIFRS"],
    priority=100,
)
_register("ordinary_profit", ["OrdinaryIncome", "OrdinaryIncomeLoss"], priority=100)
_register(
    "net_income",
    [
        "ProfitLossAttributableToOwnersOfParent",
        "ProfitLossAttributableToOwnersOfParentIFRS",
        "NetIncomeLossAttributableToOwnersOfParent",
        "ProfitLoss",
        "ProfitLossIFRS",
        "NetIncomeLoss",
    ],
    priority=100,
)
_register("total_assets", ["Assets", "AssetsIFRS", "TotalAssets"], priority=100)
_register(
    "equity",
    [
        "NetAssets",
        "EquityIFRS",
        "Equity",
        "EquityAttributableToOwnersOfParentIFRS",
        "StockholdersEquity",
    ],
    priority=100,
)
_register(
    "operating_cash_flow",
    [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesIFRS",
        "CashFlowsFromUsedInOperatingActivities",
    ],
    priority=100,
)
_register(
    "investing_cash_flow",
    [
        "NetCashProvidedByUsedInInvestingActivities",
        "NetCashProvidedByUsedInInvestmentActivities",
        "NetCashProvidedByUsedInInvestingActivitiesIFRS",
        "CashFlowsFromUsedInInvestingActivities",
    ],
    priority=100,
)
_register(
    "financing_cash_flow",
    [
        "NetCashProvidedByUsedInFinancingActivities",
        "NetCashProvidedByUsedInFinancingActivitiesIFRS",
        "CashFlowsFromUsedInFinancingActivities",
    ],
    priority=100,
)
_register(
    "eps",
    ["BasicEarningsLossPerShare", "EarningsPerShare", "BasicEarningsLossPerShareIFRS"],
    priority=100,
    value_type="per_share",
)
_register(
    "research_and_development",
    [
        "ResearchAndDevelopmentExpenses",
        "ResearchAndDevelopmentExpensesResearchAndDevelopmentActivities",
        "ResearchAndDevelopmentExpensesSGA",
        "ResearchAndDevelopmentExpensesIFRS",
        "ResearchAndDevelopmentExpenditureRecognizedAsExpenseDuringPeriodIFRS",
        "ResearchAndDevelopmentExpensesIncludedInGeneralAndAdministrativeExpensesAndManufacturingCostForCurrentPeriod",
    ],
    priority=100,
)


METRIC_LABELS = {
    "revenue": "売上高等",
    "operating_profit": "営業利益",
    "ordinary_profit": "経常利益",
    "net_income": "当期純利益",
    "total_assets": "総資産",
    "equity": "純資産・資本",
    "operating_cash_flow": "営業キャッシュフロー",
    "investing_cash_flow": "投資キャッシュフロー",
    "financing_cash_flow": "財務キャッシュフロー",
    "eps": "1株当たり利益",
    "diluted_eps": "希薄化後1株当たり利益",
    "research_and_development": "研究開発費",
}


DATE_ISO_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}[-/.](?:1[0-2]|0?[1-9])[-/.](?:3[01]|[12]\d|0?[1-9])(?!\d)"
)
DATE_JA_RE = re.compile(
    r"((?:19|20)\d{2})年(1[0-2]|0?[1-9])月(3[01]|[12]\d|0?[1-9])日"
)
PRIOR_RE = re.compile(r"Prior(\d+)Year", re.IGNORECASE)


def clean_header(value: str) -> str:
    return unicodedata.normalize("NFKC", (value or "").lstrip("\ufeff").strip()).replace(" ", "")


HEADER_ALIASES = {
    "element": {"要素ID", "elementid"},
    "label": {"項目名", "itemname"},
    "context": {"コンテキストID", "contextid"},
    "relative": {"相対年度", "relativeyear"},
    "scope": {"連結・個別", "連結/個別", "consolidatedornonconsolidated"},
    "period": {"期間・時点", "期間/時点", "periodorinstant"},
    "unit_id": {"ユニットID", "unitid"},
    "unit": {"単位", "unit"},
    "value": {"値", "value"},
}


def header_indexes(header: list[str]) -> dict[str, int]:
    normalized = [clean_header(v).lower() for v in header]
    result: dict[str, int] = {}
    for key, aliases in HEADER_ALIASES.items():
        aliases_lower = {clean_header(v).lower() for v in aliases}
        for index, value in enumerate(normalized):
            if value in aliases_lower:
                result[key] = index
                break
    required = {"element", "context", "value"}
    if not required.issubset(result):
        raise ValueError(f"CSV header is missing {sorted(required - set(result))}")
    return result


def row_value(row: list[str], indexes: dict[str, int], key: str) -> str:
    index = indexes.get(key)
    return row[index].strip() if index is not None and index < len(row) else ""


def parse_number(value: str) -> int | float | None:
    raw = unicodedata.normalize("NFKC", (value or "").strip())
    if not raw:
        return None
    if raw in {"-", "―", "−"}:
        # EDINET's CSV guide defines a lone dash as an explicit zero.
        return 0
    raw = raw.replace(",", "").replace(" ", "")
    negative_parentheses = raw.startswith("(") and raw.endswith(")")
    if negative_parentheses:
        raw = raw[1:-1]
    try:
        number = float(raw)
    except ValueError:
        return None
    if negative_parentheses:
        number = -number
    if number.is_integer():
        return int(number)
    return number


def parse_bool(value: str) -> bool | None:
    normalized = unicodedata.normalize("NFKC", (value or "").strip()).lower()
    if normalized in {"true", "1", "yes", "有", "あり"}:
        return True
    if normalized in {"false", "0", "no", "無", "なし"}:
        return False
    return None


def parse_date(value: str) -> dt.date | None:
    if not value:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    match = DATE_ISO_RE.search(normalized)
    if match:
        parts = re.split(r"[-/.]", match.group(0))
        try:
            return dt.date(*(int(p) for p in parts))
        except ValueError:
            return None
    match = DATE_JA_RE.search(normalized)
    if match:
        try:
            return dt.date(*(int(p) for p in match.groups()))
        except ValueError:
            return None
    return None


def parse_period_dates(value: str) -> list[dt.date]:
    normalized = unicodedata.normalize("NFKC", value or "")
    dates: list[dt.date] = []
    for match in DATE_ISO_RE.finditer(normalized):
        parsed = parse_date(match.group(0))
        if parsed and parsed not in dates:
            dates.append(parsed)
    for match in DATE_JA_RE.finditer(normalized):
        parsed = dt.date(*(int(p) for p in match.groups()))
        if parsed not in dates:
            dates.append(parsed)
    return dates


def shift_year(value: dt.date, years: int) -> dt.date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # 29 February to a non-leap year.
        return value.replace(year=value.year + years, day=28)


def context_offset(context_id: str, relative_year: str) -> int | None:
    context = context_id or ""
    if "CurrentYear" in context:
        return 0
    match = PRIOR_RE.search(context)
    if match:
        return -int(match.group(1))
    normalized = unicodedata.normalize("NFKC", relative_year or "")
    if normalized in {"当期", "当年度", "当連結会計年度"}:
        return 0
    match = re.search(r"前(\d+)期", normalized)
    if match:
        return -int(match.group(1))
    if normalized in {"前期", "前年度", "前連結会計年度"}:
        return -1
    return None


def infer_scope(context_id: str, scope_text: str) -> str:
    context = (context_id or "").lower()
    scope = unicodedata.normalize("NFKC", scope_text or "")
    if "nonconsolidatedmember" in context or "個別" in scope or "単体" in scope:
        return "separate"
    return "consolidated"


def is_company_wide_context(context_id: str) -> bool:
    context = context_id or ""
    if not context:
        return False
    lowered = context.lower()
    excluded = ("interim", "quarter", "filingdateinstant")
    if any(token in lowered for token in excluded):
        return False
    # Dimensions other than NonConsolidatedMember generally indicate a segment.
    if "member" in lowered and "nonconsolidatedmember" not in lowered:
        return False
    return "currentyear" in lowered or "prioryear" in lowered or PRIOR_RE.search(context) is not None


def normalize_unit(unit_id: str, unit_label: str, value_type: str) -> str:
    combined = unicodedata.normalize("NFKC", f"{unit_id} {unit_label}").lower()
    if value_type == "per_share" or "pershare" in combined or "円/株" in combined:
        return "JPY_PER_SHARE"
    if "percent" in combined or "%" in combined:
        return "PERCENT"
    if "jpy" in combined or "円" in combined:
        return "JPY"
    if "usd" in combined or "米ドル" in combined:
        return "USD"
    if "shares" in combined or "株" in combined:
        return "SHARES"
    return unit_label or unit_id or "UNKNOWN"


def detect_standard(element_names: Iterable[str], accounting_standard: str) -> str:
    normalized = unicodedata.normalize("NFKC", accounting_standard or "").upper()
    if "IFRS" in normalized or "国際" in normalized:
        return "IFRS"
    if "US" in normalized or "米国" in normalized:
        return "米国基準"
    names = list(element_names)
    if any("IFRS" in name for name in names):
        return "IFRS"
    if any("USGAAP" in name for name in names):
        return "米国基準"
    return "日本基準" if names else "不明"


def decode_tsv(data: bytes) -> str:
    for encoding in ("utf-16", "utf-16le", "utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    raise UnicodeDecodeError("utf-16", data, 0, min(len(data), 1), "unsupported EDINET CSV encoding")


def iter_tsv_rows(archive: bytes) -> Iterator[tuple[list[str], dict[str, int]]]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        names = [name for name in zipped.namelist() if name.lower().endswith(".csv")]
        public_names = [name for name in names if "audit" not in name.lower()]
        for name in public_names or names:
            text = decode_tsv(zipped.read(name))
            reader = csv.reader(io.StringIO(text), delimiter="\t", quotechar='"')
            try:
                header = next(reader)
                indexes = header_indexes(header)
            except (StopIteration, ValueError):
                continue
            for row in reader:
                if row:
                    yield row, indexes


def period_for_fact(
    context_id: str,
    relative_year: str,
    period_text: str,
    doc_start: dt.date | None,
    doc_end: dt.date | None,
) -> tuple[dt.date | None, dt.date | None]:
    explicit = parse_period_dates(period_text)
    if len(explicit) >= 2:
        return explicit[0], explicit[-1]
    if len(explicit) == 1:
        end = explicit[0]
        offset = context_offset(context_id, relative_year)
        if doc_start and doc_end and offset is not None:
            return shift_year(doc_start, offset), end
        return None, end
    offset = context_offset(context_id, relative_year)
    if offset is None or doc_end is None:
        return None, None
    start = shift_year(doc_start, offset) if doc_start else None
    return start, shift_year(doc_end, offset)


def extract_financials(archive: bytes, document: dict[str, Any]) -> dict[str, Any]:
    """Extract company-level annual facts from an EDINET type=5 archive."""
    doc_start = parse_date(document.get("periodStart") or "")
    doc_end = parse_date(document.get("periodEnd") or "")
    candidates: dict[tuple[str, str, str], dict[str, Any]] = {}
    all_elements: set[str] = set()
    consolidated_prepared: bool | None = None
    accounting_standard = ""

    for row, indexes in iter_tsv_rows(archive):
        element_id = row_value(row, indexes, "element")
        element = local_name(element_id)
        if not element:
            continue
        all_elements.add(element)
        value_text = row_value(row, indexes, "value")
        if element == "WhetherConsolidatedFinancialStatementsArePreparedDEI":
            consolidated_prepared = parse_bool(value_text)
            continue
        if element == "AccountingStandardsDEI":
            accounting_standard = value_text
            continue
        spec = METRIC_TAGS.get(element)
        if not spec:
            continue
        context_id = row_value(row, indexes, "context")
        if not is_company_wide_context(context_id):
            continue
        number = parse_number(value_text)
        if number is None:
            continue
        scope = infer_scope(context_id, row_value(row, indexes, "scope"))
        start, end = period_for_fact(
            context_id,
            row_value(row, indexes, "relative"),
            row_value(row, indexes, "period"),
            doc_start,
            doc_end,
        )
        if end is None:
            continue
        label = row_value(row, indexes, "label") or METRIC_LABELS[spec.key]
        unit = normalize_unit(
            row_value(row, indexes, "unit_id"),
            row_value(row, indexes, "unit"),
            spec.value_type,
        )
        key = (end.isoformat(), scope, spec.key)
        candidate = {
            "value": number,
            "unit": unit,
            "label": label,
            "source_element": element_id,
            "context_id": context_id,
            "priority": spec.priority,
            "period_start": start.isoformat() if start else None,
            "period_end": end.isoformat(),
        }
        previous = candidates.get(key)
        if previous is None or candidate["priority"] < previous["priority"]:
            candidates[key] = candidate

    if not candidates:
        raise ValueError("No supported company-level financial facts were found")

    if consolidated_prepared is False:
        preferred_scope = "separate"
    else:
        preferred_scope = (
            "consolidated"
            if any(key[1] == "consolidated" for key in candidates)
            else "separate"
        )

    periods: dict[str, dict[str, Any]] = {}
    for (period_end, scope, metric_key), fact in candidates.items():
        if scope != preferred_scope:
            continue
        period = periods.setdefault(
            period_end,
            {
                "period_start": fact["period_start"],
                "period_end": period_end,
                "scope": preferred_scope,
                "metrics": {},
            },
        )
        if fact["period_start"] and not period["period_start"]:
            period["period_start"] = fact["period_start"]
        fact.pop("priority", None)
        period["metrics"][metric_key] = fact

    for period in periods.values():
        metrics = period["metrics"]
        _add_derived_metric(metrics, "operating_margin", "営業利益率", "operating_profit", "revenue", "ratio")
        _add_derived_metric(metrics, "equity_ratio", "自己資本比率（簡易）", "equity", "total_assets", "ratio")
        _add_derived_metric(metrics, "free_cash_flow", "フリーキャッシュフロー", "operating_cash_flow", "investing_cash_flow", "sum")

    ordered = sorted(periods.values(), key=lambda p: p["period_end"])[-10:]
    standard = detect_standard(all_elements, accounting_standard)
    return {
        "accounting_standard": standard,
        "scope": preferred_scope,
        "periods": ordered,
    }


def _add_derived_metric(
    metrics: dict[str, dict[str, Any]],
    key: str,
    label: str,
    numerator_key: str,
    denominator_key: str,
    operation: str,
) -> None:
    numerator = metrics.get(numerator_key)
    denominator = metrics.get(denominator_key)
    if not numerator or not denominator:
        return
    if operation == "ratio":
        if denominator["value"] == 0:
            return
        value: int | float = round(numerator["value"] / denominator["value"] * 100, 4)
        unit = "PERCENT"
    else:
        if numerator["unit"] != denominator["unit"]:
            return
        value = numerator["value"] + denominator["value"]
        unit = numerator["unit"]
    source_documents: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    for operand in (numerator, denominator):
        sources = operand.get("source_documents") or []
        if not sources and operand.get("source_doc_id"):
            sources = [
                {
                    "doc_id": operand.get("source_doc_id"),
                    "submitted_at": operand.get("source_submitted_at"),
                    "description": operand.get("source_description"),
                    "url": operand.get("source_url"),
                }
            ]
        for source in sources:
            doc_id = str(source.get("doc_id") or "")
            if not doc_id or doc_id in seen_source_ids:
                continue
            seen_source_ids.add(doc_id)
            source_documents.append(source)

    derived = {
        "value": value,
        "unit": unit,
        "label": label,
        "calculated": True,
        "formula": (
            f"{numerator_key} / {denominator_key} × 100"
            if operation == "ratio"
            else f"{numerator_key} + {denominator_key}"
        ),
        "period_start": numerator.get("period_start"),
        "period_end": numerator.get("period_end"),
    }
    if source_documents:
        derived["source_documents"] = source_documents
        if len(source_documents) == 1:
            derived["source_doc_id"] = source_documents[0]["doc_id"]
            derived["source_url"] = source_documents[0].get("url")
    metrics[key] = derived


class EdinetClient:
    def __init__(
        self,
        api_key: str,
        *,
        minimum_interval: float = 3.0,
        timeout: int = 90,
        max_attempts: int = 5,
    ) -> None:
        if not api_key:
            raise ValueError("EDINET_API_KEY is required")
        self.api_key = api_key
        self.minimum_interval = max(0.0, minimum_interval)
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._last_request_at = 0.0

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.minimum_interval:
            time.sleep(self.minimum_interval - elapsed)

    def _request(self, path: str, params: dict[str, str]) -> tuple[bytes, str]:
        safe_params = dict(params)
        request_params = {**params, "Subscription-Key": self.api_key}
        query = urllib.parse.urlencode(request_params)
        url = f"{API_BASE}{path}?{query}"
        safe_url = f"{API_BASE}{path}?{urllib.parse.urlencode(safe_params)}"
        retry_delays = (5, 15, 45, 90)
        for attempt in range(self.max_attempts):
            self._pace()
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json, application/octet-stream",
                    "User-Agent": "edinet-financial-viewer/1.0",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self._last_request_at = time.monotonic()
                    return response.read(), response.headers.get_content_type()
            except urllib.error.HTTPError as error:
                self._last_request_at = time.monotonic()
                if error.code not in {429, 500, 502, 503, 504} or attempt == self.max_attempts - 1:
                    raise RuntimeError(f"EDINET request failed ({error.code}) for {safe_url}") from None
            except urllib.error.URLError as error:
                self._last_request_at = time.monotonic()
                if attempt == self.max_attempts - 1:
                    raise RuntimeError(f"EDINET request failed for {safe_url}: {error.reason}") from None
            delay = retry_delays[min(attempt, len(retry_delays) - 1)]
            time.sleep(delay)
        raise RuntimeError(f"EDINET request failed for {safe_url}")

    def list_documents(self, date: dt.date) -> list[dict[str, Any]]:
        retry_delays = (5, 15, 45, 90)
        last_error = "unknown response"
        for attempt in range(self.max_attempts):
            body, content_type = self._request(
                "/documents.json",
                {"date": date.isoformat(), "type": "2"},
            )
            try:
                if "json" not in content_type:
                    raise ValueError(f"unexpected content type {content_type}")
                payload = json.loads(body.decode("utf-8-sig"))
                metadata = payload.get("metadata", {})
                status = str(metadata.get("status", payload.get("StatusCode", "")))
                if status == "200":
                    return payload.get("results") or []
                last_error = f"status {status}: {metadata.get('message') or payload.get('message')}"
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                last_error = str(error)
            if attempt < self.max_attempts - 1:
                time.sleep(retry_delays[min(attempt, len(retry_delays) - 1)])
        raise RuntimeError(f"EDINET list API failed for {date.isoformat()}: {last_error}")

    def download_csv(self, doc_id: str) -> bytes:
        retry_delays = (5, 15, 45, 90)
        last_error = "unknown response"
        for attempt in range(self.max_attempts):
            body, content_type = self._request(f"/documents/{doc_id}", {"type": "5"})
            if zipfile.is_zipfile(io.BytesIO(body)):
                return body
            if "json" in content_type:
                try:
                    payload = json.loads(body.decode("utf-8-sig"))
                    last_error = payload.get("message") or payload.get("metadata", {}).get("message") or "unknown API error"
                except (UnicodeDecodeError, json.JSONDecodeError):
                    last_error = "invalid JSON error response"
            else:
                last_error = f"response was not a ZIP archive ({content_type})"
            if attempt < self.max_attempts - 1:
                time.sleep(retry_delays[min(attempt, len(retry_delays) - 1)])
        raise RuntimeError(f"EDINET document API failed for {doc_id}: {last_error}")


def http_get(url: str, *, timeout: int = 90) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "edinet-financial-viewer/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_company_master(archive: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        csv_names = [name for name in zipped.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError("EDINET company code ZIP did not contain a CSV")
        text = zipped.read(csv_names[0]).decode("cp932")
    rows = list(csv.reader(io.StringIO(text)))
    header_index = next(
        (
            index
            for index, row in enumerate(rows[:5])
            if any(clean_header(cell) == "EDINETコード" for cell in row)
        ),
        None,
    )
    if header_index is None:
        raise ValueError("Could not locate the EDINET company code header")
    header = [clean_header(cell) for cell in rows[header_index]]

    def find(*names: str) -> int | None:
        normalized = {clean_header(name) for name in names}
        return next((i for i, value in enumerate(header) if value in normalized), None)

    indexes = {
        "edinet_code": find("EDINETコード"),
        "listing": find("上場区分"),
        "consolidated": find("連結の有無"),
        "fiscal_year_end": find("決算日"),
        "name": find("提出者名"),
        "name_en": find("提出者名(英字)", "提出者名（英字）"),
        "name_kana": find("提出者名(ヨミ)", "提出者名（ヨミ）"),
        "location": find("所在地"),
        "industry": find("提出者業種"),
        "sec_code": find("証券コード"),
        "corporate_number": find("提出者法人番号"),
    }
    if indexes["edinet_code"] is None or indexes["name"] is None:
        raise ValueError("EDINET company code CSV is missing required columns")

    companies: list[dict[str, Any]] = []
    for row in rows[header_index + 1 :]:
        if not row:
            continue

        def get(key: str) -> str:
            index = indexes[key]
            return row[index].strip() if index is not None and index < len(row) else ""

        edinet_code = get("edinet_code")
        name = get("name")
        if not edinet_code or not name:
            continue
        sec_code = normalize_sec_code(get("sec_code"))
        ticker = ticker_from_sec_code(sec_code)
        companies.append(
            {
                "edinet_code": edinet_code,
                "sec_code": sec_code or None,
                "ticker": ticker or None,
                "name": name,
                "name_en": get("name_en") or None,
                "name_kana": get("name_kana") or None,
                "listing": get("listing") or None,
                "has_consolidated": get("consolidated") or None,
                "fiscal_year_end": get("fiscal_year_end") or None,
                "location": get("location") or None,
                "industry": get("industry") or None,
                "corporate_number": get("corporate_number") or None,
            }
        )
    companies.sort(key=lambda company: ((company.get("ticker") or "99999"), company["name"]))
    return companies


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def load_universe(path: Path) -> set[str]:
    payload = load_json(path, {})
    values = payload.get("tickers", payload) if isinstance(payload, dict) else payload
    tickers: set[str] = set()
    for item in values:
        value = item.get("ticker") if isinstance(item, dict) else item
        ticker = ticker_from_sec_code(value)
        if ticker:
            tickers.add(ticker)
    return tickers


def business_dates(end: dt.date, days: int) -> Iterator[dt.date]:
    current = end
    seen = 0
    while seen < max(days, 1):
        if current.weekday() < 5:
            yield current
        current -= dt.timedelta(days=1)
        seen += 1


def is_usable_annual_report(document: dict[str, Any], universe: set[str]) -> bool:
    ticker = ticker_from_sec_code(document.get("secCode"))
    if universe and ticker not in universe:
        return False
    if str(document.get("docTypeCode") or "") not in ANNUAL_REPORT_CODES:
        return False
    if str(document.get("csvFlag") or "") != "1":
        return False
    if str(document.get("withdrawalStatus") or "0") != "0":
        return False
    if str(document.get("disclosureStatus") or "0") != "0":
        return False
    if str(document.get("legalStatus") or "") not in VALID_LEGAL_STATUSES:
        return False
    return bool(document.get("docID") and document.get("edinetCode") and ticker)


def discover_documents(
    client: EdinetClient,
    *,
    end_date: dt.date,
    lookback_days: int,
    universe: set[str],
    stop_when_complete: bool,
    retry_dates: Iterable[dt.date] = (),
) -> tuple[dict[str, list[dict[str, Any]]], list[str], int, list[str], set[str]]:
    documents_by_ticker: dict[str, list[dict[str, Any]]] = {}
    seen_doc_ids: set[str] = set()
    tickers_with_original: set[str] = set()
    warnings: list[str] = []
    failed_dates: list[str] = []
    invalidated_doc_ids: set[str] = set()
    successful_dates = 0
    dates_to_scan: list[dt.date] = []
    seen_dates: set[dt.date] = set()
    for date in [*retry_dates, *business_dates(end_date, lookback_days)]:
        if date in seen_dates:
            continue
        seen_dates.add(date)
        dates_to_scan.append(date)
    for date in dates_to_scan:
        try:
            documents = client.list_documents(date)
            successful_dates += 1
        except RuntimeError as error:
            warnings.append(f"{date.isoformat()}: {error}")
            failed_dates.append(date.isoformat())
            continue
        for document in documents:
            invalid_status = (
                str(document.get("withdrawalStatus") or "0") != "0"
                or str(document.get("disclosureStatus") or "0") != "0"
            )
            if invalid_status:
                for key in ("docID", "parentDocID"):
                    if document.get(key):
                        invalidated_doc_ids.add(str(document[key]))
                continue
            if not is_usable_annual_report(document, universe):
                continue
            ticker = ticker_from_sec_code(document.get("secCode"))
            doc_id = str(document.get("docID") or "")
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            documents_by_ticker.setdefault(ticker, []).append(document)
            if str(document.get("docTypeCode") or "") == "120":
                tickers_with_original.add(ticker)
        if stop_when_complete and universe and universe.issubset(tickers_with_original):
            break
    if successful_dates == 0:
        raise RuntimeError("No EDINET document-list request succeeded; existing data was left unchanged")
    for documents in documents_by_ticker.values():
        documents.sort(key=lambda document: document.get("submitDateTime") or "")
    return documents_by_ticker, warnings, successful_dates, failed_dates, invalidated_doc_ids


def merge_company(
    existing: dict[str, Any] | None,
    document: dict[str, Any],
    extracted: dict[str, Any],
    master: dict[str, Any] | None,
) -> dict[str, Any]:
    sec_code = normalize_sec_code(document.get("secCode"))
    ticker = ticker_from_sec_code(sec_code)
    company = dict(existing or {})
    existing_filing = company.get("latest_filing") or {}
    candidate_rank = (str(document.get("periodEnd") or ""), str(document.get("submitDateTime") or ""))
    existing_rank = (str(existing_filing.get("period_end") or ""), str(existing_filing.get("submitted_at") or ""))
    is_latest_filing = not existing_filing or candidate_rank >= existing_rank
    company.update(
        {
            "edinet_code": document.get("edinetCode"),
            "sec_code": sec_code or None,
            "ticker": ticker,
            "name": (master or {}).get("name") or document.get("filerName") or company.get("name"),
            "name_en": (master or {}).get("name_en") or company.get("name_en"),
            "name_kana": (master or {}).get("name_kana") or company.get("name_kana"),
            "industry": (master or {}).get("industry") or company.get("industry"),
            "location": (master or {}).get("location") or company.get("location"),
            "fiscal_year_end": (master or {}).get("fiscal_year_end") or company.get("fiscal_year_end"),
            "corporate_number": document.get("JCN") or (master or {}).get("corporate_number") or company.get("corporate_number"),
        }
    )
    if is_latest_filing:
        company.update(
            {
                "accounting_standard": extracted["accounting_standard"],
                "scope": extracted["scope"],
                "latest_filing": {
                    "doc_id": document.get("docID"),
                    "doc_type_code": document.get("docTypeCode"),
                    "description": document.get("docDescription"),
                    "submitted_at": document.get("submitDateTime"),
                    "period_start": document.get("periodStart"),
                    "period_end": document.get("periodEnd"),
                    "parent_doc_id": document.get("parentDocID"),
                    "is_amendment": str(document.get("docTypeCode")) == "130",
                    "official_url": official_filing_url(document.get("docID")),
                },
            }
        )

    source_document = {
        "doc_id": document.get("docID"),
        "submitted_at": document.get("submitDateTime"),
        "description": document.get("docDescription"),
        "is_amendment": str(document.get("docTypeCode")) == "130",
        "url": official_filing_url(document.get("docID")),
    }
    for incoming_period in extracted["periods"]:
        incoming_period["accounting_standard"] = extracted["accounting_standard"]
        for fact in incoming_period.get("metrics", {}).values():
            fact["scope"] = incoming_period.get("scope")
            fact["accounting_standard"] = extracted["accounting_standard"]
            fact["source_doc_id"] = source_document["doc_id"]
            fact["source_submitted_at"] = source_document["submitted_at"]
            fact["source_description"] = source_document["description"]
            fact["source_url"] = source_document["url"]
            fact["source_documents"] = [source_document]

    periods_by_end = {
        period["period_end"]: period
        for period in company.get("periods", [])
        if period.get("period_end")
    }
    for incoming in extracted["periods"]:
        period_end = incoming.get("period_end")
        if not period_end:
            continue
        existing_period = periods_by_end.get(period_end, {})
        merged_period = {**existing_period, **incoming}
        merged_metrics = {
            **existing_period.get("metrics", {}),
            **incoming.get("metrics", {}),
        }
        recompute_derived_metrics(merged_metrics)
        for fact in merged_metrics.values():
            fact.setdefault("scope", merged_period.get("scope"))
            fact.setdefault("accounting_standard", merged_period.get("accounting_standard") or company.get("accounting_standard"))
        merged_period["metrics"] = merged_metrics
        periods_by_end[period_end] = merged_period

    company["periods"] = sorted(
        periods_by_end.values(),
        key=lambda period: period["period_end"],
    )[-10:]
    return company


def recompute_derived_metrics(metrics: dict[str, dict[str, Any]]) -> None:
    for derived_key in DERIVED_METRIC_KEYS:
        metrics.pop(derived_key, None)
    _add_derived_metric(metrics, "operating_margin", "営業利益率", "operating_profit", "revenue", "ratio")
    _add_derived_metric(metrics, "equity_ratio", "自己資本比率（簡易）", "equity", "total_assets", "ratio")
    _add_derived_metric(metrics, "free_cash_flow", "フリーキャッシュフロー", "operating_cash_flow", "investing_cash_flow", "sum")


def remove_invalidated_sources(company: dict[str, Any], invalidated_doc_ids: set[str]) -> bool:
    changed = False
    retained_periods: list[dict[str, Any]] = []
    for period in company.get("periods", []):
        metrics = dict(period.get("metrics", {}))
        for key, fact in list(metrics.items()):
            sources = fact.get("source_documents") or []
            source_ids = {str(source.get("doc_id") or "") for source in sources}
            if fact.get("source_doc_id"):
                source_ids.add(str(fact["source_doc_id"]))
            if source_ids & invalidated_doc_ids:
                metrics.pop(key, None)
                changed = True
        recompute_derived_metrics(metrics)
        for fact in metrics.values():
            fact.setdefault("scope", period.get("scope"))
            fact.setdefault("accounting_standard", period.get("accounting_standard") or company.get("accounting_standard"))
        if metrics:
            retained_periods.append({**period, "metrics": metrics})
        elif period.get("metrics"):
            changed = True
    if changed:
        company["periods"] = retained_periods
        if str((company.get("latest_filing") or {}).get("doc_id") or "") in invalidated_doc_ids:
            company["latest_filing"] = None
    return changed


def refresh_master(output_path: Path) -> tuple[list[dict[str, Any]], str | None]:
    try:
        companies = parse_company_master(http_get(COMPANY_MASTER_URL))
    except Exception as error:  # network/format failures should not destroy current data
        existing = load_json(output_path, {"companies": []})
        return existing.get("companies", []), str(error)
    payload = {
        "meta": {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source": "EDINETコードリスト",
            "source_url": COMPANY_MASTER_URL,
            "companies": len(companies),
        },
        "companies": companies,
    }
    write_json_atomic(output_path, payload)
    return companies, None


def build_dataset(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    master_path = Path(args.master_output)
    master_companies, master_error = refresh_master(master_path)
    if args.master_only:
        if master_error:
            print(f"Company master refresh skipped: {master_error}", file=sys.stderr)
            return 1 if not master_companies else 0
        print(f"Updated company master: {len(master_companies)} companies")
        return 0

    api_key = os.environ.get("EDINET_API_KEY", "").strip()
    if not api_key:
        print("EDINET_API_KEY is not configured; company master only was refreshed")
        return 0

    universe = load_universe(Path(args.universe))
    existing_payload = load_json(
        output_path,
        {
            "meta": {"status": "setup_required"},
            "companies": [],
        },
    )
    existing_companies = {
        company["ticker"]: company
        for company in existing_payload.get("companies", [])
        if company.get("ticker")
    }
    existing_meta = existing_payload.get("meta", {})
    existing_coverage = existing_meta.get("coverage", {})
    covered_tickers = {
        ticker
        for ticker, company in existing_companies.items()
        if company.get("periods")
    }
    missing_tickers = universe - covered_tickers
    previously_bootstrapped = bool(existing_coverage.get("bootstrap_complete"))
    bootstrap = bool(missing_tickers) or not previously_bootstrapped
    scan_universe = (missing_tickers or universe) if bootstrap else universe
    lookback = args.lookback_days or (args.bootstrap_days if bootstrap else args.daily_days)
    end_date = parse_date(args.end_date) if args.end_date else dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    if end_date is None:
        raise ValueError("--end-date must be YYYY-MM-DD")

    client = EdinetClient(
        api_key,
        minimum_interval=args.minimum_interval,
        timeout=args.timeout,
    )
    retry_dates = []
    for value in existing_meta.get("pending_dates", []):
        parsed = parse_date(str(value))
        if parsed:
            retry_dates.append(parsed)

    documents, warnings, successful_dates, failed_dates, invalidated_doc_ids = discover_documents(
        client,
        end_date=end_date,
        lookback_days=lookback,
        universe=scan_universe,
        stop_when_complete=bootstrap,
        retry_dates=retry_dates,
    )

    known_doc_ids = {
        str(document.get("docID") or "")
        for ticker_documents in documents.values()
        for document in ticker_documents
    }
    for pending_document in existing_meta.get("pending_documents", []):
        doc_id = str(pending_document.get("docID") or "")
        ticker = ticker_from_sec_code(pending_document.get("secCode"))
        if not doc_id or doc_id in known_doc_ids or doc_id in invalidated_doc_ids:
            continue
        if not ticker or (universe and ticker not in universe):
            continue
        documents.setdefault(ticker, []).append(pending_document)
        known_doc_ids.add(doc_id)
    for ticker_documents in documents.values():
        ticker_documents.sort(key=lambda document: document.get("submitDateTime") or "")

    invalidation_changed_data = False
    if invalidated_doc_ids:
        for ticker, company in list(existing_companies.items()):
            if remove_invalidated_sources(company, invalidated_doc_ids):
                invalidation_changed_data = True
            if not company.get("periods"):
                existing_companies.pop(ticker, None)
        if invalidation_changed_data:
            warnings.append("取下げ・非開示となった書類の値を除外しました。次回のバックフィルで再構築します")

    master_by_ticker = {
        company["ticker"]: company
        for company in master_companies
        if company.get("ticker")
    }
    processed = 0
    failed = 0
    attempted = 0
    pending_documents: list[dict[str, Any]] = []
    for ticker, ticker_documents in sorted(documents.items()):
        for document in ticker_documents:
            if str(document.get("docID") or "") in invalidated_doc_ids:
                continue
            if args.max_documents and attempted >= args.max_documents:
                pending_documents.append(document)
                continue
            attempted += 1
            try:
                archive = client.download_csv(document["docID"])
                extracted = extract_financials(archive, document)
                existing_companies[ticker] = merge_company(
                    existing_companies.get(ticker),
                    document,
                    extracted,
                    master_by_ticker.get(ticker),
                )
                processed += 1
                print(f"Processed {ticker} {document.get('filerName')} ({document['docID']})")
            except Exception as error:
                failed += 1
                pending_documents.append(document)
                warnings.append(f"{document.get('docID')}: {error}")
                print(f"Skipped {document.get('docID')}: {error}", file=sys.stderr)

    pending_by_id = {
        str(document.get("docID")): document
        for document in pending_documents
        if document.get("docID") and str(document.get("docID")) not in invalidated_doc_ids
    }
    pending_documents = sorted(
        pending_by_id.values(),
        key=lambda document: (ticker_from_sec_code(document.get("secCode")), document.get("submitDateTime") or ""),
    )

    companies = sorted(
        existing_companies.values(),
        key=lambda company: (company.get("ticker") or "99999", company.get("name") or ""),
    )
    periods_count = sum(len(company.get("periods", [])) for company in companies)
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    covered_after_run = {
        company.get("ticker")
        for company in companies
        if company.get("ticker") and company.get("periods")
    }
    coverage_complete = universe.issubset(covered_after_run)
    if bootstrap:
        bootstrap_complete = coverage_complete and not failed_dates and not pending_documents and failed == 0
    else:
        bootstrap_complete = previously_bootstrapped and not invalidation_changed_data
    has_partial_failure = bool(warnings or master_error or failed_dates or pending_documents or invalidation_changed_data)
    status = "partial" if companies and has_partial_failure else "ready" if companies else "setup_required"
    payload = {
        "meta": {
            "status": status,
            "generated_at": generated_at,
            "last_scan_date": end_date.isoformat(),
            "lookback_days": lookback,
            "source": "EDINET API v2",
            "source_url": OFFICIAL_GUIDE_URL,
            "method": "EDINETのXBRL変換CSVを本サイトが加工",
            "coverage": {
                "companies": len(companies),
                "periods": periods_count,
                "universe": len(universe),
                "dates_scanned": successful_dates,
                "documents_processed_this_run": processed,
                "documents_pending": len(pending_documents),
                "bootstrap_complete": bootstrap_complete,
            },
            "warnings": warnings[-30:],
            "master_refresh_error": master_error,
            "pending_dates": failed_dates[-30:],
            "pending_documents": pending_documents,
            "invalidated_documents_this_run": len(invalidated_doc_ids),
        },
        "companies": companies,
    }
    write_json_atomic(output_path, payload)
    print(f"Wrote {output_path}: {len(companies)} companies, {periods_count} periods")
    return 0 if companies or not documents else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/financials.json")
    parser.add_argument("--master-output", default="data/company_master.json")
    parser.add_argument("--universe", default="config/company_universe.json")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--lookback-days", type=int, default=0)
    parser.add_argument("--bootstrap-days", type=int, default=420)
    parser.add_argument("--daily-days", type=int, default=8)
    parser.add_argument("--minimum-interval", type=float, default=3.0)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--max-documents", type=int, default=0)
    parser.add_argument("--master-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        return build_dataset(parse_args(argv))
    except (ValueError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
