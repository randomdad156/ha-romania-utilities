"""Turn raw provider responses into one normalised shape per contract.

Pure functions, standard library only, so they can be unit-tested without
Home Assistant. Every parser is tolerant: a missing or renamed field leaves a
single value as ``None`` instead of failing the whole update.

Normalised contract dict (all keys always present):

    balance                 float | None   amount still to pay, RON
    last_invoice_number     str | None
    last_invoice_amount     float | None   RON
    last_invoice_issue_date date | None
    last_invoice_due_date   date | None
    last_invoice_paid       bool | None
    next_due_date           date | None    earliest due date of unpaid invoices
    days_until_due          int | None     negative when overdue
    unpaid_invoices         int | None
    consumption_unit        str | None     "kWh" or "m³"
    consumption_last_month  float | None
    consumption_ytd         float | None
    reading_window_start    date | None
    reading_window_end      date | None
    reading_window_open     bool | None
    last_index              float | None
    last_index_date         date | None
    injected_index          float | None   prosumer export register, kWh
    prosumer_balance        float | None   RON (E·ON prosumer balance)
    compensation_ytd        float | None   RON (Hidroelectrica ANRE compensations)
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Any

NORMALISED_KEYS = (
    "balance",
    "last_invoice_number",
    "last_invoice_amount",
    "last_invoice_issue_date",
    "last_invoice_due_date",
    "last_invoice_paid",
    "next_due_date",
    "days_until_due",
    "unpaid_invoices",
    "consumption_unit",
    "consumption_last_month",
    "consumption_ytd",
    "reading_window_start",
    "reading_window_end",
    "reading_window_open",
    "last_index",
    "last_index_date",
    "injected_index",
    "prosumer_balance",
    "compensation_ytd",
)


def empty_contract() -> dict[str, Any]:
    return dict.fromkeys(NORMALISED_KEYS)


# ── generic helpers ──────────────────────────────────────────────────────────

_DATE_FORMATS = (
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%Y%m%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%d.%m.%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
)


def to_date(value: Any) -> date | None:
    """Parse the date formats both providers use; None when unparseable."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # ISO with fractional seconds / timezone: keep the date part only.
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def to_float(value: Any) -> float | None:
    """Numbers arrive as numbers, '443,90', '1.234,56' or '1234.56'."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip().replace("lei", "").replace("RON", "").replace(" ", "")
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def first(mapping: Any, *keys: str) -> Any:
    """First non-empty value among ``keys`` in ``mapping``."""
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", "None"):
            return value
    return None


def as_list(data: Any, *keys: str) -> list[dict]:
    """A list of dicts, whether the response is a list or wraps one."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in keys or ("list", "items", "data", "content"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def days_between(today: date, target: date | None) -> int | None:
    return (target - today).days if target else None


def _previous_month(today: date) -> tuple[int, int]:
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


# ── E·ON ─────────────────────────────────────────────────────────────────────

_EON_AMOUNT_KEYS = (
    "issuedValue",
    "invoiceValue",
    "totalValue",
    "amount",
    "value",
    "balanceValue",
)
_EON_ISSUE_KEYS = ("emissionDate", "issueDate", "invoiceDate", "issuedDate")
_EON_DUE_KEYS = ("maturityDate", "dueDate", "scadenceDate")
_EON_NUMBER_KEYS = ("invoiceNumber", "number", "invoiceNo", "id")


def _eon_invoice(item: dict, paid: bool) -> dict[str, Any]:
    return {
        "number": str(first(item, *_EON_NUMBER_KEYS) or "") or None,
        "amount": to_float(first(item, *_EON_AMOUNT_KEYS)),
        "issue_date": to_date(first(item, *_EON_ISSUE_KEYS)),
        "due_date": to_date(first(item, *_EON_DUE_KEYS)),
        "paid": paid,
    }


def _invoice_sort_key(inv: dict[str, Any]) -> date:
    return inv["issue_date"] or inv["due_date"] or date.min


