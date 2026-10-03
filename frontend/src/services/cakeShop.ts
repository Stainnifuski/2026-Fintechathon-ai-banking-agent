import { apiClient } from './api';

export interface CakeProduct {
  product_id: string;
  name: string;
  description: string;
  price_fen: number;
  price: number;
  currency: 'CNY';
  image_key: 'rose' | 'suncake' | 'premium' | string;
}

export interface CakeOrder {
  order_id: string;
  customer_user_id: string;
  product_id: string;
  product_name: string;
  product_description: string;
  quantity: number;
  unit_price_fen: number;
  unit_price: number;
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
  status: 'PENDING_PAYMENT' | 'COMPLETED' | 'PAYMENT_CLOSED' | 'REFUNDED';
  state_version: number;
  created_at: string;
  updated_at: string;
}

export const products = () =>
  apiClient.get<CakeProduct[]>('/cake-shop/products').then(response => response.data);

export const orders = () =>
  apiClient.get<CakeOrder[]>('/cake-shop/orders').then(response => response.data);

export const order = (orderId: string) =>
  apiClient.get<CakeOrder>(`/cake-shop/orders/${orderId}`).then(response => response.data);

export const createOrder = (payload: {
  product_id: string;
  quantity: number;
  idempotency_key: string;
}) => apiClient.post<CakeOrder>('/cake-shop/orders', payload).then(response => response.data);

export const merchantOrders = () =>
  apiClient.get<CakeOrder[]>('/cake-shop/merchant/orders').then(response => response.data);

export const refundOrder = (item: CakeOrder) =>
  apiClient.post<CakeOrder>(`/cake-shop/merchant/orders/${item.order_id}/refund`, {
    state_version: item.state_version,
  }).then(response => response.data);
