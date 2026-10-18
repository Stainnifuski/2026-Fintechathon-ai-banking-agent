/** Backend facts remain available for idempotence and legacy in-flight records. */
export const statusLabels:Record<string,string>={ACCEPTED:'历史状态需核对',PENDING_PRICING:'历史状态需核对',PENDING_CASH:'历史应收需核对',SETTLED:'已成交',CANCELLED:'已取消',REJECTED:'已拒绝',UNKNOWN:'结果未知，请保留原操作号'};
