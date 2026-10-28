// NOVA orb motion studies. Presentation-only Canvas renderer.
export function createMascot(canvas, { style = 1, seed = 20261025 } = {}) {
  const x = canvas.getContext('2d'); if (!x) return {destroy(){},setBusy(){},setState(){}};
  let raf=0,dead=false,busy=false,mode='waiting',w=1,h=1,phase=0,last=0,tempo=1,state=seed>>>0,pixelRatio=1,transitionAlpha=0;
  const transitionCanvas=document.createElement('canvas');
  const transitionCtx=transitionCanvas.getContext('2d');
  const rnd=()=>{state=(1664525*state+1013904223)>>>0;return state/4294967296};
  const dots=Array.from({length:70},()=>({a:rnd()*6.28,r:.5+rnd()*.5,v:.14+rnd()*.36,z:.6+rnd()*2.4,o:.2+rnd()*.6}));
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const ellipse=(a,b,c,d,fill,rot=0)=>{x.beginPath();x.ellipse(a,b,c,d,rot,0,6.28);x.fillStyle=fill;x.fill()};
  const line=(p,col,width=1)=>{x.beginPath();p.forEach(([a,b],i)=>i?x.lineTo(a,b):x.moveTo(a,b));x.strokeStyle=col;x.lineWidth=width;x.stroke()};
  const halo=(a,b,r,col)=>{const g=x.createRadialGradient(a,b,0,a,b,r);g.addColorStop(0,col+'45');g.addColorStop(.45,col+'18');g.addColorStop(1,col+'00');ellipse(a,b,r,r,g)};
  const grad=(r,pal)=>{const g=x.createRadialGradient(-r*.35,-r*.5,0,0,0,r*1.3);pal.forEach(([p,c])=>g.addColorStop(p,c));return g};
  const orb=(r,n=0)=>{const pal=[['#fff9ff','#c8b7ff','#695cf4','#2835a3'],['#eaffff','#9de5ea','#6579f4','#472b9f'],['#fff5ff','#dfc7ff','#7a5ce8','#21295e'],['#f4ffff','#add9ff','#7f62ef','#352187']][n];return grad(r,[[0,pal[0]],[.28,pal[1]],[.65,pal[2]],[1,pal[3]]])};
  const star=(a,b,r,col,rot=0)=>{x.save();x.translate(a,b);x.rotate(rot);x.beginPath();for(let i=0;i<8;i++){const q=i*Math.PI/4-Math.PI/2,rr=i%2?r*.2:r;i?x.lineTo(Math.cos(q)*rr,Math.sin(q)*rr):x.moveTo(Math.cos(q)*rr,Math.sin(q)*rr)}x.closePath();x.fillStyle=col;x.fill();x.restore()};
  const shadow=(y,r)=>{x.save();x.filter='blur(9px)';ellipse(0,y,r,8,'#35366e20');x.restore()};
  function prism(t,s){x.save();x.scale(s,s);const b=Math.sin(t*1.1)*5,r=63+Math.sin(t*.7)*2;shadow(86,68);halo(0,b,122,'#b39cff');x.save();x.translate(0,b);x.rotate(Math.sin(t*.4)*.08);ellipse(0,0,r,r,orb(r,0));x.globalAlpha=.22;x.beginPath();x.arc(0,0,r*.85,-.9,1.8);x.strokeStyle='#fff';x.lineWidth=2;x.stroke();x.globalAlpha=1;star(-17,-11,24,'#fff',.1);star(24,25,6,'#f8f0ff',-.3);ellipse(-34,-40,18,7,'#fff8ff80',-.45);x.restore();for(let i=0;i<4;i++){const a=t*.3+i*1.57;star(Math.cos(a)*(88+i*6),b+Math.sin(a)*(35+i*5),3+i*.5,'#f9f1ffbb',a)}x.restore()}
  function liquid(t,s){x.save();x.scale(s,s);const b=Math.sin(t*.8)*8,r=61;shadow(88,73);halo(0,b,150,'#83caff');x.save();x.translate(0,b);ellipse(0,0,r,r,orb(r,1));x.save();x.beginPath();x.arc(0,0,r,0,6.28);x.clip();for(let i=-2;i<3;i++){const y=i*24+Math.sin(t*1.2+i)*8;x.beginPath();x.moveTo(-75,y);x.bezierCurveTo(-30,y-15,17,y+16,78,y-2);x.strokeStyle=i%2?'#d4ffff74':'#fff8ff5a';x.lineWidth=8;x.stroke()}x.restore();star(0,0,25,'#ffffffd9',Math.sin(t)*.08);ellipse(-29,-40,16,6,'#fff8ff8c',-.4);x.restore();for(let i=0;i<3;i++){x.beginPath();x.ellipse(0,75+i*8,70+i*23+Math.sin(t+i)*8,7+i*3,0,0,6.28);x.strokeStyle=`rgba(116,177,245,${.28-i*.06})`;x.lineWidth=1;x.stroke()}x.restore()}
  function orbit(t,s){x.save();x.scale(s,s);const b=Math.sin(t*.9)*4,r=58;shadow(86,68);halo(0,b,132,'#977dff');for(let i=0;i<3;i++){x.save();x.rotate(t*(.16+i*.06)+i*1.05);x.beginPath();x.ellipse(0,b,89+i*14,28+i*5,i*.4,0,6.28);x.strokeStyle=['#f1d8ff96','#8edfff9e','#fff4ff70'][i];x.lineWidth=i===1?2:1;x.stroke();x.restore()}x.save();x.translate(0,b);ellipse(0,0,r,r,orb(r,2));star(0,0,22,'#fff',t*.2);ellipse(-30,-37,16,6,'#fff9ff85',-.4);x.restore();dots.slice(0,15).forEach((p,i)=>{const a=p.a+t*p.v;ellipse(Math.cos(a)*(73+i%4*13),b+Math.sin(a)*(25+i%3*12),p.z,p.z,`rgba(240,222,255,${p.o})`)});x.restore()}
  function nebula(t,s){
    x.save();
    x.scale(s,s);
    const b=Math.sin(t*1.05)*6;
    const r=61;
    const thinking=mode==='thinking';
    const typing=mode==='typing';
    const intensity=thinking?1.32:typing?1.16:.92;
    shadow(88,thinking?78:70);
    halo(0,b,160*intensity,thinking?'#4b32d8':'#7969f9');
    for(let i=0;i<15;i++){
      const a=i*.41+t*(typing?.24:thinking?.32:.16);
      const rr=74+Math.sin(t*.5+i)*17;
      ellipse(Math.cos(a)*rr,b+Math.sin(a)*rr*.42,7+i%3*3,7+i%2*3,`rgba(${thinking?116:150+i*4},${thinking?102:128+i*4},255,${.1+i*.014})`)
    }
    x.save();
    x.translate(0,b);
    ellipse(0,0,r,r,orb(r,3));
    x.save();
    x.beginPath();
    x.arc(0,0,r,0,6.28);
    x.clip();
    const washCount=thinking?4:typing?3:2;
    for(let i=0;i<washCount;i++){
      const drift=t*(.22+i*.11)+i*1.7;
      const cx=Math.cos(drift)*r*.52;
      const cy=Math.sin(drift*.83)*r*.48;
      const wash=x.createRadialGradient(cx,cy,2,cx,cy,r*.9);
      const hue=thinking?['#32147c88','#0a668c72','#813bb576','#1a2f8a66'][i]:typing?['#2b4aa66a','#0a87906a','#8a43ba5e'][i]:['#7250c744','#1d849044'][i];
      wash.addColorStop(0,hue);
      wash.addColorStop(.55,hue.replace(/[0-9a-f]{2}$/,'38'));
      wash.addColorStop(1,'#00000000');
      x.fillStyle=wash;
      x.globalCompositeOperation='screen';
      x.fillRect(-r,-r,r*2,r*2);
    }
    x.globalCompositeOperation='source-over';
    x.restore();
    if(thinking){
      const shade=x.createRadialGradient(-12,-18,8,0,0,72);
      shade.addColorStop(0,'#7358ec66');
      shade.addColorStop(.56,'#3b248f55');
      shade.addColorStop(1,'#17104d88');
      ellipse(0,0,r,r,shade);
    } else if(typing){
      const shade=x.createRadialGradient(-10,-16,4,0,0,74);
      shade.addColorStop(0,'#8872ec4d');
      shade.addColorStop(.54,'#4830a055');
      shade.addColorStop(1,'#21165f70');
      ellipse(0,0,r,r,shade);
    }
    const starPulse=thinking?1+Math.sin(t*3.8)*.24:typing?1+Math.sin(t*2.4)*.14:1+Math.sin(t*1.4)*.06;
    const starAlpha=thinking?.72+.28*(.5+.5*Math.sin(t*3.8)):typing?.84+.16*(.5+.5*Math.sin(t*2.4)):.92;
    x.globalAlpha=starAlpha;
    star(0,0,(thinking?28:typing?25:23)*starPulse,'#fff',-.2+Math.sin(t*.7)*.1);
    x.globalAlpha=1;
    ellipse(-32,-39,17,6,'#ffffff90',-.42);
    x.restore();
    dots.forEach((p,i)=>{
      const a=p.a+t*p.v*intensity+Math.sin(t*(.7+p.r)+p.a)*.1;
      const rr=(thinking?92:86)+p.r*55+Math.sin(t*.9+i)*5;
      const size=p.z*(thinking?1.55:typing?1.25:1);
      ellipse(Math.cos(a)*rr,b+Math.sin(a)*rr*.5,size,size,`rgba(${thinking?'202,188,255':typing?'73,158,190':'86,80,156'},${p.o*(thinking?.9:typing?.78:.68)})`)
    });
    const ringStart=typing?1:0;
    const ringCount=thinking?0:typing?3:2;
    const ringPalette=thinking?['#3a237bce','#0b6377c8','#6f3cabca','#244a82b8','#7b4384a8']:typing?['#4777a8c8','#966fc1c0','#3c98a8c0','#7c71b8b0']:['#d7f0f4b8','#f2ddf4a8'];
    for(let i=ringStart;i<ringStart+ringCount;i++){
      const speed=thinking?0:typing?[.58,-.12,.34,-.42][i]:[.06,-.09][i];
      const phase=t*speed+i*1.9+Math.sin(t*(.7+i*.13)+i)*.35;
      const depth=.62+.38*(.5+.5*Math.sin(phase*.83+i*.6));
      const baseRadius=thinking?94:typing?88:156;
      const step=thinking?13:typing?28:28;
      const rx=(baseRadius+i*step)*depth;
      const ry=(thinking?22+i*4:typing?27+i*6:42+i*8)*(0.62+.38*Math.abs(Math.sin(phase)));
      const tilt=phase*.42+i*.91+Math.sin(t*(.55+i*.2)+i)*.65;
      x.beginPath();
      x.ellipse(0,b,rx,ry,tilt,0,6.28);
      x.strokeStyle=ringPalette[i];
      x.lineWidth=i===0?2.8:i===2?2.2:1.5;
      x.stroke();
    }
    if(thinking){
      const pulse=1+Math.sin(t*3.2)*.11;
      for(let i=0;i<3;i++){
        x.beginPath();
        x.arc(0,b,(78+i*15)*pulse,0,6.28);
        x.strokeStyle=i===0?'#33216fcf':i===1?'#075b72c7':'#54258fc4';
        x.lineWidth=i===0?2.8:1.4;
        x.globalAlpha=.9-i*.18;
        x.stroke();
      }
      x.globalAlpha=1;
      for(let i=0;i<7;i++){
        const a=t*1.15+i*.9;
        const rr=108+Math.sin(t*2+i)*12;
        ellipse(Math.cos(a)*rr,b+Math.sin(a)*rr*.5,1.5+(i%2),1.5+(i%2),i%2?'#087b92e8':'#6030a9e8');
      }
    }
    x.restore();
  }
  function draw(t){
    x.clearRect(0,0,w,h);
    x.save();
    x.translate(w/2,h*.5);
    const s=Math.min(w/410,h/260,1.45);
    [prism,liquid,orbit,nebula][Math.max(0,Math.min(3,style-1))](t,s);
    x.restore();
    if(transitionAlpha>0&&transitionCanvas.width){
      x.save();
      x.setTransform(1,0,0,1,0,0);
      x.globalAlpha=transitionAlpha;
      x.drawImage(transitionCanvas,0,0,canvas.width,canvas.height);
      x.restore();
      x.setTransform(pixelRatio,0,0,pixelRatio,0,0);
    }
  }
  const resize=new ResizeObserver(([e])=>{w=e.contentRect.width;h=e.contentRect.height;pixelRatio=Math.min(devicePixelRatio||1,2);canvas.width=w*pixelRatio;canvas.height=h*pixelRatio;transitionCanvas.width=canvas.width;transitionCanvas.height=canvas.height;x.setTransform(pixelRatio,0,0,pixelRatio,0,0);draw(phase)});resize.observe(canvas);
  const onReduced=()=>{last=0;draw(phase)};reduced.addEventListener('change',onReduced);
  function frame(now){if(dead)return;const elapsed=last?Math.min((now-last)/1000,.05):0;const target=mode==='thinking'?2.35:mode==='typing'?1.42:.88;tempo+=(target-tempo)*.05;if(elapsed&&!document.hidden&&!reduced.matches)phase+=elapsed*tempo;if(transitionAlpha>0&&!reduced.matches)transitionAlpha=Math.max(0,transitionAlpha-elapsed/0.42);last=now;if(!document.hidden&&!reduced.matches)draw(phase);raf=requestAnimationFrame(frame)}
  function setMode(next){const normalized=next==='thinking'||next==='typing'?next:'waiting';if(normalized!==mode&&!reduced.matches&&transitionCtx&&canvas.width){transitionCanvas.width=canvas.width;transitionCanvas.height=canvas.height;transitionCtx.clearRect(0,0,canvas.width,canvas.height);transitionCtx.drawImage(canvas,0,0);transitionAlpha=1}mode=normalized;busy=mode==='thinking'}
  raf=requestAnimationFrame(frame);return {setBusy(v){setMode(v?'thinking':'waiting')},setState:setMode,destroy(){dead=true;cancelAnimationFrame(raf);resize.disconnect();reduced.removeEventListener('change',onReduced)},get phase(){return phase}};
}
