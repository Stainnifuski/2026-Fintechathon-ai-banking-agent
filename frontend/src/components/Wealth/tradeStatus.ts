/** Backend facts remain available for idempotence and legacy in-flight records. */
export const statusLabels:Record<string,string>={ACCEPTED:'已受理，待结算',PENDING_PRICING:'等待计价',PENDING_CASH:'等待赎回到账',SETTLED:'已成交',CANCELLED:'已撤单',REJECTED:'已拒绝',UNKNOWN:'结果未知，请保留原操作号'};
