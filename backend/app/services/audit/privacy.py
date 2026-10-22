"""Allowlisted facts. Arbitrary prose and credentials never enter the audit store."""
import re
from datetime import date, datetime
from decimal import Decimal

# Every nested key must be approved too. In particular, data/params/command do not
# provide a way to smuggle unrecognised nested fields into an approved container.
SCALARS = set("""
status phase outcome action kind type source source_module mode provider model schema_version
tool tool_name rule_id rule_version policy_version version state_version plan_version
current_plan_version control_epoch permission_level risk_level verification_level
confirmed user_confirmed mfa_method verified required requires_confirmation requires_mfa
amount amount_fen balance_fen available_fen currency units shares nav price fee fee_fen
purchase_fee_fen redemption_fee_fen gross_fen net_fen cost_fen holding_days rate
total count result_count item_count attempts attempt_count attempt_no failed_verifications
error error_code code reason_code waiting_reason control_mode cancelled cancel_requested
is_current enabled active category month year period date start_date end_date date_from date_to
scheduled_at starts_at ends_at all_day time_zone expires_at confirmed_at verified_at
next_charge_at cancelled_at anchor_day frequency billing_cycle warning_percent
from_account to_account account_id debit_account_id merchant_account_id customer_account_id
operation_id task_id plan_id step_id node_id authorization_id session_id conversation_id
order_id payment_request_id batch_id tx_id bank_tx_id debit_tx_id credit_tx_id
card_id subscription_id product_id product_code fund_code lot_id context_id run_id
merchant_id alias_id contact_id tag_id stream_id original_tx_id parent_task_id child_task_id
owner_id actor_id subject_user_id user_id payer_user_id requester_user_id customer_user_id
param_digest digest payload_digest request_id business_request_id event_id evidence_id
name_present note_present title_present content_present result_status decision_type
method route http_status transport_status duration_ms query_count changed_count truncated
operation_kind audit_status fallback_count legacy incomplete parameter_source
rule_count depends_on goal_id goal_type dependency_name field_name validation_type
evidence_ids allowed_tools operation_ids task_ids product_ids tx_ids goal_count
removed_count recovered_count rows_affected verification_attempts transaction_committed
table mutation bank_status response_lost fingerprint matched_count source_message_id
selection_mode requirement_version assessment_id confirmed_version risk_score risk_capacity
redemption_rate purchase_rate quote_id quoted_at business_at as_of data_kind amount_unit
limit offset page page_size query_kind since until export_count provenance
direction product_version disclosure_version questionnaire_version profile_version score_version
command_version redemption_mode nav_quote_id quoted_cash_fen context_epoch scope generation
locked_until revoked_before consecutive window_failures allocation_percent weight_percent percent
execution_mode domains has_dependencies goal_count risk decision status_code backend_mode
reference_id purpose intent risk_tolerance horizon_months monthly_income_fen monthly_expense_fen
available_cash_fen liquidity_buffer_fen target_amount_fen selection_version version_id
accepted reason_codes goal operation_ref compaction_status partial_completed
capacity_level purchase_allowed matched product_risk product_risk_level operation_security_level
budget_fen budget_min_fen budget_max_fen reserve_fen horizon_days need fraction sell_then_buy
suggest_budget additional_investment cash_fen realized_pnl_fen refund_fen rounding_yuan
estimated_nav estimated_nav_date pricing_date confirm_date cash_date purchase_fee_rate
min_amount_fen max_amount_fen max_daily_amount_fen hold_days calendar_version cutoff
confirm_business_days cash_business_days purchase_end_date closed_maturity_date open_interval_days
policy_version fee_rate redemption_fee_rate verified_at capacity_level daily_limit rolling_year_limit
raises_risk single_txn_limit_fen daily_txn_limit_fen credit_limit_fen online_enabled
chat_task_id transfer_task_id focus_task_id
""".split())
CONTAINERS = set("""
before after facts params command result output plan goals steps nodes checks decision_chain
rules evidence objects arguments changes requirements fields allocation allocations products
orders operations tasks totals filters query bounds selection allocations_percent data state slots form goal_plan grounding sources
items estimate suitability disclosure details preview quote lots task operation
""".split())
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/@+{}\-]{1,128}$")
LONG_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){10,18}\d(?!\d)")
PROSE_KEYS = {"name", "note", "title", "content", "message", "description", "reason", "query"}


def clean(value, *, depth=0):
    if depth > 12:
        return {"truncated": True}
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "code" and (not isinstance(item, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,95}", item)):
                # TOTP confirmation and mock SMS inbox both use `code`.
                # Error codes remain available, numerical proofs never do.
                continue
            if key in PROSE_KEYS and isinstance(item, str):
                out[key + "_present"] = bool(item)
            elif key in SCALARS or key in CONTAINERS:
                # Object IDs are typed references, not phone/card fields. A
                # legitimate numeric operation ID must remain correlatable.
                if key in OBJECT_FIELDS and isinstance(item, str) and IDENTIFIER.fullmatch(item):
                    out[key] = item
                else:
                    out[key] = clean(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out = [clean(v, depth=depth + 1) for v in items[:100]]
        if len(items) > 100:
            out.append({"truncated": True, "total": len(items)})
        return out
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        # Identifiers, ISO times and rule codes only. Free text is not a safe
        # fallback, even if a future caller places it in an approved field.
        if not IDENTIFIER.fullmatch(value):
            return "[omitted]"
        return LONG_NUMBER.sub("[redacted]", value)
    return "[omitted]"


OBJECT_FIELDS = {
    "session_id": "session", "conversation_id": "session", "task_id": "task",
    "parent_task_id": "task", "child_task_id": "task", "plan_id": "plan",
    "step_id": "step", "node_id": "node", "operation_id": "operation",
    "authorization_id": "authorization", "payment_request_id": "payment_request",
    "order_id": "order", "batch_id": "batch", "tx_id": "transaction",
    "bank_tx_id": "transaction", "debit_tx_id": "transaction", "credit_tx_id": "transaction",
    "card_id": "card", "subscription_id": "subscription", "product_id": "product",
    "lot_id": "lot", "context_id": "context", "contact_id": "contact",
    "merchant_id": "merchant", "stream_id": "stream", "assessment_id": "assessment",
    "operation_ref": "operation",
    "chat_task_id": "chat_task", "focus_task_id": "chat_task", "transfer_task_id": "task",
}


def references(value, domain, *, depth=0):
    refs = []
    if depth > 12:
        return refs
    if isinstance(value, dict):
        for key, item in value.items():
            if key in OBJECT_FIELDS and isinstance(item, str) and IDENTIFIER.fullmatch(item):
                refs.append(dict(domain=domain, object_type=OBJECT_FIELDS[key], object_id=item,
                                 version=str(value.get("plan_version", value.get("version", "")))[:40] or None))
            elif isinstance(item, (dict, list)) and key in CONTAINERS:
                refs.extend(references(item, domain, depth=depth + 1))
    elif isinstance(value, list):
        for item in value:
            refs.extend(references(item, domain, depth=depth + 1))
    return refs
