import {useMemo} from 'react';
import * as echarts from 'echarts/core';
import {BarChart,LineChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,LegendComponent,AriaComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import Chart from './BillAnalysis/Chart';
import type {BillResultChart,BillResultTarget} from '../services/nova';
echarts.use([BarChart,LineChart,GridComponent,TooltipComponent,LegendComponent,AriaComponent,CanvasRenderer]);
const money=(value:number)=>(value/100).toLocaleString('zh-CN',{style:'currency',currency:'CNY'});
export default function NovaBillResultChart({chart,onNavigate}:{chart:BillResultChart;onNavigate:(target:BillResultTarget)=>void}) {
 const first=chart.series[0]?.points??[];
 const option=useMemo<echarts.EChartsCoreOption>(()=>({
  color:['#7660d8','#55a3bc','#db9b56'],animation:false,
  aria:{enabled:true,description:chart.title},
  // Rich text tooltips are drawn on canvas; merchant labels never become HTML.
  tooltip:{trigger:'axis',renderMode:'richText',confine:true,valueFormatter:(value:unknown)=>money(Number(value))},
  legend:{show:chart.series.length>1,bottom:0,textStyle:{color:'#667085',fontSize:11}},
  grid:{top:15,right:20,bottom:chart.series.length>1?64:45,left:12,containLabel:true},
  xAxis:chart.type==='bar'?{type:'value',axisLabel:{formatter:(value:number)=>`${value/100}元`}}:{type:'category',data:first.map(point=>point.label),axisLabel:{hideOverlap:true,formatter:(value:string)=>value.slice(5)}},
  yAxis:chart.type==='bar'?{type:'category',inverse:true,data:first.map(point=>point.label),axisLabel:{width:90,overflow:'truncate'}}:{type:'value',axisLabel:{formatter:(value:number)=>`${value/100}元`}},
  series:chart.series.map(series=>({name:series.name,type:chart.type,data:series.points.map(point=>point.value_fen),barMaxWidth:22,symbol:'circle',symbolSize:4,lineStyle:{width:2},areaStyle:chart.type==='line'&&chart.series.length===1?{opacity:.07}:undefined})),
 }),[chart,first]);
 const height=chart.type==='bar'?Math.max(210,first.length*36+55):260;
 return <section className="bill-result-chart" aria-label={chart.title}><h4>{chart.title}</h4>
 {first.length?<Chart option={option} style={{height}} onEvents={{click:event=>{const point=chart.series[event.seriesIndex??0]?.points[event.dataIndex];if(point?.target)onNavigate(point.target);}}}/>:<p>本次范围内暂无图表数据</p>}
 <details><summary>查看图表数据{chart.type==='bar'?' · 可按分类查看明细':''}</summary><div className="bill-result-table"><table><thead><tr><th>{chart.type==='bar'?'分类':'日期'}</th>{chart.series.map(series=><th key={series.name}>{series.name}</th>)}</tr></thead><tbody>{first.map((point,index)=><tr key={index}><th>{point.target?<button className="text-button" onClick={()=>onNavigate(point.target!)}>{point.label} ↗</button>:point.label}</th>{chart.series.map(series=><td key={series.name}>{series.points[index]?money(series.points[index].value_fen):'尚未核实'}</td>)}</tr>)}</tbody></table></div></details>
 </section>;
}
