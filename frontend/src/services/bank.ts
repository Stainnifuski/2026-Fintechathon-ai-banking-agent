import { apiClient } from './api';

export interface User { user_id: string; name: string; phone: string; }
export interface AuthResponse { access_token: string; token_type: string; user: User; }
export interface Account { account_id: string; account_type: string; card_number: string; balance: number; daily_transfer_used: number; daily_transfer_limit: number; }
export interface Recipient { user_id: string; name: string; phone: string; account_id: string; bank: string; }
export interface Transaction { tx_id: string; type: string; amount: number; counterparty: string; note?: string; timestamp: string; }
export interface PaymentRequest {
  payment_request_id: string; requester_user_id: string; requester_name: string;
  payer_user_id: string; payer_name: string; receive_account_id: string;
  amount: number; amount_fen: number; currency: string; note?: string;
  status: 'PENDING' | 'ACCEPTED' | 'PAID' | 'REJECTED' | 'CANCELLED' | 'EXPIRED';
  state_version: number; expires_at: string; created_at: string; responded_at?: string;
}
export interface Subscription {
  subscription_id: string; debit_account_id: string; debit_account_label: string;
  merchant_name: string; amount: number; amount_fen: number; currency: string;
  billing_cycle: 'MONTHLY'; next_charge_at: string;
  status: 'ACTIVE' | 'CANCELLED'; state_version: number;
  source_transaction_id?: string; cancelled_at?: string;
}
export interface SubscriptionChargeResult {
  subscription: Subscription; transaction_id: string; charged_amount: number;
  duplicate: boolean; message: string;
}
export interface ConfirmationCard {
  card_id: string; plan_version: number; from_account: string; to_account: string;
  recipient_name: string; recipient_phone_masked?: string; amount: number; currency: string;
  note?: string | null; note_source?: 'manual' | 'automatic' | 'cleared'; risk_level: string;
  scheduled_at?: string;
}
export interface OperationView {
  operation_id: string; status: string; amount: number; amount_fen: number; currency: string;
  from_account: string; to_account: string; bank_status?: string; bank_tx_id?: string; failure_code?: string;
  note?: string | null;
}
export interface TaskSnapshot {
  task_id: string; phase: string; outcome: string; state_version: number; plan_version: number;
  control_mode: string; control_epoch: number; cancel_requested: boolean; allowed_actions: string[];
  message: string; scheduled_at?: string; operation: OperationView; authorization: { authorization_id: string; status: string; risk_level: string };
  risk_reservation?: { reservation_id: string; status: string; amount_fen: number };
  confirmation_card?: ConfirmationCard;
}

export const login = (phone: string, password: string) => apiClient.post<AuthResponse>('/auth/login', { phone, password }).then(r => r.data);
export const register = (name: string, phone: string, password: string) => apiClient.post<AuthResponse>('/auth/register', { name, phone, password }).then(r => r.data);
export interface TotpStatus { enabled: boolean; }
export interface TotpEnroll { secret: string; otpauth_uri: string; qr_data_uri: string; }
export const totpStatus = () => apiClient.get<TotpStatus>('/auth/totp/status').then(r => r.data);
export const totpEnroll = () => apiClient.post<TotpEnroll>('/auth/totp/enroll').then(r => r.data);
export const totpConfirm = (code: string) => apiClient.post<TotpStatus>('/auth/totp/confirm', { code }).then(r => r.data);
export const accounts = (userId: string) => apiClient.get<Account[]>(`/accounts/${userId}/accounts`).then(r => r.data);
export const recipients = () => apiClient.get<Recipient[]>('/users/recipients').then(r => r.data);
export interface PhoneRecipientCandidate { account_id: string; label: string; }
export const lookupPhoneRecipients = (phone: string) =>
  apiClient.post<PhoneRecipientCandidate[]>('/users/recipients/phone', { phone }).then(r => r.data);
export const history = () => apiClient.get<Transaction[]>('/transactions/history').then(r => r.data);
export const paymentRequests = () => apiClient.get<PaymentRequest[]>('/payment-requests').then(r => r.data);
export const listSubscriptions = () => apiClient.get<Subscription[]>('/subscriptions').then(r => r.data);
export const cancelSubscription = (subscription: Subscription) =>
  apiClient.post<Subscription>(`/subscriptions/${subscription.subscription_id}/cancel`, {
    state_version: subscription.state_version,
  }).then(r => r.data);
