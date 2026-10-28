"""Explicit audit inventory. New user-facing routes/tables require classification."""

TABLE_DOMAINS = {}
for domain, tables in {
    "identity": "users security_states sms_challenges",
    "accounts": "accounts transactions scheduled_cash_holds scheduled_cash_hold_links",
    "transfer": "bank_operations transfer_tasks transfer_plans transfer_steps transfer_operations transfer_authorizations risk_reservations transfer_outbox transfer_state_events human_tickets transfer_conversations transfer_recipient_history_cutoffs",
    "payments": "payment_requests payment_request_batches payment_request_payments merchant_refunds merchant_refund_parts",
    "cards": "cards card_authorizations card_operations",
    "subscriptions": "subscriptions",
    "contacts": "contacts",
    "calendar": "calendar_events",
    "shops": "flower_orders cake_orders",
    "bills": "bill_merchants bill_merchant_aliases bill_transfer_pairs bill_recurring_streams bill_recurring_occurrences bill_transaction_metadata bill_user_tags bill_transaction_tags bill_metadata_events bill_classifications bill_category_rules bill_budgets bill_anomaly_reviews bill_recurring_schedules bill_forecast_snapshots bill_sync_states",
    "assistant": "nova_conversations nova_agent_runtime nova_task_sessions nova_task_messages nova_bill_actions nova_bill_results nova_task_graphs nova_execution_queues nova_reference_contexts nova_task_flows scheduled_custom_tasks scheduled_task_funding scheduled_transfer_funding",
    "wealth": "wealth_profiles wealth_assessments wealth_authorizations wealth_verifications wealth_plans wealth_conversations wealth_contexts wealth_cash_holds wealth_unit_holds wealth_orders wealth_lots wealth_settlements wealth_goals wealth_executions wealth_tasks wealth_task_nodes wealth_task_controls wealth_demo_receipts wealth_ledger_reviews wealth_context_selections wealth_quick_quotas wealth_clocks",
}.items():
    TABLE_DOMAINS.update({name: domain for name in tables.split()})

EXCLUDED_TABLES = {
    "audit_events": "audit sink", "audit_event_objects": "audit sink", "audit_logs": "legacy fact adapter",
    "user_phone_registry": "private phone index; users changes are audited",
    "nova_conversation_messages": "encrypted text; conversation lifecycle and AI stages audited",
    "nova_task_message_receipts": "idempotency cache", "transfer_chat_receipts": "idempotency cache",
    "wealth_chat_requests": "idempotency cache", "risk_subject_locks": "lock only",
    "bill_foundation_locks": "lock only",
    **{name: "shared reference/catalog cache; maintenance operation has a service span" for name in """
        wealth_datasets wealth_fund_cache wealth_fund_directory wealth_knowledge wealth_macro
        wealth_navs wealth_product_versions wealth_products wealth_research_products
        wealth_schema_versions wealth_sync_jobs
    """.split()},
}

ROUTE_DOMAINS = {
    "accounts": "accounts", "transactions": "accounts", "auth": "identity",
    "users": "contacts", "tasks": "transfer", "operations": "transfer",
    "chat": "assistant", "bills": "bills", "cards": "cards",
    "subscriptions": "subscriptions", "nova": "assistant", "conversations": "assistant", "scheduled-tasks": "assistant",
    "calendar": "calendar", "wealth": "wealth", "payment-requests": "payments",
    "flower-shop": "shops", "cake-shop": "shops", "audit": "audit",
    "system": "system", "bank": "assistant",
}


def route_domain(path):
    parts = path.strip("/").split("/")
    if parts[:2] != ["api", "v1"]:
        return None
    if parts[2:4] == ["internal", "bank"]:
        return "wealth" if parts[4:5] == ["wealth"] else "bank"
    return ROUTE_DOMAINS.get(parts[2] if len(parts) > 2 else "")


def action_domain(action):
    for prefixes, domain in (
        (("investment_", "wealth_"), "wealth"), (("card_",), "cards"),
        (("subscription_",), "subscriptions"), (("security_",), "identity"),
        (("bank_transfer", "transfer_"), "transfer"),
        (("payment_request_", "merchant_refund"), "payments"),
    ):
        if action.startswith(prefixes):
            return domain
    return "bank"


OBJECT_TYPES = {
    "transfer_tasks": "task", "wealth_tasks": "task", "scheduled_custom_tasks": "task", "scheduled_cash_holds": "hold", "transfer_plans": "plan",
    "wealth_plans": "plan", "wealth_task_nodes": "node", "transfer_steps": "step",
    "bank_operations": "operation", "transfer_operations": "operation",
    "wealth_orders": "operation", "cards": "card", "card_operations": "operation",
    "calendar_events": "calendar_event", "contacts": "contact",
    "nova_conversations": "session", "nova_task_sessions": "session",
    "transfer_conversations": "session", "wealth_conversations": "session",
    "payment_requests": "payment_request", "subscriptions": "subscription",
    "transactions": "transaction", "flower_orders": "order", "cake_orders": "order",
}
