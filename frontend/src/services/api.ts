import axios from 'axios';
import {publishSecurityDenial} from './securityState';
import type { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse } from 'axios';
import {changesAssetFacts,invalidateAssetSummary} from './assetSummaryEvents';
import {AUTHENTICATION_EXPIRED_EVENT,isSessionAuthenticationFailure} from '../utils/sessionAuthentication';

/**
 * Axios 实例封装 + 请求/响应拦截器
 */
const apiClient: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

/** 请求拦截器：添加 Authorization header */
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem('token');
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error: unknown) => {
    return Promise.reject(error);
  }
);

/** 响应拦截器：统一错误处理 */
apiClient.interceptors.response.use(
  (response: AxiosResponse) => {
    if(changesAssetFacts(response.config.method??'get',response.config.url??''))invalidateAssetSummary();
    return response;
  },
  (error: unknown) => {
    if (axios.isAxiosError(error)) {
      const status = error.response?.status;
      publishSecurityDenial(error);

      switch (status) {
        case 401:
          if (isSessionAuthenticationFailure(error)) {
            localStorage.removeItem('token');
            localStorage.removeItem('bank-user');
            window.dispatchEvent(new Event(AUTHENTICATION_EXPIRED_EVENT));
            console.error('认证失败，请重新登录');
          }
          break;
        case 403:
          console.error('没有权限执行此操作');
          break;
        case 429:
          console.error('请求过于频繁，请稍后再试');
          break;
        case 500:
          console.error('服务器内部错误');
          break;
        default:
          console.error('请求失败:', error.message);
      }
    }

    return Promise.reject(error);
  }
);

export { apiClient };
