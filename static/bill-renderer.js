/* Transparent, original vector illustration. Shared by observer and crawlers. */
(() => {
'use strict';
const palettes={watch:['#ffef69','#edbc30','#b48622'],investigate:['#ff5447','#df1d32','#671422'],warning:['#101018','#050509','#282536'],complete:['#8adfff','#247fb4','#12376c']};
function stroke(c,pts,color,width){c.beginPath();pts.forEach((p,i)=>i?c.lineTo(...p):c.moveTo(...p));c.strokeStyle=color;c.lineWidth=width;c.stroke()}
// Unequal fixations followed by quick saccades: autonomous on touch too.
const search=[[.00,0,0],[.72,-.9,-.35],[1.43,.78,.4],[2.39,.95,-.5],[2.96,-.5,.62],[4.02,-.94,.12],[4.66,.35,-.64],[5.73,.84,.22],[6.55,-.65,-.25],[7.35,0,0]];
function eyePose(clock,gaze=0){
 const t=((clock%8.3)+8.3)%8.3;let i=search.length-1;
 while(i>0&&t<search[i][0])i--;
 const cur=search[i],prev=search[(i+search.length-1)%search.length],q=Math.min(1,(t-cur[0])/.14),ease=q*q*(3-2*q);
 const x=prev[1]+(cur[1]-prev[1])*ease,y=prev[2]+(cur[2]-prev[2])*ease;
 const blink=t>5.15&&t<5.35?Math.max(.035,Math.abs(t-5.25)/.1):1;
 return {x:Math.max(-10,Math.min(10,x*9.5+gaze*1.3)),y:y*4.2,open:blink};
}
function almond(c){c.beginPath();c.moveTo(-17,0);c.quadraticCurveTo(0,-25,17,0);c.quadraticCurveTo(0,22,-17,0);c.closePath()}
function draw(c,{x,y,scale=1,form='watch',clock=0,lean=0,limbs=true,gaze=0}){
 const p=palettes[form]||palettes.watch,red=form==='investigate',dark=form==='warning',blue=form==='complete';
 c.save();c.translate(x,y);c.rotate(lean);c.scale(scale,scale);c.lineCap='round';c.lineJoin='round';
 if(limbs){
  const count=red?6:2;
  for(let i=0;i<count;i++){const side=i<count/2?-1:1,j=i%(count/2),sy=red?-12+j*19:9;
   const end=[side*(54+Math.sin(clock*1.7+i)*5),sy-14+Math.cos(clock*1.3+i*1.9)*12];
   const elbow=[side*(44+Math.sin(clock+i)*4),sy+25];
   stroke(c,[[side*20,sy],elbow,end],red?'#ffeb79':dark?'#24212c':'#070a14',red?3:4);
   for(let f=-1;f<=1;f++)stroke(c,[end,[end[0]+side*(4+f),end[1]-5-f*3]],red?'#fff29a':dark?'#393244':'#070a14',2);
   if(blue&&side<0){c.save();c.translate(...end);c.shadowColor='#36dcff';c.shadowBlur=18;for(let n=0;n<3;n++){const q=clock*3+n;c.beginPath();c.moveTo(-13+n*4,2);c.bezierCurveTo(-23,-14,-4+Math.sin(q)*6,-23,Math.sin(q)*7,-39-n*3);c.bezierCurveTo(3,-18,24,-11,12,3);c.closePath();c.fillStyle=['#1061ef','#28cbff','#a1ffff'][n];c.fill()}c.restore()}
  }
  for(let side of [-1,1])stroke(c,[[side*13,30],[side*(18+Math.sin(clock+side)*3),48],[side*23,52],[side*25,45]],dark?'#211d28':'#080b13',4);
 }
 c.shadowColor=dark?'#a687cf':blue?'#32cfff':red?'#ff6436':'#ffe656';c.shadowBlur=dark?7:13;
 c.beginPath();c.moveTo(0,-40);c.lineTo(44,31);c.quadraticCurveTo(0,36,-44,31);c.closePath();const g=c.createLinearGradient(-20,-35,20,35);g.addColorStop(0,p[0]);g.addColorStop(.6,p[1]);g.addColorStop(1,p[2]);c.fillStyle=g;c.fill();c.shadowBlur=0;c.strokeStyle=dark?'#605269':'#fae893';c.lineWidth=.8;c.stroke();
 c.save();c.clip();for(let yy=-19,row=0;yy<40;yy+=11,row++){stroke(c,[[-48,yy],[48,yy]],dark?'#111018':red?'#8f1522':blue?'#68bfd4':'#cdab2c',.8);for(let xx=-50+(row%2)*10;xx<50;xx+=20)stroke(c,[[xx,yy],[xx,yy+11]],dark?'#111018':red?'#8f1522':blue?'#68bfd4':'#cdab2c',.7)}c.restore();
 const eye=eyePose(clock,gaze),open=eye.open;
 c.save();c.translate(0,-6);c.scale(1,open);c.shadowColor=dark?'#ffff77':blue?'#2efbff':'transparent';c.shadowBlur=dark?18:blue?8:0;c.fillStyle=red?'#13090d':dark?'#ffff9d':blue?'#58f5ff':'#fffef0';almond(c);c.fill();c.shadowBlur=0;c.strokeStyle='#161019';c.lineWidth=1.3;c.stroke();c.save();almond(c);c.clip();c.fillStyle=red?'#fff37c':blue?'#efffff':'#101018';c.beginPath();c.ellipse(eye.x,eye.y,red?2.6:3.2,red?10:11,0,0,Math.PI*2);c.fill();c.restore();for(let i=-2;i<=2;i++)stroke(c,[[i*5,-13+Math.abs(i)*2],[i*6,-19+Math.abs(i)*2]],dark?'#403642':'#171017',1);c.restore();
 c.fillStyle=dark?'#322937':'#080a14';c.beginPath();c.moveTo(-11,17);c.lineTo(0,23);c.lineTo(11,17);c.lineTo(10,30);c.lineTo(0,25);c.lineTo(-11,30);c.closePath();c.fill();
 c.fillStyle='#070811';c.strokeStyle=red?'#fce783':blue?'#76b4d0':'#bda653';c.lineWidth=.8;c.fillRect(-8,-66,16,25);c.strokeRect(-8,-66,16,25);c.fillRect(-15,-43,30,4);c.strokeRect(-15,-43,30,4);c.fillStyle=red?'#e9c13f':blue?'#2b679f':'#554424';c.fillRect(-7,-47,14,3);c.restore();
}
window.BillIllustration={draw,stroke,eyePose,bodyHalfWidth:44};
})();
