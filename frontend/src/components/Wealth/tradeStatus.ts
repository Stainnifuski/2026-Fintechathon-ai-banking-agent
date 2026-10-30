/** Backend facts remain available for idempotence and legacy in-flight records. */
export const statusLabels:Record<string,string>={ACCEPTED:'历史状态需核对',PENDING_PRICING:'历史状态需核对',PENDING_CASH:'历史应收需核对',SETTLED:'已成交',CANCELLED:'已取消',REJECTED:'已拒绝',UNKNOWN:'结果未知，请保留原操作号'};
Object.assign(statusLabels,{
 AWAITING_CONFIRMATION:'方案已准备，待本人确认',AWAITING_MFA:'本人已确认，待验证码验证',
 VALID:'验证已通过，待提交',EXPIRED:'授权已过期，需重新核对',
 EXECUTING:'已进入执行流程，结果待核对',FAILED:'处理失败，请核对原操作记录',
});
export const tradeStatusLabel=(status:string)=>statusLabels[status]??'状态待核对，请查看原操作记录';
