(() => {
  'use strict';
  const root=document.documentElement,video=document.getElementById('world-video'),stage=document.getElementById('bill-stage'),pupil=document.getElementById('pupil'),eye=document.getElementById('eye-whole'),bill=document.getElementById('bill');
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)'),coarse=window.matchMedia('(pointer: coarse)');
  // The removed control's saved preference must not strand motion paused.
  try{localStorage.removeItem('fomobill-motion')}catch{}
  let moving=!reduced.matches,loaded=false,x=innerWidth/2,y=innerHeight*.35,gx=0,gy=0,lastPointer=0,lastTime=0,nextBlink=3.4,blinkStart=-10,frame=0;
  function applyMotion(){
    root.dataset.motion=moving?'active':'paused';
    if(moving){if(!loaded){video.src=video.dataset.src;loaded=true;video.load()}if(!document.hidden)video.play().catch(()=>{})}else video.pause();
  }
  video.addEventListener('canplay',()=>{root.dataset.videoReady='true';if(moving&&!document.hidden)video.play().catch(()=>{})});
  video.addEventListener('error',()=>{root.dataset.videoReady='false'});
  reduced.addEventListener('change',()=>{moving=!reduced.matches;applyMotion()});
  document.addEventListener('pointermove',e=>{x=e.clientX;y=e.clientY;lastPointer=performance.now()/1000},{passive:true});
  document.addEventListener('pointerdown',e=>{x=e.clientX;y=e.clientY;lastPointer=performance.now()/1000;if(stage.contains(e.target))blinkStart=lastPointer},{passive:true});
  function tick(ms){
    const t=ms/1000,dt=Math.min(.05,lastTime?t-lastTime:.016);lastTime=t;const box=stage.getBoundingClientRect(),cx=box.left+box.width/2,cy=box.top+box.height*.46;
    let dx=(x-cx)/Math.max(240,innerWidth*.38),dy=(y-cy)/Math.max(180,innerHeight*.35);
    if(coarse.matches&&t-lastPointer>4&&moving){dx=.65*Math.sin(t*.47);dy=.3*Math.sin(t*.39+.7)}
    const a=1-Math.exp(-dt*11);gx+=(Math.max(-1,Math.min(1,dx))*15-gx)*a;gy+=(Math.max(-1,Math.min(1,dy))*8-gy)*a;pupil.setAttribute('transform',`translate(${gx.toFixed(3)} ${gy.toFixed(3)})`);
    if(moving&&t>nextBlink){blinkStart=t;nextBlink=t+4.1+Math.sin(t*2.1)*1.3}
    const d=t-blinkStart,close=d>=0&&d<.21?Math.sin(Math.PI*d/.21):0;eye.style.transform=`scaleY(${(1-.96*close).toFixed(4)})`;
    if(!reduced.matches&&moving){bill.style.setProperty('--bill-rotation',`${(gx*.12).toFixed(2)}deg`);root.style.setProperty('--px',`${(-gx*.25).toFixed(2)}px`);root.style.setProperty('--py',`${(-gy*.24).toFixed(2)}px`)}
    frame=requestAnimationFrame(tick);
  }
  document.addEventListener('visibilitychange',()=>{if(document.hidden){video.pause();cancelAnimationFrame(frame)}else{lastTime=0;applyMotion();frame=requestAnimationFrame(tick)}});
  const dialog=document.getElementById('how-dialog'),opener=document.getElementById('how-button'),closer=document.getElementById('close-dialog');
  opener.addEventListener('click',()=>dialog.showModal());closer.addEventListener('click',()=>dialog.close());dialog.addEventListener('click',e=>{if(e.target===dialog){const b=dialog.getBoundingClientRect();if(e.clientX<b.left||e.clientX>b.right||e.clientY<b.top||e.clientY>b.bottom)dialog.close()}});dialog.addEventListener('close',()=>opener.focus());
  applyMotion();frame=requestAnimationFrame(tick);
})();
