/** 聊天消息类型枚举 */
export type MessageType = 'text' | 'confirmation' | 'mfa_required' | 'chart' | 'error';

/** 聊天消息 */
export interface ChatMessage {
  messageId: string;
  sessionId: string;
  type: MessageType;
  content: string;
  actionRequired?: ActionRequired;
  timestamp: string;
  isUser: boolean;
}

/** 需要用户执行的操作 */
export interface ActionRequired {
  type: 'confirm' | 'mfa' | 'select';
  data: ConfirmationData | MfaData;
}

/** 确认操作数据（黄色级别） */
export interface ConfirmationData {
  action: string;
  description: string;
  details: TransferDetails;
}

/** 转账详情 */
export interface TransferDetails {
  fromAccount: string;
  toContact: string;
  amount: number;
  note?: string;
}

/** MFA 验证数据（红色级别） */
export interface MfaData {
  method: 'sms' | 'face' | 'ukey';
  phone?: string;
}

/** 发送消息请求 */
export interface SendMessageRequest {
  sessionId?: string;
  userId: string;
  message: string;
  channel?: string;
  deviceId?: string;
}

/** 确认/取消操作请求 */
export interface ConfirmActionRequest {
  sessionId: string;
  messageId: string;
  action: 'confirm' | 'cancel';
  mfaCode?: string;
}

/** 用户信息 */
export interface User {
  userId: string;
  name: string;
  phone: string;
  riskLevel: string;
  status: string;
}

/** 账户信息 */
export interface Account {
  accountId: string;
  accountType: 'savings' | 'checking' | 'credit';
  cardNumber: string;
  balance: number;
  dailyTransferUsed: number;
  dailyTransferLimit: number;
  status: string;
}

/** 交易记录 */
export interface Transaction {
  txId: string;
  accountId: string;
  type: 'transfer_in' | 'transfer_out' | 'purchase' | 'refund' | 'subscription';
  amount: number;
  counterparty: string;
  category: string;
  note?: string;
  timestamp: string;
  status: 'completed' | 'pending' | 'failed' | 'cancelled';
  channel: string;
}

/** 理财产品 */
export interface FinancialProduct {
  productId: string;
  name: string;
  annualRate: number;
  riskLevel: string;
  termDays: number;
  minAmount: number;
  maxAmount: number;
  status: 'on_sale' | 'sold_out' | 'off_shelf';
}

/** 订阅 */
export interface Subscription {
  subId: string;
  serviceName: string;
  merchant: string;
  amount: number;
  cycle: 'monthly' | 'quarterly' | 'yearly';
  nextChargeDate: string;
  accountId: string;
  status: 'active' | 'paused' | 'cancelled';
}

/** API 通用响应 */
export interface ApiResponse<T> {
  code: number;
  message: string;
  data: T;
}
