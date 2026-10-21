import {authenticationExpiredMessage,isSessionAuthenticationFailure} from './sessionAuthentication.ts';

/** Preserve the actual request failure instead of replacing it with a ratio error. */
export function wealthRequestFailure(error:unknown):string {
 if(isSessionAuthenticationFailure(error))return authenticationExpiredMessage;
 const detail=(error as {response?:{data?:{detail?:string|{message?:string;code?:string}}}})?.response?.data?.detail;
 if(typeof detail==='string')return wealthFeedback(detail);
 if(detail?.message&&detail.code&&['INSUFFICIENT_AVAILABLE_FUNDS','INVALID_AMOUNT','NAV_UNAVAILABLE',
  'DATA_STALE','PURCHASE_STOPPED','HOLDING_CAP'].includes(detail.code)&&!/请|后重试/.test(detail.message))
  return `${wealthFeedback(detail.message)} ${wealthFeedback(detail.code)}`;
 if(detail?.message||detail?.code)return wealthFeedback(detail.message??detail.code!);
 return wealthFeedback(error instanceof Error?error.message:'本次请求未完成，请稍后手动重试。');
}

/** User-facing explanations; server codes remain in network/audit evidence only. */
export function wealthFeedback(raw:string):string {
 const rules:[string[],string][]=[
  [['PRODUCT_SET_INFEASIBLE'],'所选产品尚未覆盖模型需要的配置板块，请补选相应活钱、稳健或进取产品后继续核对。'],
  [['PLANNING_AVAILABLE_CASH_CHANGED'],'可用现金已经变化，请重新读取账户资料，再填写保留现金或本次投资金额。尚未下单或占款。'],
  [['PLANNING_CASH_OUT_OF_RANGE'],'保留现金需在0至当前可用现金之间，请检查金额，最多输入两位小数。'],
  [['PLANNING_SELECT_CASH_PRODUCT'],'保留现金低于模型活钱目标，请在活钱候选中选择合格产品补足，或提高保留现金后重新计算。'],
  [['PLANNING_CASH_POLICY_INFEASIBLE','PLANNING_NONCASH_UNAVAILABLE'],'当前产品与风险规则无法满足这笔现金安排，请调整所选产品或增加保留现金后重新计算。'],
  [['PLANNING_MODEL_RATIO_FIXED'],'稳健和进取比例由模型推荐，请返回上方修改现金金额或所选产品后重新计算。'],
  [['BANK_UNAVAILABLE','BANK_TIMEOUT','database is locked'],'银行资料或最新报价读取暂未完成，尚未生成交易预览。请稍后再次点击“核对差额与准备预览”；原方案和已受理交易保留。'],
  [['ASSESSMENT_UNCONFIRMED'],'风险评估已填写，但还没有本人确认。请展开上方“本人风险评估”，点击“本人确认上述评估结果并继续原需求”，再重新发起推荐或规划。'],
  [['ASSESSMENT_PURCHASE_FORBIDDEN'],'当前正式风险评估结果不允许申购。请先查看“本人风险评估”中的结果说明；可以继续查询产品、订单和持仓，不能跳过评估限制购买。'],
  [['ASSESSMENT_LIMIT_REACHED'],'已达到风险评估次数限制，请按评估页面提示的可用时间再尝试；现有查询不受影响。'],
  [['ASSESSMENT_EXPIRED','ASSESSMENT_VERSION_STALE'],'风险评估已过期或资料已变化。请在上方“本人风险评估”中重新填写并确认，再发起推荐或规划。'],
  [['ASSESSMENT_REQUIRED'],'尚未完成有效的本人风险评估，暂时不能推荐或规划。请先展开上方“本人风险评估”→“填写／重新评估”，回答9题并提交，再点击“本人确认上述评估结果并继续原需求”。完成后重新点击推荐或规划。'],
  [['ASSESSMENT_CURRENT_VERSION_UNPROVEN','ASSESSMENT_BINDING_UNPROVEN','ASSESSMENT_IDENTITY_MISMATCH','ASSESSMENT_SOURCE_MISMATCH','ASSESSMENT_READ_TIME_MISMATCH','ASSESSMENT_VERSION_UNKNOWN','FORMAL_ASSESSMENT_UNAVAILABLE'],'暂时无法核实当前账户的正式风险评估。请先点击“刷新银行事实”，再展开“本人风险评估”查看是否已完成并确认；仍无法读取时请联系维护人员，不要反复重填问卷。'],
  [['RISK_EXCEEDS_CAPACITY','SUITABILITY_MISMATCH'],'所选产品风险超过本人评估等级，不能购买。请移除超出等级的产品，选择本人风险等级允许的产品后重新校验。'],
  [['VALUATION_INCOMPLETE','ASSET_SUMMARY_UNAVAILABLE'],'暂时无法取得完整资产估值，不能确认配置方案。请先点击“刷新银行事实”；仍有缺失时等待报价恢复，再重新生成方案。'],
  [['PURCHASE_STOPPED','CURRENT_DISPLAY_QUALIFICATION_UNAVAILABLE'],'产品当前不满足申购条件。请刷新推荐或选择其他合格产品，再重新核对方案。'],
  [['INSUFFICIENT_AVAILABLE_FUNDS','INSUFFICIENT_FUNDS','INSUFFICIENT_CASH'],'借记账户可用资金不足。请减少申购金额，或向绑定借记账户补足资金后重试。'],
  [['QUOTE_CHANGED','PREVIEW_REQUIRED'],'交易报价已变化，系统需重新核对当前交易明细；请查看更新后的金额、净值日期和费用后确认。'],
  [['NAV_UNAVAILABLE','DATA_STALE'],'暂时缺少可用净值，当前无法申购。请稍后重试，或移除该产品并选择其他产品。'],
  [['NAV_UPDATE_REQUIRED'],'当前净值未更新到本次交易所需日期，暂时不能成交。请稍后重试或选择净值已更新的产品；已有持仓和资金保留。'],
  [['INVALID_AMOUNT'],'金额不符合该产品的申购限额。请查看产品详情中的最低及最高金额并修改后重试。'],
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
