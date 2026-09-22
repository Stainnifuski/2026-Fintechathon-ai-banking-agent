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
export interface ConfirmationCard {
  card_id: string; plan_version: number; from_account: string; to_account: string;
  recipient_name: string; amount: number; currency: string; note?: string; risk_level: string;
}
export interface OperationView {
  operation_id: string; status: string; amount: number; amount_fen: number; currency: string;
  from_account: string; to_account: string; bank_status?: string; bank_tx_id?: string; failure_code?: string;
}
export interface TaskSnapshot {
  task_id: string; phase: string; outcome: string; state_version: number; plan_version: number;
  control_mode: string; control_epoch: number; cancel_requested: boolean; allowed_actions: string[];
  message: string; operation: OperationView; authorization: { authorization_id: string; status: string; risk_level: string };
  risk_reservation?: { reservation_id: string; status: string; amount_fen: number };
  confirmation_card?: ConfirmationCard;
}

export const login = (phone: string, password: string) => apiClient.post<AuthResponse>('/auth/login', { phone, password }).then(r => r.data);
export const register = (name: string, phone: string, password: string) => apiClient.post<AuthResponse>('/auth/register', { name, phone, password }).then(r => r.data);
export const accounts = (userId: string) => apiClient.get<Account[]>(`/accounts/${userId}/accounts`).then(r => r.data);
export const recipients = () => apiClient.get<Recipient[]>('/users/recipients').then(r => r.data);
export const history = () => apiClient.get<Transaction[]>('/transactions/history').then(r => r.data);
export const paymentRequests = () => apiClient.get<PaymentRequest[]>('/payment-requests').then(r => r.data);
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
  request_id: string; operation_id: string; from_account: string; to_account: string; amount: number; note?: string;
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
export const verifyTask = (task: TaskSnapshot, requestId: string) =>
  apiClient.post<TaskSnapshot>(`/tasks/${task.task_id}/verify`, {
    request_id: requestId, state_version: task.state_version,
    challenge_id: 'placeholder', proof: 'placeholder',
  }).then(r => r.data);
