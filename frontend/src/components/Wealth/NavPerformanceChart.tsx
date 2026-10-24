import * as echarts from 'echarts/core';
import {LineChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,AriaComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import Chart from '../BillAnalysis/Chart';
import {navPerformance,performanceLabel} from './navPerformance';

echarts.use([LineChart,GridComponent,TooltipComponent,AriaComponent,CanvasRenderer]);

/** Same chart interaction as bill insights; one NAV-relative performance line. */
export default function NavPerformanceChart({points=[],performance}:{points?:{date:string;nav:string}[];performance?:{date:string;percent:number|null}[]}){
  const series=performance??navPerformance(points);
  if(series.filter(p=>p.percent!==null).length<2)return <p>暂无足够的可靠数据绘制曲线。</p>;
  const values=series.map(p=>p.percent),known=values.filter((v):v is number=>v!==null),low=Math.min(0,...known),high=Math.max(0,...known);
  const gap=Math.max((high-low)*.08,.1);
  const description=performance?'个人整体理财区间收益率，已校正申赎现金流；缺失点不连线。':'本基金净值涨跌幅，所选区间首值为0%。';
  return <section aria-label={performance?'整体理财收益率':'本基金净值涨跌幅'}>
    {!performance&&<p className="hint">以所选区间首个净值为基准，非含分红总回报。</p>}
    <Chart style={{height:290}} option={{animation:false,
      aria:{enabled:true,description},grid:{left:65,right:20,top:20,bottom:40},
      tooltip:{trigger:'axis',triggerOn:'mousemove|click',confine:true,renderMode:'richText',
        formatter:(params:unknown)=>{const items=params as {dataIndex:number}[];const p=series[items[0]?.dataIndex];return p?`${p.date}\n${p.percent===null?'待更新':performanceLabel(p.percent)}`:'';},
        axisPointer:{type:'line',lineStyle:{type:'dashed',color:'#94a3b8'}}},
      xAxis:{type:'category',boundaryGap:false,data:series.map(p=>p.date),axisLabel:{hideOverlap:true,formatter:(date:string)=>date.slice(5)},axisLine:{lineStyle:{color:'#94a3b8'}}},
      yAxis:{type:'value',min:low-gap,max:high+gap,axisLabel:{formatter:(v:number)=>`${Number(v.toFixed(2))}%`},splitLine:{lineStyle:{color:'#e2e8f0'}}},
      series:[{name:performance?'整体收益率':'本基金',type:'line',connectNulls:false,showSymbol:false,symbolSize:7,lineStyle:{width:2.5,color:'#3563e9'},itemStyle:{color:'#3563e9'},data:values}]
    }}/>
    <span className="hint">{series[0].date} — {series[series.length-1].date}</span>
  </section>;
}
