import csv
import datetime as dt
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import edinet_sync  # noqa: E402


HEADERS = [
    "要素ID",
    "項目名",
    "コンテキストID",
    "相対年度",
    "連結・個別",
    "期間・時点",
    "ユニットID",
    "単位",
    "値",
]


def make_csv_zip(rows):
    text = io.StringIO()
    writer = csv.writer(text, delimiter="\t", quotechar='"', lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    writer.writerow(HEADERS)
    writer.writerows(rows)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("XBRL_TO_CSV/PublicDoc/report.csv", text.getvalue().encode("utf-16"))
        zipped.writestr("XBRL_TO_CSV/AuditDoc/audit.csv", text.getvalue().encode("utf-16"))
    return archive.getvalue()


class NumberParsingTests(unittest.TestCase):
    def test_explicit_dash_is_zero(self):
        self.assertEqual(edinet_sync.parse_number("-"), 0)

    def test_blank_is_missing(self):
        self.assertIsNone(edinet_sync.parse_number(""))

    def test_parentheses_are_negative(self):
        self.assertEqual(edinet_sync.parse_number("(1,234)"), -1234)


class ExtractorTests(unittest.TestCase):
    def setUp(self):
        self.document = {
            "docID": "S100TEST",
            "edinetCode": "E00001",
            "secCode": "12340",
            "periodStart": "2024-04-01",
            "periodEnd": "2025-03-31",
        }

    def test_extracts_consolidated_history_and_derived_metrics(self):
        rows = [
            ["jpdei_cor:WhetherConsolidatedFinancialStatementsArePreparedDEI", "連結財務諸表作成の有無", "FilingDateInstant", "", "その他", "2025-06-25", "-", "-", "true"],
            ["jpdei_cor:AccountingStandardsDEI", "会計基準", "FilingDateInstant", "", "その他", "2025-06-25", "-", "-", "Japan GAAP"],
            ["jpcrp_cor:NetSalesSummaryOfBusinessResults", "売上高", "CurrentYearDuration", "当期", "その他", "2024-04-01～2025-03-31", "JPY", "円", "1,200,000,000"],
            ["jpcrp_cor:NetSalesSummaryOfBusinessResults", "売上高", "Prior1YearDuration", "前期", "その他", "2023-04-01～2024-03-31", "JPY", "円", "1,000,000,000"],
            ["jpcrp_cor:OperatingIncomeLossSummaryOfBusinessResults", "営業利益", "CurrentYearDuration", "当期", "その他", "2024-04-01～2025-03-31", "JPY", "円", "120,000,000"],
            ["jpcrp_cor:OperatingIncomeLossSummaryOfBusinessResults", "営業利益", "Prior1YearDuration", "前期", "その他", "2023-04-01～2024-03-31", "JPY", "円", "80,000,000"],
            ["jpcrp_cor:TotalAssetsSummaryOfBusinessResults", "総資産", "CurrentYearInstant", "当期", "その他", "2025-03-31", "JPY", "円", "2,000,000,000"],
            ["jpcrp_cor:NetAssetsSummaryOfBusinessResults", "純資産", "CurrentYearInstant", "当期", "その他", "2025-03-31", "JPY", "円", "800,000,000"],
            ["jpcrp_cor:NetSalesSummaryOfBusinessResults", "売上高", "CurrentYearDuration_NonConsolidatedMember", "当期", "個別", "2024-04-01～2025-03-31", "JPY", "円", "700,000,000"],
            ["jppfs_cor:NetSales", "セグメント売上", "CurrentYearDuration_OperatingSegmentsMember", "当期", "連結", "2024-04-01～2025-03-31", "JPY", "円", "999,000,000"],
        ]
        result = edinet_sync.extract_financials(make_csv_zip(rows), self.document)
        self.assertEqual(result["accounting_standard"], "日本基準")
        self.assertEqual(result["scope"], "consolidated")
        self.assertEqual(len(result["periods"]), 2)
        current = result["periods"][-1]
        self.assertEqual(current["period_end"], "2025-03-31")
        self.assertEqual(current["metrics"]["revenue"]["value"], 1_200_000_000)
        self.assertEqual(current["metrics"]["operating_margin"]["value"], 10)
        self.assertEqual(current["metrics"]["equity_ratio"]["value"], 40)

    def test_nonconsolidated_company_uses_separate_facts(self):
        rows = [
            ["jpdei_cor:WhetherConsolidatedFinancialStatementsArePreparedDEI", "連結財務諸表作成の有無", "FilingDateInstant", "", "その他", "2025-06-25", "-", "-", "false"],
            ["jpcrp_cor:NetSalesSummaryOfBusinessResults", "売上高", "CurrentYearDuration_NonConsolidatedMember", "当期", "個別", "2024-04-01～2025-03-31", "JPY", "円", "700,000,000"],
        ]
        result = edinet_sync.extract_financials(make_csv_zip(rows), self.document)
        self.assertEqual(result["scope"], "separate")
        self.assertEqual(result["periods"][0]["metrics"]["revenue"]["value"], 700_000_000)

    def test_current_us_gaap_and_research_aliases(self):
        rows = [
            ["jpdei_cor:AccountingStandardsDEI", "会計基準", "FilingDateInstant", "", "その他", "2025-06-25", "-", "-", "US GAAP"],
            ["jpcrp_cor:RevenuesUSGAAPSummaryOfBusinessResults", "売上高", "CurrentYearDuration", "当期", "その他", "2024-04-01～2025-03-31", "JPY", "円", "1,200,000,000"],
            ["jpcrp_cor:EquityAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults", "親会社株主資本", "CurrentYearInstant", "当期", "その他", "2025-03-31", "JPY", "円", "500,000,000"],
            ["jpcrp_cor:ResearchAndDevelopmentExpensesResearchAndDevelopmentActivities", "研究開発費", "CurrentYearDuration", "当期", "その他", "2024-04-01～2025-03-31", "JPY", "円", "25,000,000"],
        ]
        result = edinet_sync.extract_financials(make_csv_zip(rows), self.document)
        metrics = result["periods"][0]["metrics"]
        self.assertEqual(result["accounting_standard"], "米国基準")
        self.assertEqual(metrics["revenue"]["value"], 1_200_000_000)
        self.assertEqual(metrics["equity"]["value"], 500_000_000)
        self.assertEqual(metrics["research_and_development"]["value"], 25_000_000)


class MasterListTests(unittest.TestCase):
    def test_parses_cp932_master_list_with_title_row(self):
        text = io.StringIO()
        writer = csv.writer(text, lineterminator="\r\n")
        writer.writerow(["EDINETコードリスト"])
        writer.writerow([
            "ＥＤＩＮＥＴコード",
            "提出者種別",
            "上場区分",
            "連結の有無",
            "資本金",
            "決算日",
            "提出者名",
            "提出者名（英字）",
            "提出者名（ヨミ）",
            "所在地",
            "提出者業種",
            "証券コード",
            "提出者法人番号",
        ])
        writer.writerow(["E02144", "内国法人", "上場", "有", "", "03-31", "テスト自動車株式会社", "Test Motor", "テストジドウシャ", "東京都", "輸送用機器", "72030", "1234567890123"])
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            zipped.writestr("EdinetcodeDlInfo.csv", text.getvalue().encode("cp932"))
        companies = edinet_sync.parse_company_master(archive.getvalue())
        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["ticker"], "7203")
        self.assertEqual(companies[0]["industry"], "輸送用機器")

    def test_preserves_alphanumeric_ticker(self):
        self.assertEqual(edinet_sync.normalize_sec_code("１３０ａ０"), "130A0")
        self.assertEqual(edinet_sync.ticker_from_sec_code("130A0"), "130A")


