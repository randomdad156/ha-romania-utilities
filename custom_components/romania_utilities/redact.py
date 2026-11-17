"""What diagnostics must not reveal (pure, no Home Assistant imports, testable)."""

from __future__ import annotations

from typing import Any

# Credentials, tokens, personal data and every account / contract identifier.
TO_REDACT = {
    # credentials and session
    "password",
    "username",
    "tokens",
    "access_token",
    "refresh_token",
    "accessToken",
    "refreshToken",
    "idToken",
    "token",
    "SessionToken",
    "UserID",
    "key",
    "tokenId",
    # people and places
    "email",
    "Email",
    "phone",
    "Phone",
    "phoneNumber",
    "mobile",
    "name",
    "Name",
    "FullName",
    "firstName",
    "lastName",
    "address",
    "Address",
    "mailingAddress",
    "consumptionPointAddress",
    "street",
    "streetNumber",
    "apartment",
    "cnp",
    "CNP",
    "fiscalNumber",
    "iban",
    "label",
    "titlu",
    "asoc_name",
    # account, contract and meter identifiers
    "id",
    "uan",
    "account_number",
    "id_ap",
    "asoc",
    "parent",
    "partnerCode",
    "accountContract",
    "collectiveContract",
    "collectiveAccountContract",
    "contract",
    "contractName",
    "contractUuid",
    "consumptionPointCode",
    "companyCode",
    "UtilityAccountNumber",
    "utilityAccountNumber",
    "AccountNumber",
    "InstallationNumber",
    "installation",
    "bp_number",
    "BpNumber",
    "cod_client",
    "pod",
    "Pod",
    "podValue",
    "prosumId",
    "cb",
    "invoiceNumber",
    "invoicenumber",
    "invoiceId",
    "last_invoice_number",
    "meterSeries",
    "serialNumber",
    "SerialNumber",
    "ablbelnr",
    # iHidro (SmartCMobile) account, POD and meter identifiers
    "contractAccountID",
    "accountID",
    "POD",
    "CounterSeries",
    "deviceNumber",
    "equipmentNo",
    "registerNo",
    "distCustomer",
    "distCustomerId",
    "distContract",
    "exbel",
    # e-bloc apartment and meter identifiers
    "ap",
    "id_contor",
    "guid",
}


def anonymise_contract_keys(data: Any, contracts: list[dict[str, Any]]) -> Any:
    """Re-key a {contract id: ...} mapping as {"contract_1": ...} in contract order.

    Key-based redaction never touches dict keys, and here the keys are the
    contract / account numbers themselves.
    """
    if not isinstance(data, dict):
        return data
    positions = {str(c.get("id")): i for i, c in enumerate(contracts, start=1)}
    out: dict[str, Any] = {}
    extra = len(positions)
    for key, value in data.items():
        index = positions.get(str(key))
        if index is None:
            extra += 1
            index = extra
        out[f"contract_{index}"] = value
    return out