def eon_balance(dashboard: Any) -> float | None:
    """Balance from invoices/dashboard-data (or invoice-balance)."""
    if not isinstance(dashboard, dict):
        return None
    keys = ("balance", "total", "totalBalance", "balancePay", "balanceValue")
    nested = dashboard.get("balance")
    if isinstance(nested, dict):
        value = to_float(first(nested, *keys))
        if value is not None:
            return value
    for key in keys:
        value = dashboard.get(key)
        if not isinstance(value, dict):
            parsed = to_float(value)
            if parsed is not None:
                return parsed
    return None


def eon_registers(meter_index: Any) -> list[dict]:
    if not isinstance(meter_index, dict):
        return []
    details = meter_index.get("indexDetails") or {}
    registers: list[dict] = []
    for device in details.get("devices") or []:
        if isinstance(device, dict):
            registers.extend(r for r in device.get("indexes") or [] if isinstance(r, dict))
    return registers


def _eon_register(meter_index: Any, code: str) -> dict | None:
    for register in eon_registers(meter_index):
        if str(register.get("code") or "").upper() == code:
            return register
    return None


def _register_value(register: dict | None) -> float | None:
    return to_float(first(register, "currentValue", "oldSelfIndexValue", "value", "oldValue"))


def eon_unit(contract_info: Any, graphic: Any) -> str:
    unit = str(first(graphic, "unit", "measureUnit", "um") or "").lower()
    if unit in ("mc", "m3", "m³"):
        return "m³"
    if unit == "kwh":
        return "kWh"
    text = " ".join(
        str(first(contract_info, key) or "")
        for key in ("utilityType", "serviceType", "division", "productName", "contractName", "type")
    ).lower()
    return "m³" if ("gaz" in text or "gas" in text) and "kwh" not in unit else "kWh"


def parse_eon_contract(raw: dict[str, Any], today: date) -> dict[str, Any]:
    """Normalise the responses fetched for one E·ON account contract.

    ``raw`` keys: contract, dashboard, unpaid, paid, meter_index, graphic,
    prosum_balance. Any of them may be None.
    """
    out = empty_contract()
    out["balance"] = eon_balance(raw.get("dashboard"))

    unpaid = [_eon_invoice(i, False) for i in as_list(raw.get("unpaid"))]
    paid = [_eon_invoice(i, True) for i in as_list(raw.get("paid"))]
    if raw.get("unpaid") is not None:
        out["unpaid_invoices"] = len(unpaid)

    invoices = sorted(unpaid + paid, key=_invoice_sort_key, reverse=True)
    if invoices:
        last = invoices[0]
        out["last_invoice_number"] = last["number"]
        out["last_invoice_amount"] = last["amount"]
        out["last_invoice_issue_date"] = last["issue_date"]
        out["last_invoice_due_date"] = last["due_date"]
        out["last_invoice_paid"] = last["paid"]

    due_dates = sorted(i["due_date"] for i in unpaid if i["due_date"])
    out["next_due_date"] = due_dates[0] if due_dates else None
    out["days_until_due"] = days_between(today, out["next_due_date"])

    # Consumption from the monthly chart.
    graphic = raw.get("graphic")
    rows = as_list(graphic, "consumption", "list", "items")
    if rows:
        out["consumption_unit"] = eon_unit(raw.get("contract"), graphic)
        prev_year, prev_month = _previous_month(today)
        ytd = 0.0
        found_ytd = False
        for row in rows:
            year = int(to_float(row.get("year")) or 0)
            month = int(to_float(row.get("month")) or 0)
            value = to_float(first(row, "consumptionValue", "value", "consumption"))
            if value is None:
                continue
            if (year, month) == (prev_year, prev_month):
                out["consumption_last_month"] = round(value, 3)
            if year == today.year:
                ytd += value
                found_ytd = True
        if found_ytd:
            out["consumption_ytd"] = round(ytd, 3)

    # Meter reading window and index.
    meter_index = raw.get("meter_index")
    if isinstance(meter_index, dict):
        period = meter_index.get("readingPeriod") or {}
        out["reading_window_start"] = to_date(period.get("startDate"))
        out["reading_window_end"] = to_date(period.get("endDate"))
        in_period = first(period, "inPeriod", "allowedReading")
        if in_period is not None:
            out["reading_window_open"] = bool(in_period)
        elif out["reading_window_start"] and out["reading_window_end"]:
            out["reading_window_open"] = (
                out["reading_window_start"] <= today <= out["reading_window_end"]
            )
        consumption_register = _eon_register(meter_index, "R") or next(
            iter(eon_registers(meter_index)), None
        )
        out["last_index"] = _register_value(consumption_register)
        out["last_index_date"] = to_date(first(consumption_register, "sentAt", "readingDate"))
        out["injected_index"] = _register_value(_eon_register(meter_index, "P"))
        if out["consumption_unit"] is None and consumption_register is not None:
            out["consumption_unit"] = eon_unit(raw.get("contract"), None)

    prosum = raw.get("prosum_balance")
    if isinstance(prosum, dict) and prosum:
        out["prosumer_balance"] = eon_balance(prosum)
    return out


