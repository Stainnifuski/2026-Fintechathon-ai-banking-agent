/** User-facing explanations; server codes remain in network/audit evidence only. */
export function wealthFeedback(raw:string):string {
 const rules:[string[],string][]=[
  [['ASSESSMENT_UNCONFIRMED'],'风险评估已填写，但还没有本人确认。请展开上方“本人风险评估”，点击“本人确认上述评估结果并继续原需求”，再重新发起推荐或规划。'],
  [['ASSESSMENT_PURCHASE_FORBIDDEN'],'当前正式风险评估结果不允许申购。请先查看“本人风险评估”中的结果说明；可以继续查询产品、订单和持仓，不能跳过评估限制购买。'],
  [['ASSESSMENT_LIMIT_REACHED'],'已达到风险评估次数限制，请按评估页面提示的可用时间再尝试；现有查询不受影响。'],
  [['ASSESSMENT_EXPIRED','ASSESSMENT_VERSION_STALE'],'风险评估已过期或资料已变化。请在上方“本人风险评估”中重新填写并确认，再发起推荐或规划。'],
  [['ASSESSMENT_REQUIRED'],'尚未完成有效的本人风险评估，暂时不能推荐或规划。请先展开上方“本人风险评估”→“填写／重新评估”，回答9题并提交，再点击“本人确认上述评估结果并继续原需求”。完成后重新点击推荐或规划。'],
  [['ASSESSMENT_CURRENT_VERSION_UNPROVEN','ASSESSMENT_BINDING_UNPROVEN','ASSESSMENT_IDENTITY_MISMATCH','ASSESSMENT_SOURCE_MISMATCH','ASSESSMENT_READ_TIME_MISMATCH','ASSESSMENT_VERSION_UNKNOWN','FORMAL_ASSESSMENT_UNAVAILABLE'],'暂时无法核实当前账户的正式风险评估。请先点击“刷新银行事实”，再展开“本人风险评估”查看是否已完成并确认；仍无法读取时请联系维护人员，不要反复重填问卷。'],
  [['RISK_EXCEEDS_CAPACITY','SUITABILITY_MISMATCH'],'所选产品风险超过本人评估等级，不能购买。请移除超出等级的产品，选择本人风险等级允许的产品后重新校验。'],
  [['VALUATION_INCOMPLETE','ASSET_SUMMARY_UNAVAILABLE'],'暂时无法取得完整资产估值，不能确认配置方案。请先点击“刷新银行事实”；仍有缺失时等待报价恢复，再重新生成方案。'],
  [['PURCHASE_STOPPED','CURRENT_DISPLAY_QUALIFICATION_UNAVAILABLE'],'产品当前不满足申购条件。请刷新推荐或选择其他合格产品，再重新核对方案。'],
  [['INSUFFICIENT_FUNDS','INSUFFICIENT_CASH'],'账户可支付现金不足。请先查看可用余额和占款，减少申购金额或等待资金实际到账后重新准备预览。'],
  [['AUTHORIZATION_EXPIRED','STALE_PLAN','VERSION_STALE','STALE'],'方案、账户事实或授权已变化。请刷新银行事实，重新核对当前方案并准备新的确认预览；已受理订单不变。'],
  [['WRITE_CONTROL_UNPROVEN','TASK_WRITE_BLOCKED'],'当前任务暂停或尚未取得写入许可。请先检查任务状态并恢复允许操作的任务，再重试；不要重复提交交易。'],
  [['HOLDING_CAP'],'该持仓当前只允许保留或减持，不能增持。请降低该产品目标金额，再重新校验。'],
  [['PRODUCT_','DISCLOSURE_','BANK_'],'当前产品资料、报价或交易规则尚未满足操作条件。请刷新银行事实并重新核对所选产品；仍失败时先选择其他合格产品。'],
  [['PREFERENCE'],'进取偏好不在当前允许范围内。请按页面给出的整数百分比范围调整，再提交；原方案保留。'],
 ];
 for(const [codes,text] of rules)if(codes.some(code=>raw.includes(code)))return text;
 const clean=raw.replace(/\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g,'').replace(/^[\s:：；;,]+|[\s:：；;,]+$/g,'');
 return /[\u4e00-\u9fff]/.test(clean)?clean:'本次操作未完成。请先刷新银行事实后重试；仍失败时请联系维护人员核对原因。';
}
