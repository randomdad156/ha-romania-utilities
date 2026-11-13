"""Unit tests for the pure response parsers (no Home Assistant needed)."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

_PATH = Path(__file__).parents[1] / "custom_components" / "romania_utilities" / "parsers.py"
_spec = importlib.util.spec_from_file_location("ru_parsers", _PATH)
p = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p)

TODAY = date(2026, 11, 13)


# ── helpers ──

def test_to_float_formats():
    assert p.to_float("443,90") == 443.90
    assert p.to_float("1.234,56") == 1234.56
    assert p.to_float("1234.56") == 1234.56
    assert p.to_float("12,00 lei") == 12.0
    assert p.to_float(7) == 7.0
    assert p.to_float("") is None
    assert p.to_float(None) is None
    assert p.to_float(True) is None


def test_to_date_formats():
    assert p.to_date("21.11.2026") == date(2026, 11, 21)
    assert p.to_date("21/11/2026") == date(2026, 11, 21)
    assert p.to_date("20261121") == date(2026, 11, 21)
    assert p.to_date("2026-11-21T00:00:00.000+02:00") == date(2026, 11, 21)
    assert p.to_date("nonsense") is None
    assert p.to_date(None) is None


# ── E·ON ──

EON_RAW = {
    "contract": {"accountContract": "002100000002", "utilityType": "02"},
    "dashboard": {"balance": {"balance": "245,30"}},
    "unpaid": {
        "list": [
            {"invoiceNumber": "F2", "issuedValue": "245,30", "emissionDate": "2026-11-03",
             "maturityDate": "2026-11-17"},
        ]
    },
    "paid": [
        {"invoiceNumber": "F1", "issuedValue": "198,10", "emissionDate": "2026-10-02",
         "maturityDate": "2026-10-16"},
    ],
    "meter_index": {
        "readingPeriod": {"startDate": "2026-11-20", "endDate": "2026-11-27", "inPeriod": False},
        "indexDetails": {"devices": [{"indexes": [
            {"code": "R", "currentValue": 4521, "sentAt": "2026-10-22"},
        ]}]},
    },
    "graphic": {"consumption": [
        {"year": 2026, "month": 9, "consumptionValue": 40},
        {"year": 2026, "month": 10, "consumptionValue": 85.5},
        {"year": 2025, "month": 12, "consumptionValue": 300},
    ]},
    "prosum_balance": None,
}


def test_eon_contract_normalised():
    out = p.parse_eon_contract(EON_RAW, TODAY)
    assert set(out) == set(p.NORMALISED_KEYS)
    assert out["balance"] == 245.30
    assert out["last_invoice_number"] == "F2"
    assert out["last_invoice_amount"] == 245.30
    assert out["last_invoice_issue_date"] == date(2026, 11, 3)
    assert out["last_invoice_due_date"] == date(2026, 11, 17)
    assert out["last_invoice_paid"] is False
    assert out["unpaid_invoices"] == 1
    assert out["next_due_date"] == date(2026, 11, 17)
    assert out["days_until_due"] == 4
    assert out["consumption_unit"] == "m³"
    assert out["consumption_last_month"] == 85.5
    assert out["consumption_ytd"] == 125.5
    assert out["reading_window_start"] == date(2026, 11, 20)
    assert out["reading_window_end"] == date(2026, 11, 27)
    assert out["reading_window_open"] is False
    assert out["last_index"] == 4521
    assert out["last_index_date"] == date(2026, 10, 22)
    assert out["injected_index"] is None


def test_eon_all_paid_and_missing_parts():
    raw = {**EON_RAW, "unpaid": {"list": []}, "meter_index": None, "graphic": None,
           "dashboard": {"total": 0}}
    out = p.parse_eon_contract(raw, TODAY)
    assert out["balance"] == 0
    assert out["unpaid_invoices"] == 0
    assert out["last_invoice_number"] == "F1"
    assert out["last_invoice_paid"] is True
    assert out["days_until_due"] is None
    assert out["reading_window_open"] is None


def test_eon_prosumer_register():
    raw = {**EON_RAW, "meter_index": {"indexDetails": {"devices": [{"indexes": [
        {"code": "R", "currentValue": 100}, {"code": "P", "currentValue": 900},
    ]}]}}, "prosum_balance": {"balance": "-50,00"}}
    out = p.parse_eon_contract(raw, TODAY)
    assert out["last_index"] == 100
    assert out["injected_index"] == 900
    assert out["prosumer_balance"] == -50.0


def test_eon_flatten_collective_contract():
    items = [
        {"contractDetails": {"accountContract": "009900000001"},
         "subContracts": [
             {"contractDetails": {"accountContract": "002100000002", "utilityType": "02"}},
             {"contractDetails": {"accountContract": "002200000003", "utilityType": "01"}},
         ]},
        {"accountContract": "002100000004"},
    ]
    flat = p.eon_flatten_contracts(items)
    numbers = [p.eon_contract_label(i)[0] for i in flat]
    assert numbers == ["009900000001", "002100000002", "002200000003", "002100000004"]
    assert flat[1]["_parent"] == "009900000001"


# ── Hidroelectrica ──

def _wrap(data=None, **result):
    return {"responsestatus": 1, "status_code": 200, "result": {"Data": data, **result}}


HIDRO_RAW = {
    "bill": {"result": {"billamount": "443,90", "rembalance": "443,90",
                        "invoicenumber": "ID_05", "duedate": "20261121"}},
    "window": _wrap({"OpeningDate": "22", "ClosingDate": "26", "Is_Window_Open": "0"}),
    "history": _wrap([
        {"Date": "20/09/2025", "Index": 15000, "Registers": "1.8.0"},
        {"Date": "20/12/2025", "Index": 15400, "Registers": "1.8.0"},
        {"Date": "20/09/2026", "Index": 16500, "Registers": "1.8.0"},
        {"Date": "20/10/2026", "Index": 16866, "Registers": "1.8.0"},
        {"Date": "20/10/2026", "Index": 3000, "Registers": "1.8.0_P"},
    ]),
    "billing_history": {"result": {"objBillingHistoryEntity": None,
                                   "objBillingPaymentHistoryEntity": [
                                       {"channel": "Comp ANRE", "paymentDate": "15/06/2026",
                                        "amount": "120,50"},
                                       {"channel": "Card", "paymentDate": "15/07/2026",
                                        "amount": "300,00"},
                                   ]}},
}


def test_hidro_account_normalised():
    out = p.parse_hidro_account(HIDRO_RAW, TODAY)
    assert set(out) == set(p.NORMALISED_KEYS)
    assert out["balance"] == 443.90
    assert out["last_invoice_amount"] == 443.90
    assert out["last_invoice_number"] == "ID_05"
    assert out["last_invoice_due_date"] == date(2026, 11, 21)
    assert out["last_invoice_paid"] is False
    assert out["unpaid_invoices"] == 1
    assert out["days_until_due"] == 8
    assert out["reading_window_start"] == date(2026, 11, 22)
    assert out["reading_window_end"] == date(2026, 11, 26)
    assert out["reading_window_open"] is False
    assert out["last_index"] == 16866
    assert out["consumption_last_month"] == 366
    assert out["consumption_ytd"] == 1466
    assert out["injected_index"] == 3000
    assert out["compensation_ytd"] == 120.50


def test_hidro_paid_and_window_rolls_to_next_month():
    raw = {**HIDRO_RAW,
           "bill": {"result": {"billamount": "200,00", "rembalance": "0,00", "duedate": "20261020"}},
           "window": _wrap({"OpeningDate": "02", "ClosingDate": "06", "Is_Window_Open": "0"})}
    out = p.parse_hidro_account(raw, TODAY)
    assert out["unpaid_invoices"] == 0
    assert out["last_invoice_paid"] is True
    assert out["days_until_due"] is None
    assert out["reading_window_start"] == date(2026, 12, 2)
    assert out["reading_window_end"] == date(2026, 12, 6)


def test_hidro_accounts():
    resp = _wrap({"Table1": [{"UtilityAccountNumber": "8000000001", "AccountNumber": "A1",
                              "Pod": "RO1", "BpNumber": "B1"}],
                  "Table2": [{"UtilityAccountNumber": "8000000001"}]})
    accounts = p.hidro_accounts(resp)
    assert len(accounts) == 1
    assert accounts[0]["uan"] == "8000000001"
    assert accounts[0]["pod"] == "RO1"


def test_empty_responses_never_raise():
    assert p.parse_eon_contract({}, TODAY) == p.empty_contract()
    out = p.parse_hidro_account({}, TODAY)
    assert out["consumption_unit"] == "kWh"
    assert out["balance"] is None


def test_eon_kind_and_unit_codes():
    assert p.eon_kind({"utilityType": "01"}) == "Electricity"
    assert p.eon_kind({"utilityType": "02"}) == "Gas"
    assert p.eon_kind({"utilityType": "00"}) == "Duo"
    assert p.eon_unit({"utilityType": "01"}, {"um": "KWH"}) == "kWh"
    assert p.eon_unit({"utilityType": "01"}, {"um": "M3"}) == "m³"
    assert p.eon_unit({"utilityType": "02"}, None) == "m³"
    assert p.eon_unit({"utilityType": "01"}, None) == "kWh"


def test_eon_contract_options_names():
    items = [{"contractDetails": {"accountContract": "009900000001", "utilityType": "00"},
              "subContracts": [{"contractDetails": {"accountContract": "002200000003",
                                                    "utilityType": "01", "productName": "Duo"}}]}]
    opts = p.eon_contract_options(items)
    assert opts["002200000003"]["name"] == "E·ON Electricity 002200000003"
    assert opts["002200000003"]["parent"] == "009900000001"
    assert "sub-contract of 009900000001" in opts["002200000003"]["label"]
    assert opts["009900000001"]["kind"] == "Duo"


def test_hidro_last_invoice_from_history_when_paid():
    raw = {**HIDRO_RAW,
           "bill": {"result": {"billamount": "0,00", "rembalance": "0,00", "duedate": "20261113"}},
           "billing_history": {"result": {"objBillingHistoryEntity": [
               {"amount": "150,20", "invoiceDate": "2026-09-14", "dueDate": "2026-09-29", "invoiceId": "A"},
               {"amount": "180,75", "invoiceDate": "2026-10-14", "dueDate": "2026-10-29", "invoiceId": "B"},
           ], "objBillingPaymentHistoryEntity": None}}}
    out = p.parse_hidro_account(raw, TODAY)
    assert out["last_invoice_amount"] == 180.75
    assert out["last_invoice_issue_date"] == date(2026, 10, 14)
    assert out["last_invoice_due_date"] == date(2026, 10, 29)
    assert out["last_invoice_number"] == "B"
    assert out["last_invoice_paid"] is True
    assert out["unpaid_invoices"] == 0