def eon_contract_label(item: dict) -> tuple[str, str]:
    """(account contract, human label) for the contract selection list."""
    number = str(first(item, "accountContract", "contractAccount", "accountContractNumber") or "")
    kind = first(item, "utilityType", "serviceType", "division", "productName", "contractName")
    address = item.get("consumptionPointAddress") or item.get("address")
    if isinstance(address, dict):
        address = ", ".join(
            str(address[k]) for k in ("street", "streetNumber", "locality", "city") if address.get(k)
        )
    parts = [number]
    if kind:
        parts.append(str(kind))
    if address:
        parts.append(str(address))
    return number, " · ".join(parts)


def eon_flatten_contracts(items: list[dict]) -> list[dict]:
    """Contracts and their subcontracts (collective "DUO" contracts) as one list."""
    flat: list[dict] = []
    seen: set[str] = set()

    def add(entry: Any, parent: str | None) -> None:
        if not isinstance(entry, dict):
            return
        details = entry.get("contractDetails") if isinstance(entry.get("contractDetails"), dict) else entry
        number, _ = eon_contract_label(details)
        if number and number not in seen:
            seen.add(number)
            flat.append({**details, "_parent": parent})
        for sub in entry.get("subContracts") or entry.get("subcontracts") or []:
            add(sub, number or parent)

    for item in items:
        add(item, None)
    return flat


# ── Hidroelectrica ───────────────────────────────────────────────────────────

REG_CONSUMPTION = "1.8.0"
REG_INJECTED = "1.8.0_P"


def hidro_payload(resp: Any) -> Any:
    """``result.Data`` of an iHidro response (dict or list)."""
    if isinstance(resp, dict) and isinstance(resp.get("result"), dict):
        return resp["result"].get("Data")
    return None


def hidro_result(resp: Any) -> dict:
    if isinstance(resp, dict) and isinstance(resp.get("result"), dict):
        return resp["result"]
    return {}


def hidro_accounts(user_setting: Any) -> list[dict[str, str]]:
    """Accounts from GetUserSetting (Table1 + Table2), de-duplicated."""
    data = hidro_payload(user_setting)
    if not isinstance(data, dict):
        return []
    accounts: list[dict[str, str]] = []
    seen: set[str] = set()
    for table in ("Table1", "Table2"):
        for entry in data.get(table) or []:
            if not isinstance(entry, dict):
                continue
            uan = str(entry.get("UtilityAccountNumber") or "").strip()
            if not uan or uan in seen:
                continue
            seen.add(uan)
            accounts.append(
                {
                    "uan": uan,
                    "account_number": str(entry.get("AccountNumber") or ""),
                    "pod": str(entry.get("Pod") or ""),
                    "bp_number": str(entry.get("BpNumber") or ""),
                    "address": str(first(entry, "Address", "ServiceAddress", "address") or ""),
                }
            )
    return accounts


