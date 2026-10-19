import {CSSProperties,useLayoutEffect,useRef} from 'react';
import * as engine from 'echarts/core';

/** Synchronous chart lifetime prevents delayed initialization after a report unmounts. */
export default function Chart({option,onEvents,style}:{option:engine.EChartsCoreOption;onEvents?:Record<string,(event:any)=>void>;style?:CSSProperties;echarts?:typeof engine}) {
 const node=useRef<HTMLDivElement>(null);
 const instance=useRef<engine.EChartsType|null>(null);
 useLayoutEffect(()=>{
  if(!node.current)return;
  const chart=engine.init(node.current);
  instance.current=chart;
  let frame=0;
  const observer=new ResizeObserver(()=>{
   cancelAnimationFrame(frame);
   frame=requestAnimationFrame(()=>{if(!chart.isDisposed()&&node.current?.clientWidth)chart.resize();});
  });
  observer.observe(node.current);
  return()=>{observer.disconnect();cancelAnimationFrame(frame);instance.current=null;chart.dispose();};
 },[]);
 useLayoutEffect(()=>{instance.current?.setOption(option,{notMerge:true});},[option]);
 useLayoutEffect(()=>{
  const chart=instance.current;
  if(!chart)return;
  const handlers=Object.entries(onEvents||{});
  handlers.forEach(([name,handler])=>chart.on(name,handler));
  return()=>{if(!chart.isDisposed())handlers.forEach(([name,handler])=>chart.off(name,handler));};
 },[onEvents]);
 return <div ref={node} style={{width:'100%',...style}}/>;
}
