import * as echarts from 'echarts/core';
import {LineChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,AriaComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import Chart from '../BillAnalysis/Chart';
import {navPerformance,performanceLabel} from './navPerformance';

echarts.use([LineChart,GridComponent,TooltipComponent,AriaComponent,CanvasRenderer]);

/** Same chart interaction as bill insights; one NAV-relative performance line. */
export default function NavPerformanceChart({points}:{points:{date:string;nav:string}[]}){
  const series=navPerformance(points);
  if(series.length<2)return <p>本窗口可靠历史不足，图表已隐藏。</p>;
  const values=series.map(p=>p.percent),low=Math.min(0,...values),high=Math.max(0,...values);
  const gap=Math.max((high-low)*.08,.1);
  const description=`本基金净值涨跌幅，所选区间首值为0%。${series[0].date}至${series[series.length-1].date}，末值${performanceLabel(values[values.length-1])}。`;
  return <section aria-label="本基金净值涨跌幅">
    <p className="hint">以所选区间首个净值为基准，非含分红总回报。</p>
    <Chart style={{height:290}} option={{animation:false,
      aria:{enabled:true,description},grid:{left:65,right:20,top:20,bottom:40},
      tooltip:{trigger:'axis',triggerOn:'mousemove|click',confine:true,renderMode:'richText',
        formatter:(params:unknown)=>{const items=params as {dataIndex:number}[];const p=series[items[0]?.dataIndex];return p?`${p.date}\n${performanceLabel(p.percent)}`:'';},
        axisPointer:{type:'line',lineStyle:{type:'dashed',color:'#94a3b8'}}},
      xAxis:{type:'category',boundaryGap:false,data:series.map(p=>p.date),axisLabel:{hideOverlap:true,formatter:(date:string)=>date.slice(5)},axisLine:{lineStyle:{color:'#94a3b8'}}},
      yAxis:{type:'value',min:low-gap,max:high+gap,axisLabel:{formatter:(v:number)=>`${Number(v.toFixed(2))}%`},splitLine:{lineStyle:{color:'#e2e8f0'}}},
      series:[{name:'本基金',type:'line',showSymbol:false,symbolSize:7,lineStyle:{width:2.5,color:'#3563e9'},itemStyle:{color:'#3563e9'},data:values}]
    }}/>
    <span className="hint">{series[0].date} — {series[series.length-1].date}</span>
  </section>;
}