def hidro_readings(history: Any, register: str = REG_CONSUMPTION) -> list[tuple[date, float, dict]]:
    """Index readings for one register, oldest first."""
    data = hidro_payload(history)
    rows: list[tuple[date, float, dict]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        if (item.get("Registers") or REG_CONSUMPTION) != register:
            continue
        when = to_date(item.get("Date"))
        index = to_float(item.get("Index"))
        if when is not None and index is not None:
            rows.append((when, index, item))
    rows.sort(key=lambda row: row[0])
    return rows


def _window_date(value: Any, today: date) -> date | None:
    """Window dates come as full dates or as a day of the current month."""
    parsed = to_date(value)
    if parsed:
        return parsed
    day = to_float(value)
    if day is None or not 1 <= day <= 31:
        return None
    last_day = monthrange(today.year, today.month)[1]
    return today.replace(day=min(int(day), last_day))


def _next_month(day: date) -> date:
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return day.replace(year=year, month=month, day=min(day.day, monthrange(year, month)[1]))


def parse_hidro_account(raw: dict[str, Any], today: date) -> dict[str, Any]:
    """Normalise the responses fetched for one iHidro account.

    ``raw`` keys: bill, window, history, billing_history. Any may be None.
    """
    out = empty_contract()
    out["consumption_unit"] = "kWh"

    bill = hidro_result(raw.get("bill"))
    if bill:
        out["balance"] = to_float(bill.get("rembalance"))
        out["last_invoice_amount"] = to_float(bill.get("billamount"))
        out["last_invoice_number"] = str(bill.get("invoicenumber") or "") or None
        out["last_invoice_due_date"] = to_date(bill.get("duedate"))
        out["last_invoice_issue_date"] = to_date(
            first(bill, "billdate", "invoicedate", "issuedate", "BillDate")
        )

    # Invoice list from the billing history, when the account has one.
    history_result = hidro_result(raw.get("billing_history"))
    invoices = as_list(history_result.get("objBillingHistoryEntity"))
    if invoices:
        def inv_date(item: dict) -> date:
            return to_date(first(item, "billDate", "BillDate", "invoiceDate", "billdate")) or date.min

        newest = max(invoices, key=inv_date)
        if out["last_invoice_issue_date"] is None and inv_date(newest) != date.min:
            out["last_invoice_issue_date"] = inv_date(newest)

    if out["balance"] is not None:
        unpaid = out["balance"] > 0.009
        out["unpaid_invoices"] = 1 if unpaid else 0
        if out["last_invoice_amount"] is not None:
            out["last_invoice_paid"] = not unpaid
        if unpaid:
            out["next_due_date"] = out["last_invoice_due_date"]
    out["days_until_due"] = days_between(today, out["next_due_date"])

    # ANRE compensations (prosumers), current year, RON.
    payments = as_list(history_result.get("objBillingPaymentHistoryEntity"))
    comp_total = 0.0
    comp_found = False
    for payment in payments:
        if not str(payment.get("channel") or "").startswith(("Comp ANRE", "Comp ", "Compensare")):
            continue
        when, amount = to_date(payment.get("paymentDate")), to_float(payment.get("amount"))
        if when and amount is not None and when.year == today.year:
            comp_total += amount
            comp_found = True
    if comp_found:
        out["compensation_ytd"] = round(comp_total, 2)

    # Reading window.
    window = hidro_payload(raw.get("window"))
    if isinstance(window, dict) and window:
        start = _window_date(window.get("OpeningDate"), today)
        end = _window_date(window.get("ClosingDate"), today)
        is_open = str(window.get("Is_Window_Open", "")).strip() == "1"
        if start and end and end < today and not is_open:
            start = _window_date(window.get("NextMonthOpeningDate"), today) or _next_month(start)
            end = _window_date(window.get("NextMonthClosingDate"), today) or _next_month(end)
            if start < today:
                start, end = _next_month(start), _next_month(end)
        out["reading_window_start"] = start
        out["reading_window_end"] = end
        out["reading_window_open"] = is_open

    # Index history: last index and consumption derived from index deltas
    # (classic meters have no consumption curve).
    readings = hidro_readings(raw.get("history"))
    if readings:
        last_date, last_index, _ = readings[-1]
        out["last_index"] = last_index
        out["last_index_date"] = last_date
        if len(readings) >= 2:
            delta = last_index - readings[-2][1]
            if delta >= 0:
                out["consumption_last_month"] = round(delta, 3)
        before_year = [r for r in readings if r[0].year < today.year]
        if before_year:
            ytd = last_index - before_year[-1][1]
            if ytd >= 0:
                out["consumption_ytd"] = round(ytd, 3)
    injected = hidro_readings(raw.get("history"), REG_INJECTED)
    if injected:
        out["injected_index"] = injected[-1][1]
    return out
