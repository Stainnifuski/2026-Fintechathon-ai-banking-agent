import {CSSProperties,useLayoutEffect,useRef} from 'react';
import * as engine from 'echarts/core';

type Handlers=Record<string,(event:any)=>void>;
function bind(chart:engine.EChartsType,previous:Handlers,next:Handlers) {
 Object.entries(previous).forEach(([name,handler])=>chart.off(name,handler));
 Object.entries(next).forEach(([name,handler])=>chart.on(name,handler));
 return next;
}

/** Hidden workspaces retain data; initialize only once their viewport is measurable. */
export default function Chart({option,onEvents,style}:{option:engine.EChartsCoreOption;onEvents?:Handlers;style?:CSSProperties;echarts?:typeof engine}) {
 const node=useRef<HTMLDivElement>(null),instance=useRef<engine.EChartsType|null>(null);
 const latest=useRef({option,onEvents});latest.current={option,onEvents};
 const bound=useRef<Handlers>({});
 useLayoutEffect(()=>{
  const element=node.current;if(!element)return;
  let frame=0,closed=false;
  const initialize=()=>{
   if(closed||!element.clientWidth||!element.clientHeight)return;
   if(!instance.current){
    const chart=engine.init(element);instance.current=chart;
    chart.setOption(latest.current.option,{notMerge:true});
    bound.current=bind(chart,{},latest.current.onEvents||{});
   }else if(!instance.current.isDisposed())instance.current.resize();
  };
  initialize();
  const observer=new ResizeObserver(()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(initialize);});
  observer.observe(element);
  return()=>{closed=true;observer.disconnect();cancelAnimationFrame(frame);const chart=instance.current;instance.current=null;bound.current={};if(chart&&!chart.isDisposed())chart.dispose();};
 },[]);
 useLayoutEffect(()=>{instance.current?.setOption(option,{notMerge:true});},[option]);
 useLayoutEffect(()=>{const chart=instance.current;if(chart&&!chart.isDisposed())bound.current=bind(chart,bound.current,onEvents||{});},[onEvents]);
 return <div ref={node} style={{width:'100%',...style}}/>;
}
