// DEV-only explicit recording. Synthetic IDs never reach a bank endpoint.
import recording from '../../../../tests/fixtures/wealth_portfolio_contract_v1/f23.json' with {type:'json'};
import type {Assets,CancellationEligibility} from '../../services/wealth';
import type {EligibilityLoader} from './OrderTable';
import {recordedDetail} from './FixtureProductProvider.ts';
import type {ProductDetailLoader} from './ProductDetail';
export const recordedTableAssets=():Assets=>structuredClone(recording.assets) as Assets;
export const recordedTableEligibility:EligibilityLoader=async id=>{
  const found=recording.cancellation_eligibility.find(row=>row.operation_id===id);
  if(!found)throw new Error('F23未录制该订单的撤单资格');
  return structuredClone(found) as CancellationEligibility;
};
export const recordedTableDetail:ProductDetailLoader=async(id,period,version)=>{
  if(version!=null&&version!==1)throw new Error('F23没有录制此版本的产品资料，不展示其他版本替代');
  const base=await recordedDetail(id,period,version);
  const observation=recording.product_observations.find(p=>p.product_id===id&&p.product_version===1);
  if(!observation)return {...base,identity:'F23契约样例 · 虚构数据'};
  const points=structuredClone(observation.points);
  return {...base,identity:'F23契约样例 · 虚构数据',as_of:recording.assets.as_of,
    latest_nav:{nav:points[1].nav,date:points[1].date},money_yield:[],chart:{points,window_start:points[0].date,window_end:points[1].date,
      quality_flags:[],notice:'F23明确录制的两个虚构观察点，与本表资料时点一致；不是实际基金行情或本人收益。',
      metrics:{nav_change:{value_percent:observation.value_percent,reason:null},max_drawdown:{value_percent:null,reason:'样例未提供'},volatility:{value_percent:null,reason:'样例未提供'}}}};
};