class DocumentFilterTests(unittest.TestCase):
    def test_accepts_a_visible_annual_report(self):
        document = {
            "docID": "S100TEST",
            "edinetCode": "E00001",
            "secCode": "72030",
            "docTypeCode": "120",
            "csvFlag": "1",
            "withdrawalStatus": "0",
            "disclosureStatus": "0",
            "legalStatus": "1",
        }
        self.assertTrue(edinet_sync.is_usable_annual_report(document, {"7203"}))

    def test_rejects_withdrawn_or_outside_universe(self):
        document = {
            "docID": "S100TEST",
            "edinetCode": "E00001",
            "secCode": "72030",
            "docTypeCode": "120",
            "csvFlag": "1",
            "withdrawalStatus": "2",
            "disclosureStatus": "0",
            "legalStatus": "1",
        }
        self.assertFalse(edinet_sync.is_usable_annual_report(document, {"7203"}))
        document["withdrawalStatus"] = "0"
        self.assertFalse(edinet_sync.is_usable_annual_report(document, {"6758"}))

    def test_accepts_alphanumeric_ticker(self):
        document = {
            "docID": "S100ALPHA",
            "edinetCode": "E00002",
            "secCode": "130A0",
            "docTypeCode": "120",
            "csvFlag": "1",
            "withdrawalStatus": "0",
            "disclosureStatus": "0",
            "legalStatus": "1",
        }
        self.assertTrue(edinet_sync.is_usable_annual_report(document, {"130A"}))

    def test_discovery_keeps_original_before_amendment(self):
        def document(ticker, doc_id, doc_type, submitted):
            return {
                "docID": doc_id,
                "edinetCode": f"E{ticker}",
                "secCode": f"{ticker}0",
                "docTypeCode": doc_type,
                "csvFlag": "1",
                "withdrawalStatus": "0",
                "disclosureStatus": "0",
                "legalStatus": "1",
                "submitDateTime": submitted,
            }

        class Client:
            def list_documents(self, date):
                return {
                    dt.date(2025, 6, 25): [document("1234", "AMEND", "130", "2025-06-25 12:00")],
                    dt.date(2025, 6, 24): [document("1234", "ORIGINAL", "120", "2025-06-24 09:00")],
                    dt.date(2025, 6, 23): [document("5678", "OTHER", "120", "2025-06-23 09:00")],
                }.get(date, [])

        found, warnings, dates, failed_dates, invalidated = edinet_sync.discover_documents(
            Client(),
            end_date=dt.date(2025, 6, 25),
            lookback_days=3,
            universe={"1234", "5678"},
            stop_when_complete=True,
        )
        self.assertEqual([item["docID"] for item in found["1234"]], ["ORIGINAL", "AMEND"])
        self.assertEqual(warnings, [])
        self.assertEqual(dates, 3)
        self.assertEqual(failed_dates, [])
        self.assertEqual(invalidated, set())


