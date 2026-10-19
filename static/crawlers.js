/* Information walker: eight fixed-length IK legs, alternating support pairs.
   The feet plant on the visible page surface, not distant panel borders.
   Scroll semantics: a viewport companion; planted screen-space feet remain
   stationary while the information target pool is refreshed beneath them. */
(() => {
'use strict';
const canvas=document.createElement('canvas');canvas.id='billCrawlers';canvas.setAttribute('aria-hidden','true');document.body.append(canvas);
const ctx=canvas.getContext('2d'),hero=document.getElementById('billArt'),hc=hero.getContext('2d'),media=matchMedia('(prefers-reduced-motion: reduce)');
const button=document.createElement('button');button.id='billMotion';button.type='button';const dock=document.createElement('div');dock.className='bill-dock';dock.append(button);const note=document.createElement('span');dock.append(note);document.querySelector('.navline').after(dock);
let enabled=true;try{enabled=localStorage.getItem('fomobill-crawl-motion')!=='off'}catch(_){}
let agents=[],clock=0,last=0,frame=0,dirty=true,mobile=false,targets=[],marks=[],form=hero.dataset.form||'watch',gaze=0,heroSize={width:0,height:0};
const clamp=(n,a,b)=>Math.max(a,Math.min(b,n)),rect=e=>e.getBoundingClientRect(),active=()=>enabled&&!media.matches;
const REACH=60,BONE=32,PAIRS=[[0,5],[2,7],[4,1],[6,3]],RENDER=Object.freeze({form:'investigate',palette:['#ff5447','#df1d32','#671422']});
function label(){button.textContent='Bill: motion '+(active()?'on':'off');button.setAttribute('aria-pressed',String(active()));note.textContent=active()?'Bill walks across the data · animation, not analysis':'Static Bill · motion disabled';}label();button.addEventListener('click',()=>{enabled=!enabled;try{localStorage.setItem('fomobill-crawl-motion',enabled?'on':'off')}catch(_){}label()});media.addEventListener('change',label);
function collect(){
 const els=[...document.querySelectorAll('.market-card h3, .market-card dd, .transaction-row a, .evidence-card h3, .evidence-card .address, .watch-observation .address')];
 targets=els.filter(el=>!el.classList.contains('event')||!el.querySelector('strong,b')).map(el=>{const r=rect(el);return {el,x:clamp(r.left+r.width*.48,65,innerWidth-65),y:clamp(r.top+r.height*.5,90,innerHeight-65),text:el.textContent.trim().slice(0,110)}}).filter(t=>{const r=rect(t.el);return r.width>0&&r.height>0&&r.top>=35&&r.bottom<=innerHeight-20});
}
function choose(a){let pool=targets.filter(t=>t.el!==a.goal?.el&&Math.hypot(t.x-a.x,t.y-a.y)>45);if(!pool.length)pool=targets;if(!pool.length){a.goal=null;return}pool.sort((p,q)=>(a.visits.get(p.el)||0)-(a.visits.get(q.el)||0)||Math.hypot(p.x-a.x,p.y-a.y)-Math.hypot(q.x-a.x,q.y-a.y));a.goal=pool[a.id%Math.min(2,pool.length)];a.targets++;a.nextGoal=5;}
function hip(a,i,x=a.x,y=a.y){return {x:x+(i<4?-1:1)*[10,18,24,28][i%4]*a.scale,y:y+[-23,-9,8,25][i%4]*a.scale}}
function nominal(a,i,lead=0){const side=i<4?-1:1;return {x:clamp(a.x+side*[32,43,43,32][i%4]+a.vx*lead,8,innerWidth-8),y:clamp(a.y+[-35,-13,16,39][i%4]+a.vy*lead,10,innerHeight-10)}}
function make(id){const t=targets[id%Math.max(1,targets.length)];const a={id,x:t?t.x:innerWidth*.55,y:t?t.y:innerHeight*.5,scale:mobile?.60:.72,vx:0,vy:0,steps:0,targets:0,nextGoal:0,pair:0,lean:0,visits:new Map(),trail:[],pause:0};a.hands=Array.from({length:8},(_,i)=>({...nominal(a,i),moving:false,progress:0,plant:0,lift:0}));choose(a);return a}
function layout(){const was=mobile;mobile=innerWidth<=650;const dpr=Math.min(devicePixelRatio||1,2);canvas.width=innerWidth*dpr;canvas.height=innerHeight*dpr;canvas.style.width=innerWidth+'px';canvas.style.height=innerHeight+'px';ctx.setTransform(dpr,0,0,dpr,0,0);collect();if(!agents.length||was!==mobile)agents=Array.from({length:mobile?1:2},(_,i)=>make(i));else agents.forEach(a=>{const x=clamp(a.x,65,innerWidth-65),y=clamp(a.y,90,innerHeight-65),dx=x-a.x,dy=y-a.y;a.x=x;a.y=y;a.hands.forEach(h=>{h.x+=dx;h.y+=dy;if(h.from){h.from.x+=dx;h.from.y+=dy;h.to.x+=dx;h.to.y+=dy}});a.trail=[];a.nextGoal=0});const r=rect(hero);heroSize={width:r.width,height:r.height};hero.width=r.width*dpr;hero.height=r.height*dpr;hc.setTransform(dpr,0,0,dpr,0,0);dirty=false}
function visit(a){const el=a.goal.el;a.visits.set(el,(a.visits.get(el)||0)+1);marks=marks.filter(m=>m.el!==el);marks.push({el,time:clock,text:el.textContent.trim().slice(0,110)});if(marks.length>8)marks.shift();a.pause=.18;choose(a)}
function update(a,dt){
 a.nextGoal-=dt;if(a.nextGoal<=0)choose(a);if(a.goal&&Math.hypot(a.goal.x-a.x,a.goal.y-a.y)<9)visit(a);
 a.pause=Math.max(0,a.pause-dt);const dx=a.goal?a.goal.x-a.x:0,dy=a.goal?a.goal.y-a.y:0,d=Math.hypot(dx,dy)||1,speed=a.pause?0:Math.min(mobile?88:104,d*4);a.vx=dx/d*speed;a.vy=dy/d*speed;
 // An ordinary step is an airborne arc between two fixed surface points.
 for(const h of a.hands){if(!h.moving)continue;h.progress=Math.min(1,h.progress+dt/.105);const p=h.progress,t=p*p*(3-2*p);h.lift=Math.sin(p*Math.PI)*7;h.x=h.from.x+(h.to.x-h.from.x)*t;h.y=h.from.y+(h.to.y-h.from.y)*t-h.lift;if(p===1){h.x=h.to.x;h.y=h.to.y;h.moving=false;h.lift=0;h.plant++;a.steps++}}
 // Four diagonal pairs alternate. Six feet support the body at any time.
 if(!a.hands.some(h=>h.moving)&&speed>0){const pair=PAIRS[a.pair++%4];for(const i of pair){const h=a.hands[i];h.from={x:h.x,y:h.y};h.to=nominal(a,i,.17);h.progress=0;h.moving=true}}
 // Constrain torso displacement, never stretch bones or drag planted feet.
 let fraction=1,feasible=false;for(let n=0;n<14;n++){const x=a.x+a.vx*dt*fraction,y=a.y+a.vy*dt*fraction;if(a.hands.every((h,i)=>{const p=hip(a,i,x,y);return Math.hypot(h.x-p.x,h.y-p.y)<=REACH})){feasible=true;break}fraction*=.5}if(!feasible)fraction=0;
 a.x+=a.vx*dt*fraction;a.y+=a.vy*dt*fraction;a.lean=0;
 const tail=a.trail.at(-1);if(!tail||Math.hypot(a.x-tail.x,a.y-tail.y)>3)a.trail.push({x:a.x,y:a.y,time:clock});a.trail=a.trail.filter(p=>clock-p.time<1.7).slice(-80);
}
function geometry(a,h,i){const start=hip(a,i),dx=h.x-start.x,dy=h.y-start.y,len=Math.hypot(dx,dy)||.001,bend=Math.sqrt(Math.max(0,BONE*BONE-len*len/4)),side=i<4?-1:1;return {start,end:{x:h.x,y:h.y},elbow:{x:(start.x+h.x)/2-side*dy/len*bend,y:(start.y+h.y)/2+side*dx/len*bend},reach:len}}
function drawMarks(){marks=marks.filter(m=>clock-m.time<4.5);for(const m of marks){const r=rect(m.el),alpha=Math.max(0,1-(clock-m.time)/4.5);ctx.strokeStyle=`rgba(246,219,98,${alpha*.5})`;ctx.lineWidth=.8;ctx.strokeRect(r.left-2,r.top-2,r.width+4,r.height+4);ctx.beginPath();ctx.moveTo(r.left,r.bottom+2);ctx.lineTo(r.right,r.bottom+2);ctx.stroke()}}
function draw(a){
 ctx.save();ctx.lineCap='round';ctx.lineJoin='round';for(let i=1;i<a.trail.length;i++){const p=a.trail[i-1],q=a.trail[i];BillIllustration.stroke(ctx,[[p.x,p.y],[q.x,q.y]],`rgba(255,84,71,${Math.max(0,1-(clock-q.time)/1.7)*.22})`,1)}
 a.hands.forEach((h,i)=>{const g=geometry(a,h,i);BillIllustration.stroke(ctx,[[g.start.x,g.start.y],[g.elbow.x,g.elbow.y],[h.x,h.y]],h.moving?'#ffdf87':'#d8ba78',h.moving?1.1:.9);ctx.fillStyle=h.moving?'#ff7669':'#f4de8b';ctx.beginPath();ctx.arc(h.x,h.y,h.moving?1.1:1.65,0,Math.PI*2);ctx.fill()});
 BillIllustration.draw(ctx,{x:a.x,y:a.y,scale:a.scale,form:RENDER.form,clock:clock+a.id*1.7,lean:0,limbs:false});ctx.restore();
}
function drawHero(){hc.clearRect(0,0,heroSize.width,heroSize.height);const scale=Math.min(heroSize.width/155,(heroSize.height-25)/140);BillIllustration.draw(hc,{x:heroSize.width/2,y:heroSize.height*.56+Math.sin(clock*1.3)*3,scale,form,clock,lean:Math.sin(clock*.8)*.025,gaze,limbs:true});const fallback=document.getElementById('billFallback');if(fallback)fallback.hidden=true}
function protectContent(){
 // Clear whole reading surfaces, including trails and limbs, every frame.
 // pointer-events:none alone does not prevent visual obstruction.
 for(const node of document.querySelectorAll('.panel,.sysline,.brandrow,.demo,.navline,.bill-dock,.first-actions,.command,.limitations,footer')){
  const r=rect(node);ctx.clearRect(r.left-1,r.top-1,r.width+2,r.height+2);
 }
}
function tick(now){const dt=Math.min((now-last)/1000||0,.035);last=now;if(dirty)layout();if(!document.hidden){if(active()){clock+=dt;agents.forEach(a=>update(a,dt))}ctx.clearRect(0,0,innerWidth,innerHeight);drawMarks();agents.forEach(draw);protectContent();drawHero();frame++}requestAnimationFrame(tick)}
addEventListener('resize',()=>dirty=true);addEventListener('scroll',()=>{collect();agents.forEach(a=>a.nextGoal=0)},{passive:true});document.addEventListener('visibilitychange',()=>last=performance.now());addEventListener('pointermove',e=>{if(active())gaze=clamp((e.clientX-innerWidth/2)/(innerWidth/2),-1,1)});
addEventListener('bill-state',e=>{form=e.detail;hero.dataset.form=form;collect();agents.forEach(a=>a.nextGoal=0)});
window.__billCrawl={snapshot:()=>({enabled,reduced:media.matches,hidden:document.hidden,width:innerWidth,height:innerHeight,frame,clock,mobile,hero:{form,clock,gaze,eye:BillIllustration.eyePose(clock,gaze)},marks:marks.map(m=>({text:m.text,age:clock-m.time})),agents:agents.map(a=>({id:a.id,x:a.x,y:a.y,eye:BillIllustration.eyePose(clock+a.id*1.7),render:RENDER,goal:a.goal?{x:a.goal.x,y:a.goal.y,text:a.goal.text,element:a.goal.el.tagName}:null,steps:a.steps,targets:a.targets,trail:a.trail.map(p=>({...p,alpha:Math.max(0,1-(clock-p.time)/1.7)})),hands:a.hands.map((h,i)=>{const g=geometry(a,h,i);return {x:h.x,y:h.y,moving:h.moving,plant:h.plant,lift:h.lift,hip:g.start,knee:g.elbow,reach:g.reach,bones:[Math.hypot(g.elbow.x-g.start.x,g.elbow.y-g.start.y),Math.hypot(h.x-g.elbow.x,h.y-g.elbow.y)]}})}))})};requestAnimationFrame(tick);
})();
