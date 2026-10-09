export const disclosureLabels: Record<string, string> = {
  version: '披露版本', nature: '产品性质', manager: '模拟管理机构', seller: '展示与销售角色',
  risk: '风险说明', fees: '费用', liquidity: '流动性', holding_period_days: '最低持有天数',
  pricing: '计价规则', settlement: '结算与到账', cancellation: '撤单窗口', limitations: '模拟范围',
};
export const displayDisclosure = (value: unknown): string => value == null ? '未提供' : Array.isArray(value)
  ? value.map(displayDisclosure).join('；') : typeof value === 'object'
    ? Object.entries(value).map(([key, item]) => `${key}：${displayDisclosure(item)}`).join('；') : String(value);
