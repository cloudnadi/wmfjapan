"""
Matching logic for payment reconciliation: compares admin-uploaded payment
transactions (credit card / bank transfer / PayPay CSV exports) against
registrations for the same event, and classifies each side as matched,
unmatched, or an amount mismatch.

Kept as pure functions (no boto3) so the algorithm can be reasoned about
independently of the Lambda I/O in handler.py.
"""

import re
from typing import Any, Optional


def clean_amount(raw: Any) -> Optional[int]:
    """Parse a CSV amount cell into whole yen. Returns None if unparsable."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = re.sub(r"[^\d.\-]", "", text)
    if not text or text in {"-", "."}:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if negative:
        value = -value
    return int(round(value))


def normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def registrant_name(reg: dict) -> str:
    full = str(reg.get("name", "")).strip()
    if not full:
        full = f"{reg.get('firstName', '')} {reg.get('lastName', '')}".strip()
    return full


# Common header spellings seen in credit card, bank, and PayPay CSV exports
# (English and Japanese). Used only to prefill the admin's mapping choice —
# the admin always reviews and can override before importing.
COLUMN_ALIASES = {
    "date": ["date", "transaction date", "txn date", "posted date", "payment date", "利用日", "日付", "取引日"],
    "amount": ["amount", "amount (jpy)", "amount(jpy)", "debit", "value", "price", "金額", "利用金額", "お支払い金額"],
    "description": ["description", "memo", "details", "narrative", "remarks", "notes", "摘要", "備考"],
    "payerName": ["payer", "payer name", "name", "customer", "customer name", "cardholder", "氏名", "お名前"],
    "payerEmail": ["email", "payer email", "customer email", "buyer email", "e-mail", "メール", "メールアドレス"],
    "reference": ["reference", "reference id", "ref", "reference no", "transaction id", "order id", "id"],
}


def suggest_column_mapping(headers: list) -> dict:
    """Best-effort guess of which uploaded CSV header maps to each of our
    normalized fields, by case-insensitive alias matching.

    Runs in two passes so an exact alias match on one field always wins over
    a looser substring match on another (e.g. a "Payer Email" header should
    land on payerEmail, not get stolen by payerName's "payer" substring
    check just because that field is considered first) — and each header is
    claimed by at most one field.
    """
    normalized_headers = [(h, normalize_text(h)) for h in headers]
    used_headers: set = set()
    mapping: dict = {}

    # Pass 1: exact alias matches, across all fields, take priority.
    for field, aliases in COLUMN_ALIASES.items():
        normalized_aliases = [normalize_text(a) for a in aliases]
        for header, norm in normalized_headers:
            if header in used_headers:
                continue
            if norm in normalized_aliases:
                mapping[field] = header
                used_headers.add(header)
                break

    # Pass 2: substring matches fill in whatever fields are still unmapped.
    for field, aliases in COLUMN_ALIASES.items():
        if field in mapping:
            continue
        normalized_aliases = [normalize_text(a) for a in aliases if a]
        for header, norm in normalized_headers:
            if header in used_headers:
                continue
            if any(alias in norm for alias in normalized_aliases):
                mapping[field] = header
                used_headers.add(header)
                break

    return mapping


def normalize_transaction_row(row: dict, column_mapping: dict) -> Optional[dict]:
    """Apply column_mapping (targetField -> csv header) to a parsed CSV row.
    Returns a normalized dict, or None if the row has no usable amount."""

    def get(field):
        header = column_mapping.get(field, "")
        return row.get(header, "") if header else ""

    amount = clean_amount(get("amount"))
    if amount is None:
        return None

    return {
        "transactionDate": str(get("date") or "").strip(),
        "amount": amount,
        "description": str(get("description") or "").strip(),
        "payerName": str(get("payerName") or "").strip(),
        "payerEmail": str(get("payerEmail") or "").strip().lower(),
        "referenceId": str(get("reference") or "").strip(),
    }


def match_transactions(registrations: list, transactions: list) -> dict:
    """Core reconciliation algorithm.

    registrations: normalized registration dicts with at least registrationId,
        totalAmount, paidAmount, paymentStatus, email, name/firstName/lastName.
    transactions: normalized transaction dicts with at least transactionId,
        amount, description, payerName, referenceId, sourceType.

    Matching runs in two passes so higher-confidence signals (the
    registration id or email appearing in the transaction's free-text field,
    or a name+amount pair) claim a registration before weaker signals
    (amount-only) get a chance — an ambiguous amount collision is left
    unmatched rather than guessed.
    """
    reg_by_id = {r["registrationId"]: r for r in registrations}
    remaining_reg_ids = set(reg_by_id)

    reg_email: dict = {}
    reg_name: dict = {}
    for r in registrations:
        rid = r["registrationId"]
        email = normalize_text(r.get("email", ""))
        if email:
            reg_email.setdefault(email, []).append(rid)
        name = normalize_text(registrant_name(r))
        if name:
            reg_name.setdefault(name, []).append(rid)

    matches: dict = {}  # registrationId -> [transaction, ...]
    match_confidence: dict = {}
    unmatched_transactions: list = []

    CONFIDENCE_RANK = {"reference": 3, "email": 3, "name+amount": 2, "name-only": 1, "amount-only": 1}

    def claim(rid, txn, confidence):
        matches.setdefault(rid, []).append(txn)
        if rid not in match_confidence or CONFIDENCE_RANK.get(confidence, 0) > CONFIDENCE_RANK.get(match_confidence[rid], 0):
            match_confidence[rid] = confidence

    strong: list = []
    weak: list = []

    for txn in transactions:
        haystack_norm = normalize_text(
            " ".join([txn.get("description", ""), txn.get("referenceId", ""), txn.get("payerEmail", "")])
        )

        # 1) The registration id itself appears in the free-text field.
        found_rid = next(
            (rid for rid in remaining_reg_ids if rid and normalize_text(rid) in haystack_norm),
            None,
        )
        if found_rid:
            strong.append((txn, found_rid, "reference"))
            continue

        # 2) The registrant's email — either a dedicated payer-email column or
        # mentioned in the free-text description/reference — matches.
        found_rid = None
        for email, rids in reg_email.items():
            if email and email in haystack_norm:
                candidates = [rid for rid in rids if rid in remaining_reg_ids]
                if len(candidates) == 1:
                    found_rid = candidates[0]
                    break
        if found_rid:
            strong.append((txn, found_rid, "email"))
            continue

        # 3) The payer name on the transaction matches a registrant's name.
        payer_norm = normalize_text(txn.get("payerName", ""))
        name_hit = None
        if payer_norm:
            for name, rids in reg_name.items():
                if name and (name in payer_norm or payer_norm in name):
                    candidates = [rid for rid in rids if rid in remaining_reg_ids]
                    if len(candidates) == 1:
                        name_hit = candidates[0]
                        break
        if name_hit:
            reg_amount = int(reg_by_id[name_hit].get("totalAmount", 0) or 0)
            if reg_amount == txn["amount"]:
                strong.append((txn, name_hit, "name+amount"))
            else:
                weak.append((txn, name_hit, "name-only"))
            continue

        weak.append((txn, None, "amount-only"))

    for txn, rid, confidence in strong:
        if rid in remaining_reg_ids:
            claim(rid, txn, confidence)
            remaining_reg_ids.discard(rid)
        else:
            unmatched_transactions.append(txn)

    # Weak pass: a name-only hit is honored even without an amount match (the
    # discrepancy is surfaced via matchedAmount vs registrationAmount);
    # amount-only is honored only when exactly one unmatched registration
    # shares that exact total.
    amount_buckets: dict = {}
    for rid in remaining_reg_ids:
        amt = int(reg_by_id[rid].get("totalAmount", 0) or 0)
        amount_buckets.setdefault(amt, []).append(rid)

    for txn, rid, confidence in weak:
        if rid and rid in remaining_reg_ids:
            claim(rid, txn, confidence)
            remaining_reg_ids.discard(rid)
            bucket = amount_buckets.get(int(reg_by_id[rid].get("totalAmount", 0) or 0), [])
            if rid in bucket:
                bucket.remove(rid)
            continue

        candidates = [c for c in amount_buckets.get(txn["amount"], []) if c in remaining_reg_ids]
        if len(candidates) == 1:
            claim(candidates[0], txn, "amount-only")
            remaining_reg_ids.discard(candidates[0])
        else:
            unmatched_transactions.append(txn)

    matched_pairs: list = []
    amount_mismatches: list = []
    for rid, txns in matches.items():
        reg = reg_by_id[rid]
        matched_amount = sum(t["amount"] for t in txns)
        reg_amount = int(reg.get("totalAmount", 0) or 0)
        difference = matched_amount - reg_amount
        pair = {
            "registrationId": rid,
            "name": registrant_name(reg),
            "email": reg.get("email", ""),
            "eventType": reg.get("eventType", ""),
            "registrationAmount": reg_amount,
            "matchedAmount": matched_amount,
            "difference": difference,
            "matchConfidence": match_confidence.get(rid, "amount-only"),
            "paymentStatus": reg.get("paymentStatus", ""),
            "transactionIds": [t["transactionId"] for t in txns],
            "sourceTypes": sorted({t.get("sourceType", "") for t in txns}),
        }
        matched_pairs.append(pair)
        if difference != 0:
            amount_mismatches.append(pair)

    unmatched_registrations: list = []
    for rid in remaining_reg_ids:
        reg = reg_by_id[rid]
        payment_status = str(reg.get("paymentStatus", "")).lower()
        paid_amount = int(reg.get("paidAmount", 0) or 0)
        if payment_status == "paid" or paid_amount > 0:
            unmatched_registrations.append({
                "registrationId": rid,
                "name": registrant_name(reg),
                "email": reg.get("email", ""),
                "eventType": reg.get("eventType", ""),
                "totalAmount": int(reg.get("totalAmount", 0) or 0),
                "paidAmount": paid_amount,
                "paymentMethod": reg.get("paymentMethod", ""),
                "paymentStatus": reg.get("paymentStatus", ""),
            })

    total_transaction_amount = sum(t["amount"] for t in transactions)
    total_matched_amount = sum(p["matchedAmount"] for p in matched_pairs)

    summary = {
        "totalRegistrations": len(registrations),
        "totalPaidRegistrations": sum(1 for r in registrations if str(r.get("paymentStatus", "")).lower() == "paid"),
        "totalTransactionsUploaded": len(transactions),
        "totalTransactionAmount": total_transaction_amount,
        "totalMatched": len(matched_pairs),
        "totalMatchedAmount": total_matched_amount,
        "totalUnmatchedRegistrations": len(unmatched_registrations),
        "totalUnmatchedTransactions": len(unmatched_transactions),
        "totalAmountMismatches": len(amount_mismatches),
        "netDiscrepancyAmount": sum(p["difference"] for p in amount_mismatches),
    }

    matched_pairs.sort(key=lambda p: p["name"].lower())
    unmatched_registrations.sort(key=lambda p: p["name"].lower())
    unmatched_transactions.sort(key=lambda t: t.get("transactionDate", ""))

    return {
        "summary": summary,
        "matchedPairs": matched_pairs,
        "unmatchedRegistrations": unmatched_registrations,
        "unmatchedTransactions": unmatched_transactions,
        "amountMismatches": amount_mismatches,
    }
