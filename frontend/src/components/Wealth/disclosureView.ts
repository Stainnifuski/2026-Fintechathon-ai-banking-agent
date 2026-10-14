export const disclosureLabels: Record<string, string> = {
  version: '披露版本', nature: '产品性质', manager: '管理机构（来源见产品身份）', seller: '展示与销售角色',
  risk: '风险说明', fees: '费用', liquidity: '流动性', holding_period_days: '最低持有天数',
  pricing: '计价规则', settlement: '结算与到账', cancellation: '撤单窗口', limitations: '模拟范围',
  underlying_source_kind:'底层资料来源',execution_source_kind:'交易执行来源',source_product_code:'真实具体代码',
  project_policy_version:'项目分级政策版本',issuer_risk_rating:'公开评级原值（独立保留）',public_sources:'公开资料指纹',public_fields:'公开字段原值',
};
export const displayDisclosure = (value: unknown): string => value == null ? '未提供' : Array.isArray(value)
  ? value.map(displayDisclosure).join('；') : typeof value === 'object'
    ? Object.entries(value).map(([key, item]) => `${key}：${displayDisclosure(item)}`).join('；') : String(value);

export const visibleDisclosures=(value:Record<string,unknown>)=>Object.entries(value).filter(([key])=>["nature","manager","seller","risk","fees","liquidity","holding_period_days","pricing","settlement","cancellation","limitations","quick_redemption"].includes(key));
