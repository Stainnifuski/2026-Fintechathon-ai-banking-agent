import { apiClient } from './api';

export interface FlowerProduct {
  product_id: string;
  name: string;
  description: string;
  price_fen: number;
  price: number;
  currency: 'CNY';
  image_key: 'rose' | 'sunflower' | 'premium' | string;
}

export interface FlowerOrder {
  order_id: string;
  customer_user_id: string;
  product_id: string;
  product_name: string;
  product_description: string;
  quantity: number;
  unit_price_fen: number;
  unit_price: number;
  refunded_fen: number; remaining_refundable_fen: number;
  total_fen: number;
  total: number;
  currency: 'CNY';
  payment_request_id?: string;
  payment_request?: {
    payment_request_id: string;
    requester_name: string;
    payer_name: string;
    amount: number;
    amount_fen: number;
    currency: 'CNY';
    note?: string;
    status: string;
    state_version: number;
  };
  status: 'PENDING_PAYMENT' | 'COMPLETED' | 'PAYMENT_CLOSED' | 'REFUNDED' | 'PARTIALLY_REFUNDED';
  state_version: number;
  created_at: string;
  updated_at: string;
}

export const products = () =>
  apiClient.get<FlowerProduct[]>('/flower-shop/products').then(response => response.data);

export const orders = () =>
  apiClient.get<FlowerOrder[]>('/flower-shop/orders').then(response => response.data);

export const order = (orderId: string) =>
  apiClient.get<FlowerOrder>(`/flower-shop/orders/${orderId}`).then(response => response.data);

export const createOrder = (payload: {
  product_id: string;
  quantity: number;
  idempotency_key: string;
}) => apiClient.post<FlowerOrder>('/flower-shop/orders', payload).then(response => response.data);

export const merchantOrders = () =>
  apiClient.get<FlowerOrder[]>('/flower-shop/merchant/orders').then(response => response.data);

export const refundOrder = (item: FlowerOrder, amount_fen?:number, operation_id?:string) =>
  apiClient.post<FlowerOrder>(`/flower-shop/merchant/orders/${item.order_id}/refund`, {
    state_version: item.state_version, amount_fen, operation_id,
  }).then(response => response.data);