export const simulateSubscriptionCharge = (subscription: Subscription, idempotencyKey: string) =>
  apiClient.post<SubscriptionChargeResult>(`/subscriptions/${subscription.subscription_id}/simulate-charge`, {
    state_version: subscription.state_version,
    idempotency_key: idempotencyKey,
  }).then(r => r.data);
export const createPaymentRequest = (payload: {
  payer_user_id: string; receive_account_id: string; amount: number; note?: string; idempotency_key: string;
}) => apiClient.post<PaymentRequest>('/payment-requests', payload).then(r => r.data);
export const createPaymentRequestBatch = (payload: {
  payer_user_ids: string[]; receive_account_id: string; amount: number;
  split_equally: boolean; note?: string; idempotency_key: string;
}) => apiClient.post<PaymentRequest[]>('/payment-requests/batch', payload).then(r => r.data);
export const acceptPaymentRequest = (request: PaymentRequest, payerAccountId: string) =>
  apiClient.post<TaskSnapshot>(`/payment-requests/${request.payment_request_id}/accept`, {
    state_version: request.state_version, payer_account_id: payerAccountId,
  }).then(r => r.data);
export const rejectPaymentRequest = (request: PaymentRequest) =>
  apiClient.post<PaymentRequest>(`/payment-requests/${request.payment_request_id}/reject`, { state_version: request.state_version }).then(r => r.data);
export const cancelPaymentRequest = (request: PaymentRequest) =>
  apiClient.post<PaymentRequest>(`/payment-requests/${request.payment_request_id}/cancel`, { state_version: request.state_version }).then(r => r.data);
export const createTransferTask = (payload: {
  request_id: string; operation_id: string; from_account: string; to_account: string;
  amount: number; note?: string | null; note_source?: 'manual' | 'automatic' | 'cleared';
  recipient_phone?: string; scheduled_at?: string;
}) => apiClient.post<TaskSnapshot>('/tasks/transfers', payload).then(r => r.data);
export interface ChatReply {
  session_id: string; session_version: number; kind: string; message: string; mode: string;
  candidates: { candidate_id: string; kind: string; label: string }[]; task?: TaskSnapshot;
}
export interface ChatRequest {
  request_id: string; operation_id: string; message: string; session_id?: string;
  session_version?: number; candidate_id?: string; task_id?: string; state_version?: number;
}
export const createChatTask = (payload: ChatRequest) =>
  apiClient.post<ChatReply>('/tasks/messages', payload).then(r => r.data);
export const getConversation = (id: string) =>
  apiClient.get<ChatReply>(`/tasks/conversations/${id}`).then(r => r.data);
export const getTask = (taskId: string) => apiClient.get<TaskSnapshot>(`/tasks/${taskId}`).then(r => r.data);
export const confirmTask = (task: TaskSnapshot, requestId: string) =>
  apiClient.post<TaskSnapshot>(`/tasks/${task.task_id}/confirm`, {
    request_id: requestId, state_version: task.state_version, plan_version: task.plan_version,
  }).then(r => r.data);
export const acceptTask = (task: TaskSnapshot, requestId: string) =>
  apiClient.post<TaskSnapshot>(`/tasks/${task.task_id}/accept`, {
    request_id: requestId, state_version: task.state_version,
  }).then(r => r.data);
export const cancelTask = (task: TaskSnapshot, requestId: string) =>
  apiClient.post<TaskSnapshot>(`/tasks/${task.task_id}/cancel`, {
    request_id: requestId, state_version: task.state_version,
  }).then(r => r.data);
export const reconcileTask = (task: TaskSnapshot, requestId: string) =>
  apiClient.post<TaskSnapshot>(`/tasks/${task.task_id}/reconcile`, {
    request_id: requestId, state_version: task.state_version,
  }).then(r => r.data);
export const verifyTask = (task: TaskSnapshot, requestId: string, code: string) =>
  apiClient.post<TaskSnapshot>(`/tasks/${task.task_id}/verify`, {
    request_id: requestId, state_version: task.state_version,
    challenge_id: 'totp', proof: code,
  }).then(r => r.data);



export const modifyTask = (task: TaskSnapshot, payload: {
  operation_id: string; from_account: string; to_account: string; amount: number;
  note: string | null; note_source: 'manual' | 'cleared';
}) => apiClient.post<TaskSnapshot>(`/tasks/${task.task_id}/modify`, {
  request_id: crypto.randomUUID(), state_version: task.state_version, ...payload,
}).then(r => r.data);
