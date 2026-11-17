"""Diagnostics redaction: identifiers must not survive as values or as keys."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_PATH = Path(__file__).parents[1] / "custom_components" / "romania_utilities" / "redact.py"
_spec = importlib.util.spec_from_file_location("ru_redact", _PATH)
r = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r)

CONTRACTS = [
    {"id": "002100000004", "kind": "Gas"},
    {"id": "8000000001", "uan": "8000000001", "account_number": "A1", "pod": "RO1"},
    {"id": "11_7", "asoc": "11", "id_ap": "7"},
]


def test_contract_keys_replaced_by_position():
    data = {"8000000001": {"balance": 1}, "002100000004": {"balance": 2}, "11_7": {"balance": 3}}
    out = r.anonymise_contract_keys(data, CONTRACTS)
    assert out == {"contract_2": {"balance": 1}, "contract_1": {"balance": 2}, "contract_3": {"balance": 3}}


def test_unknown_keys_get_new_positions_not_their_value():
    out = r.anonymise_contract_keys({"999": 1, "998": 2}, CONTRACTS)
    assert set(out) == {"contract_4", "contract_5"}


def test_non_dict_passthrough():
    assert r.anonymise_contract_keys(None, CONTRACTS) is None


def test_identifier_fields_are_redacted():
    for key in ("id", "uan", "account_number", "id_ap", "asoc", "cod_client", "bp_number", "pod",
                "accountContract", "UtilityAccountNumber", "AccountNumber", "invoiceNumber",
                "password", "username", "tokens", "fiscalNumber", "phoneNumber"):
        assert key in r.TO_REDACT, key


def _redact(value):
    """Same semantics as homeassistant.components.diagnostics.async_redact_data."""
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, dict):
        return {k: "**REDACTED**" if k in r.TO_REDACT else _redact(v) for k, v in value.items()}
    return value


def test_no_contract_number_left_in_dump():
    raw = {c["id"]: {"contract": {"accountContract": c["id"]}, "rows": [{"id_ap": "7", "asoc": "11"}]}
           for c in CONTRACTS}
    dump = json.dumps({"entry": _redact({"contracts": CONTRACTS}),
                       "raw": _redact(r.anonymise_contract_keys(raw, CONTRACTS))})
    for secret in ("002100000004", "8000000001", "11_7", "RO1", '"A1"'):
        assert secret not in dump, secret
