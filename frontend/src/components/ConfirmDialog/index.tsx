import Modal from '../Modal';
import { type CSSProperties } from 'react';
import type { ChatMessage, ConfirmationData } from '@/types';

interface ConfirmDialogProps {
  message: ChatMessage;
  onConfirm: () => void;
  onCancel: () => void;
}

/** 转账确认弹窗（黄色级别） */
export function ConfirmDialog({ message, onConfirm, onCancel }: ConfirmDialogProps) {
  const confirmData = message.actionRequired?.data as ConfirmationData | undefined;
  const details = confirmData?.details;


  const dialogStyle: CSSProperties = {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: '28px 24px',
    width: 400,
    maxWidth: '90vw',
    boxShadow: '0 8px 30px rgba(0, 0, 0, 0.12)',
  };

  const titleStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 18,
    fontWeight: 600,
    color: '#fa8c16',
    marginBottom: 20,
  };

  const detailRowStyle: CSSProperties = {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '10px 0',
    borderBottom: '1px solid #f0f0f0',
    fontSize: 14,
  };

  const labelStyle: CSSProperties = {
    color: '#888',
  };

  const valueStyle: CSSProperties = {
    color: '#333',
    fontWeight: 500,
  };

  const amountStyle: CSSProperties = {
    color: '#fa8c16',
    fontWeight: 700,
    fontSize: 20,
  };

  const buttonRowStyle: CSSProperties = {
    display: 'flex',
    gap: 12,
    marginTop: 24,
  };

  const cancelBtnStyle: CSSProperties = {
    flex: 1,
    padding: '10px 0',
    borderRadius: 8,
    border: '1px solid #d9d9d9',
    backgroundColor: '#fff',
    color: '#666',
    fontSize: 14,
    cursor: 'pointer',
    fontWeight: 500,
  };

  const confirmBtnStyle: CSSProperties = {
    flex: 1,
    padding: '10px 0',
    borderRadius: 8,
    border: 'none',
    backgroundColor: '#fa8c16',
    color: '#fff',
    fontSize: 14,
    cursor: 'pointer',
    fontWeight: 600,
  };

  return (
    <Modal role="dialog" aria-modal="true">
      <div style={dialogStyle}>
        <div style={titleStyle}>
          <span>⚠️</span>
          <span>转账确认</span>
        </div>

        {confirmData?.description && (
          <p style={{ color: '#666', marginBottom: 16, fontSize: 14 }}>
            {confirmData.description}
          </p>
        )}

        <div>
          <div style={detailRowStyle}>
            <span style={labelStyle}>收款人</span>
            <span style={valueStyle}>{details?.toContact ?? '-'}</span>
          </div>
          <div style={detailRowStyle}>
            <span style={labelStyle}>转账金额</span>
            <span style={amountStyle}>¥{details?.amount?.toFixed(2) ?? '0.00'}</span>
          </div>
          <div style={detailRowStyle}>
            <span style={labelStyle}>付款账户</span>
            <span style={valueStyle}>{details?.fromAccount ?? '-'}</span>
          </div>
          {details?.note && (
            <div style={detailRowStyle}>
              <span style={labelStyle}>备注</span>
              <span style={valueStyle}>{details.note}</span>
            </div>
          )}
        </div>

        <div style={buttonRowStyle}>
          <button style={cancelBtnStyle} onClick={onCancel}>
            取消
          </button>
          <button style={confirmBtnStyle} onClick={onConfirm}>
            确认转账
          </button>
        </div>
      </div>
    </Modal>
  );
}