class MergeTests(unittest.TestCase):
    def test_partial_amendment_preserves_other_metrics(self):
        existing = {
            "periods": [
                {
                    "period_start": "2024-04-01",
                    "period_end": "2025-03-31",
                    "scope": "consolidated",
                    "metrics": {
                        "revenue": {"value": 100, "unit": "JPY"},
                        "operating_profit": {"value": 10, "unit": "JPY"},
                    },
                }
            ]
        }
        document = {
            "docID": "S100AMEND",
            "docTypeCode": "130",
            "edinetCode": "E00001",
            "secCode": "12340",
            "filerName": "テスト株式会社",
            "periodStart": "2024-04-01",
            "periodEnd": "2025-03-31",
        }
        extracted = {
            "accounting_standard": "日本基準",
            "scope": "consolidated",
            "periods": [
                {
                    "period_start": "2024-04-01",
                    "period_end": "2025-03-31",
                    "scope": "consolidated",
                    "metrics": {"revenue": {"value": 105, "unit": "JPY"}},
                }
            ],
        }
        company = edinet_sync.merge_company(existing, document, extracted, None)
        metrics = company["periods"][0]["metrics"]
        self.assertEqual(metrics["revenue"]["value"], 105)
        self.assertEqual(metrics["operating_profit"]["value"], 10)
        self.assertAlmostEqual(metrics["operating_margin"]["value"], 9.5238)
        self.assertEqual(metrics["revenue"]["source_doc_id"], "S100AMEND")
        self.assertEqual(metrics["operating_margin"]["source_doc_id"], "S100AMEND")
        self.assertTrue(company["latest_filing"]["is_amendment"])

    def test_old_period_amendment_does_not_replace_latest_filing(self):
        existing = {
            "accounting_standard": "日本基準",
            "scope": "consolidated",
            "latest_filing": {
                "doc_id": "LATEST",
                "period_end": "2025-03-31",
                "submitted_at": "2025-06-25 09:00",
            },
            "periods": [],
        }
        document = {
            "docID": "OLD-AMENDMENT",
            "docTypeCode": "130",
            "edinetCode": "E00001",
            "secCode": "12340",
            "filerName": "テスト株式会社",
            "periodStart": "2022-04-01",
            "periodEnd": "2023-03-31",
            "submitDateTime": "2025-07-01 09:00",
        }
        extracted = {
            "accounting_standard": "日本基準",
            "scope": "consolidated",
            "periods": [
                {
                    "period_start": "2022-04-01",
                    "period_end": "2023-03-31",
                    "scope": "consolidated",
                    "metrics": {"revenue": {"value": 100, "unit": "JPY"}},
                }
            ],
        }
        company = edinet_sync.merge_company(existing, document, extracted, None)
        self.assertEqual(company["latest_filing"]["doc_id"], "LATEST")

    def test_invalidated_source_is_removed(self):
        company = {
            "latest_filing": {"doc_id": "WITHDRAWN"},
            "periods": [
                {
                    "period_end": "2025-03-31",
                    "metrics": {
                        "revenue": {
                            "value": 100,
                            "unit": "JPY",
                            "source_doc_id": "WITHDRAWN",
                        }
                    },
                }
            ],
        }
        self.assertTrue(edinet_sync.remove_invalidated_sources(company, {"WITHDRAWN"}))
        self.assertEqual(company["periods"], [])
        self.assertIsNone(company["latest_filing"])


class ClientRetryTests(unittest.TestCase):
    def test_retries_json_level_error(self):
        client = edinet_sync.EdinetClient("secret", minimum_interval=0, max_attempts=2)
        responses = [
            (json.dumps({"metadata": {"status": "429", "message": "busy"}}).encode(), "application/json"),
            (json.dumps({"metadata": {"status": "200"}, "results": []}).encode(), "application/json"),
        ]
        with mock.patch.object(client, "_request", side_effect=responses) as request, mock.patch.object(edinet_sync.time, "sleep"):
            self.assertEqual(client.list_documents(dt.date(2025, 6, 25)), [])
        self.assertEqual(request.call_count, 2)


class StaticDataTests(unittest.TestCase):
    def test_site_fixture_is_valid_json(self):
        payload = json.loads((ROOT / "tests" / "fixtures" / "site_data.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["meta"]["is_demo"], True)
        self.assertGreaterEqual(len(payload["companies"]), 2)


if __name__ == "__main__":
    unittest.main()
