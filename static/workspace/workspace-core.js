/* Real data only. localStorage is a local bookmark list, not a monitor. */
'use strict';
const $=id=>document.getElementById(id);
const KEY='fomobill-live-watchlist-v1',MAX_WATCH=50;
const alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
let watch=[],storageOK=true,busy=false,current=null;
function validAddress(value){
 if(typeof value!=='string'||!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(value))return false;
 let n=0n;for(const c of value)n=n*58n+BigInt(alphabet.indexOf(c));
 let bytes=0;while(n){bytes++;n>>=8n;}return bytes+(value.match(/^1*/)||[''])[0].length===32;
}
function el(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
function link(label,url){const a=el('a',label);try{const parsed=new URL(url);if(parsed.protocol!=='https:')return el('span',label);a.href=parsed.href;}catch{return el('span',label);}a.target='_blank';a.rel='noopener noreferrer';return a;}
function say(message){$('statusText').textContent=message;$('feedback').hidden=false;}
function short(s){return s.slice(0,8)+'…'+s.slice(-6);}
function money(n){if(n===null||n===undefined)return 'Unavailable';const num=Number(n);if(!Number.isFinite(num))return 'Unavailable';return '$'+num.toLocaleString('en-US',{maximumSignificantDigits:8});}
function date(value){if(value===null||value===undefined)return 'Time unavailable';const d=new Date(value);return Number.isNaN(d.getTime())?'Time unavailable':d.toISOString().replace('T',' ').replace('.000Z',' UTC');}
function persist(){
 try{localStorage.setItem(KEY,JSON.stringify(watch));storageOK=true;}
 catch(_){storageOK=false;}
 renderWatch();
 return storageOK;
}
function renderWatch(){
 $('watchCount').textContent=watch.length+' / '+MAX_WATCH;$('watchlist').replaceChildren();
 $('storageNote').textContent=storageOK?'No sync or background notifications. Clearing browser data deletes the list.':'Storage unavailable. This list lasts only until the page is closed.';
 if(!watch.length)$('watchlist').append(el('p','The list is empty. Check an address and save it.'));
 for(const item of watch){
  const row=el('div',undefined,'watch-item'),open=el('button',(item.kind==='token'?'TOKEN':'ACCOUNT')+' · '+short(item.address),'watch-open');
  open.type='button';open.title=item.address;open.disabled=busy;open.addEventListener('click',()=>{$('kind').value=item.kind;$('address').value=item.address;lookup();});
  const remove=el('button','×','watch-remove');remove.type='button';remove.setAttribute('aria-label','Remove '+item.address);remove.addEventListener('click',()=>{watch=watch.filter(x=>x!==item);persist();say('Address removed from the local list.');updateSave();});
  row.append(open,remove);$('watchlist').append(row);
 }
}
function isSaved(){return current&&watch.some(x=>x.kind===current.kind&&x.address===current.address);}
function updateSave(){const button=$('saveButton');if(!button)return;button.textContent=isSaved()?'✓ In local list':'+ Save address';button.disabled=busy||isSaved();}
try{
 const raw=localStorage.getItem(KEY)||'[]';if(raw.length>20000)throw Error('Too large');
 const items=JSON.parse(raw);if(!Array.isArray(items))throw Error('Invalid list');
 const seen=new Set();watch=items.filter(x=>x&&['token','wallet'].includes(x.kind)&&validAddress(x.address)&&!seen.has(x.kind+x.address)&&seen.add(x.kind+x.address)).slice(0,MAX_WATCH).map(x=>({kind:x.kind,address:x.address}));
}catch(_){storageOK=false;}
renderWatch();
function sourceError(errors,container){for(const e of errors||[])container.append(el('p',e.source+' — '+e.message,'error-note'));}
function appendPreview(root,container,label){
 const rows=[...container.children];
 if(rows.length<=3){root.append(container);return;}
 const rest=el('div',undefined,container.className),details=el('details',undefined,'more-results');
 for(const row of rows.slice(3))rest.append(row);
 details.append(el('summary','Show more: '+(rows.length-3)+' '+label+' · total '+rows.length),rest);
 root.append(container,details);
}
function render(data){
 current=data;
 const root=$('resultContent');root.replaceChildren();root.dataset.result=data.status;
 const heading=el('h2',data.kind==='token'?'Market pairs':'Account activity records','result-heading');heading.id='detailTitle';root.append(heading,el('div',data.address,'address'));
 const actions=el('div',undefined,'result-actions');
 const save=el('button','+ Save address');save.id='saveButton';save.type='button';save.addEventListener('click',()=>{
  if(isSaved())return;if(watch.length>=MAX_WATCH){say('Limit: 50 addresses. Remove one from the list.');return;}
  watch.push({kind:data.kind,address:data.address});const stored=persist();updateSave();say(stored?'Address saved in this browser. Background monitoring has not started.':'Address added for this session: storage unavailable.');
 });
 actions.append(save,link('Address on Solscan ↗','https://solscan.io/account/'+data.address));
 if(data.kind==='wallet'){const observe=el('button','Monitor on server…');observe.id='observeButton';observe.type='button';observe.addEventListener('click',()=>{$('monitorAddress').value=data.address;$('monitorConsent').checked=false;window.FomoUI?.navigate('wallets',false);$('monitorAddress').focus();});actions.append(observe);}
 const refresh=el('button','Retry request');refresh.type='button';refresh.addEventListener('click',()=>{$('kind').value=data.kind;$('address').value=data.address;lookup();});actions.append(refresh);root.append(actions);
 const time=el('p',(FomoData.preview?'Saved snapshot: ':'Retrieved: ')+date(data.observedAt)+' · '+data.provider+(data.cache?.hit?' · cached':' · new request'),'source-line');root.append(time);
 const note=data.kind==='token'?'Pairs where the entered address is the base token. At most 12, ranked by liquidity. Provider USD estimates, not wallet trades.':'Up to 20 recent finalized records referencing the exact address. Not a complete history; token accounts are not expanded. Buys, sells and account ownership are not determined.';
 const coverage=el('p',note,'coverage-line');coverage.id='detailText';root.append(coverage);
 if(data.source)root.append(link('Source: '+data.source,data.source));
 sourceError(data.errors,root);
 if(data.status==='unavailable')root.append(el('p','Source unavailable. No substitute data. Try again later.','empty'));
 else if(data.status==='empty')root.append(el('p',data.kind==='token'?'DexScreener returned no matching Solana pairs. This does not prove the token does not exist.':'RPC returned an empty page. This does not prove there was no past activity or that the wallet exists.','empty'));
 else if(data.kind==='token'){
  root.append(el('p','Pairs in this snapshot: '+data.pairs.length,'snapshot'));
  const cards=el('div',undefined,'markets');
  for(const p of data.pairs){
   const card=el('article',undefined,'market-card');card.append(el('h3',(p.symbol||'Unknown')+' / '+(p.quoteSymbol||'Unknown')),el('div',(p.name||'Name unavailable')+' · '+(p.dex||'Unknown DEX'),'source-line'));
   const dl=el('dl');for(const [title,value] of [['Base token price, USD',p.priceUsd],['Pair liquidity, USD',p.liquidityUsd],['24h pair volume, USD',p.volume24hUsd]])dl.append(el('dt',title),el('dd',money(value)));
   card.append(dl,link('Pair on DexScreener ↗',p.url),el('p',p.pairAddress,'address'));
   if(p.context?.links?.length){card.append(el('p','Links are provided by DexScreener; accounts are not verified. X content has not been read.','coverage-line'));for(const ref of p.context.links)card.append(link(ref.kind+' ↗',ref.url));}
   cards.append(card);
  }
  appendPreview(root,cards,'pairs');
 }else{
  root.append(el('p','Records in this snapshot: '+data.activity.length+' · trades not decoded','snapshot'));
  const rows=el('div',undefined,'transactions');
  for(const t of data.activity){
   const row=el('article',undefined,'transaction-row');const first=el('div');first.append(link(short(t.signature),t.url));
   const time=el('div',date(t.blockTime===null?null:t.blockTime*1000));time.append(el('small','Block time · UTC'));
   const state=el('div',t.status==='success'?'Successful transaction':'Failed transaction',t.status==='success'?'yellow':'danger');state.append(el('small','Slot '+t.slot+' · '+t.confirmation));row.append(first,time,state);rows.append(row);
  }
  appendPreview(root,rows,'records');
 }
 const detail=el('details');detail.append(el('summary','Technical sample boundaries'));detail.append(el('p',data.coverage,'coverage-line'));if(data.cursor)detail.append(el('p','Oldest signature on this page: '+data.cursor,'address'));root.append(detail);
 updateSave();age();
 billState(data.status==='unavailable'?'error':data.status==='ok'?'evidence':'finished');
}
function age(){
 if(!current)return;
 const seconds=Math.max(0,Math.floor((Date.now()-Date.parse(current.observedAt))/1000));
 $('resultAge').textContent=FomoData.preview?'SAVED EXAMPLE':Number.isFinite(seconds)?'SNAPSHOT '+seconds+' SEC AGO'+(seconds>60?' · REFRESH ON REQUEST':''):'TIME UNAVAILABLE';
}
setInterval(age,1000);
async function lookup(){
 if(busy)return;
 const address=$('address').value,kind=$('kind').value;
 if(!validAddress(address)){say('An exact Solana base58 address (32 bytes) is required, without spaces. EVM / 0x is not supported.');return;}
 busy=true;$('searchButton').disabled=true;$('exampleButton').disabled=true;renderWatch();updateSave();
 current=null;$('resultContent').replaceChildren(el('p','Requesting public source…','loading'));delete $('resultContent').dataset.result;$('resultAge').textContent='REQUESTING';
 billState('loading');say('Requesting '+(kind==='token'?'DexScreener':'Solana RPC')+'…');
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
 try{
  const response=await FomoData.apiFetch('/api/'+kind+'?address='+encodeURIComponent(address),{signal:controller.signal,headers:{'Accept':'application/json'}});
  const data=await response.json();if(!response.ok)throw Error(data.error||'HTTP '+response.status);
  if(data.address!==address||data.kind!==kind||!['ok','empty','unavailable'].includes(data.status))throw Error('Response does not match the requested address.');
  render(data);say(data.status==='ok'?'Source snapshot received. This is not a trading signal.':data.status==='empty'?'Source responded, but no matching records were found.':'Source unavailable. Please try again later.');
  window.FomoUI?.navigate('lookup',false);$('results').scrollIntoView({block:'start',behavior:'smooth'});
 }catch(error){
  billState('error');current=null;$('resultAge').textContent='NO DATA';$('resultContent').replaceChildren(el('p',error.name==='AbortError'?'Source did not respond within 20 seconds. Try again later.':'Request failed: '+error.message,'error-note'));$('resultContent').dataset.result='unavailable';say('Could not retrieve data. Check your connection or try again later.');
 }finally{clearTimeout(timer);busy=false;$('searchButton').disabled=false;$('exampleButton').disabled=false;renderWatch();updateSave();}
}
function billState(state){
 const states={loading:['investigate','Requesting source.','Waiting for a real response.'],active:['watch','Monitoring enabled.','The server collects a bounded sample.'],evidence:['investigate','Source data available.','Open the links and check the sample boundaries.'],error:['warning','There is a data gap.','An error or unavailable source does not mean no activity.'],finished:['complete','Request complete.','No new evidence in this snapshot.']};
 const [form,title,subtitle]=states[state];$('stateTitle').textContent=title;$('stateSubtitle').textContent=subtitle;$('billArt').dataset.state=state;
 dispatchEvent(new CustomEvent('bill-state',{detail:form}));
}
let monitorSnapshot=null,monitorBusy=false,monitorTimer=null,monitorDelay=15000,identityReady=false,participantRequest=0,participantRenderKey='',monitorUpdated=0,participantSaleScope=null;
async function privateAPI(path,body){
 const control=new AbortController(),timeout=setTimeout(()=>control.abort(),12000);
 try{const response=await FomoData.apiFetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',signal:control.signal,headers:{'Accept':'application/json',...(body===undefined?{}:{'Content-Type':'application/json','X-Fomobill':'1'})},...(body===undefined?{}:{body:JSON.stringify(body)})});
 const data=await response.json();if(!response.ok)throw Error(data.error||'HTTP '+response.status);return data;
 }finally{clearTimeout(timeout);}
}
function chainDate(t){return date(t===null||t===undefined?null:t*1000);}
function receiptLink(t){return link('Transaction ↗','https://solscan.io/tx/'+t.signature);}
function inspectTokenButton(mint){
 const button=el('button','Token holders ↗','inspect-token');button.type='button';button.title=mint;
 button.addEventListener('click',()=>{
  if(busy){say('Wait for the current request, then open the token market.');return;}
  window.FomoUI?.navigate('tokens',false);$('tokenAddress').value=mint;discoverToken();$('discover').scrollIntoView({block:'start'});
 });
 return button;
}
function tradeCard(t){
 const card=el('article',undefined,'evidence-card');card.dataset.evidence=t.id;card.dataset.side=t.side;
 card.append(el('span',t.side==='buy'?'OBSERVED BUY':'OBSERVED SALE','event-label '+(t.side==='buy'?'buy-label':'sell-label')),el('h3',t.side==='buy'?'A purchase was observed.':'A sale was observed.'),el('p',chainDate(t.blockTime),'source-line'),el('p','Wallet: '+t.wallet,'address'),link('Token '+short(t.mint)+' ↗','https://solscan.io/token/'+t.mint),receiptLink(t));
 const detail=el('details',undefined,'technical');detail.append(el('summary','Amounts and verification details'),el('p','Raw token amount: '+t.tokenAmountRaw+' · raw quote amount: '+t.quoteAmountRaw,'address'),el('p','First observed: '+chainDate(t.observedAt),'source-line'),el('p',t.qualifiesForGroup?'Signer matches event user.':'Attribution is not eligible for groups.','coverage-line'));
 const raw=el('button','Open saved RPC receipt');raw.type='button';raw.addEventListener('click',async()=>{raw.disabled=true;try{const d=await privateAPI('/api/monitor/evidence?signature='+encodeURIComponent(t.signature));detail.append(el('p','SHA256: '+d.sha256,'address'),el('pre',d.rawResponse));}catch(e){detail.append(el('p',e.message,'error-note'));}finally{raw.disabled=false;}});detail.append(raw);card.append(detail,inspectTokenButton(t.mint));return card;
}
// Snapshot interpretation — pure, derived only from the saved producer DTO.
function watchState(s,w,now){
 const cursor=s.coverage.cursors.find(x=>x.address===w.address),coverage=cursor?.coverage;
 const checkedAt=Number(coverage?.checkedAt)||null;
 const rows=s.trades.filter(t=>t.wallet===w.address),times=rows.map(t=>t.blockTime).filter(t=>Number.isFinite(t));
 const result={checkedAt,latestEventAt:times.length?Math.max(...times):null,count:rows.length};
 let state,title,body;
 if(s.coverage.paused){state='paused';title='Collection paused';body='The operator paused collection. Saved evidence remains available.';}
 else if(!s.runtime.running){state='stopped';title='Collection not running';body='The worker is off. Refreshing the page does not start collection.';}
 else if(s.runtime.error||coverage?.status==='gap'){state='gap';title='There is a data gap';body='RPC or decoding is incomplete. This does not mean no trades; the server will retry.';}
 else if(!cursor||coverage?.status==='not_yet_sampled'){state='waiting';title='Waiting for first check';body='This address has not been checked yet. The server processes the queue in bounded passes; there is no exact ETA. You can close the page.';}
 else if(!checkedAt||!['sampled','pending'].includes(coverage?.status)){state='unknown';title='Coverage unknown';body='No confirmed check status. No conclusion about the absence of trades can be drawn.';}
 else if(now-checkedAt>1200){state='stale';title='Check is stale';body='This address has not been checked for over 20 minutes. Refreshing the page does not make the saved events below fresh.';}
 else if(coverage.status==='pending'){state='pending';title='Sample still processing';body='Signatures or pages remain in the queue. Saved events are available, but the result is incomplete.';}
 else if(rows.length){state='result';title='Saved trades available';body='Decoded in the current sample: '+rows.length+'. Open a participant, then the exact transaction.';}
 else{state='empty';title='Checked: no matching trades yet';body='No supported Pump trades found in the processed sample. This does not mean no activity: ATAs, AMMs and complete history are not covered.';}
 return {...result,state,title,body};
}
function groupStory(g,trades){
 const later=(g.progression||[]).filter(t=>t.mint===g.mint&&g.participants.includes(t.wallet)&&Number.isFinite(t.blockTime)&&t.blockTime>g.triggerTime).sort((a,b)=>a.blockTime-b.blockTime||a.id.localeCompare(b.id));
 const buys=trades.filter(t=>g.evidenceIds.includes(t.id)&&t.mint===g.mint&&t.side==='buy'&&g.participants.includes(t.wallet));
 const buyers=g.participants.map(wallet=>({wallet,buys:buys.filter(t=>t.wallet===wallet),sales:later.filter(t=>t.wallet===wallet&&t.side==='sell')}));
 return {later,buyers,sales:later.filter(t=>t.side==='sell').length,sellers:buyers.filter(p=>p.sales.length).length};
}
function participantSales(snapshot,scope){
 const group=snapshot?.groups.find(g=>g.id===scope.groupId&&g.participants.includes(scope.wallet));
 if(!group)return null;
 return {mint:group.mint,triggerTime:group.triggerTime,rows:groupStory(group,snapshot.trades).buyers.find(p=>p.wallet===scope.wallet).sales};
}
// Monitoring DOM
function updateMonitorFreshness(){
 if(!monitorSnapshot)return;
 for(const row of $('monitorWatches').children){
  const w=monitorSnapshot.watches.find(w=>w.address===row.dataset.address);if(!w)continue;
  const status=watchState(monitorSnapshot,w,Date.now()/1000),node=row.querySelector('.watch-state');node.dataset.watchState=status.state;
  node.querySelector('strong').textContent=status.title;node.querySelector('p').textContent=status.body;
  row.querySelector('.watch-freshness').textContent=(status.checkedAt?'Last check attempt: '+chainDate(status.checkedAt)+' · '+Math.max(0,Math.floor(Date.now()/1000-status.checkedAt))+' sec ago':'No check attempts yet')+' · Latest saved trade (block): '+chainDate(status.latestEventAt);
 }
}
function renderMonitor(s){
 $('monitorEmpty').hidden=!!s.watches.length;$('observationsPanel').hidden=!s.watches.length;monitorSnapshot=s;updateHolderSelection();const c=s.coverage,selected=$('participantFilter').value,filter=$('participantFilter');
 filter.replaceChildren(el('option','All my monitored addresses'));filter.firstChild.value='';
 for(const w of s.watches){const option=el('option',short(w.address));option.value=w.address;filter.append(option);}filter.value=s.watches.some(w=>w.address===selected)?selected:'';
 const root=$('monitorWatches');root.replaceChildren();
 for(const w of s.watches){const row=el('article',undefined,'watch-observation');row.dataset.address=w.address;
 const state=el('div',undefined,'watch-state');state.append(el('strong'),el('p'));
 row.append(el('p',w.address,'address'),state,el('p',undefined,'watch-freshness source-line'),el('p','Monitoring until '+chainDate(w.expires),'source-line'));
 const select=el('button','Participant trades');select.type='button';select.addEventListener('click',()=>{participantSaleScope=null;filter.value=w.address;renderParticipant();$('participantEvidence').scrollIntoView({block:'start'});});
 const stop=el('button','Stop / remove');stop.type='button';stop.addEventListener('click',()=>changeWatch('unwatch',w.address));row.append(select,stop);root.append(row);}
 updateMonitorFreshness();
 const gaps=c.cursors.some(x=>x.coverage?.status==='gap'),active=!!s.watches.length;
 $('monitorStatus').textContent=!active?'The server list is empty. Bookmarks do not enable monitoring.':c.paused?'Monitor paused by the operator.':!s.runtime.running?'Worker is off. Only the saved snapshot is shown.':gaps?'RPC/decoding gaps exist. Saved data is shown below.':'Monitoring active: '+s.watches.length+' / 8. This page reads the snapshot every 15 seconds; the server makes a bounded pass once per minute.';
 const cards=$('monitorEvidence'),retained=new Map([...cards.querySelectorAll('.group-card')].map(node=>[node.dataset.group,node]));cards.replaceChildren();
 cards.append(el('h3','Co-buys → what happened next'));
 if(!s.groups.length)cards.append(el('p',active?'No group of two monitored buyers of the same token within 300 seconds yet. At least two addresses in your list are needed for comparison; no group does not mean no connections.':'Add an address with consent above. Co-buys by monitored participants and their subsequent actions will appear here.','empty'));
 for(const g of s.groups){
 const story=groupStory(g,s.trades),key=JSON.stringify([g,story]),previous=retained.get(g.id);
 if(previous?.dataset.renderKey===key){cards.append(previous);continue;}
 const card=el('article',undefined,'evidence-card group-card');card.dataset.group=g.id;card.dataset.renderKey=key;
 card.append(el('h3',g.participants.length+' participants bought the same token within '+g.windowSeconds+' sec.'),el('p',g.mint,'address'),el('p','Group formed: '+chainDate(g.triggerTime),'source-line'));
 card.append(el('p',story.sales?'Later sales: '+story.sales+' by '+story.sellers+' of '+g.participants.length+' participants in the displayed events.':'No sales found in the displayed later events. This does not mean the participants still hold the token.','group-story'));
 for(const p of story.buyers){
 const row=el('div',undefined,'group-participant'),button=el('button',short(p.wallet)+' · all participant trades');button.type='button';button.title=p.wallet;button.addEventListener('click',()=>{participantSaleScope=null;filter.value=p.wallet;renderParticipant();$('participantEvidence').scrollIntoView({block:'start'});});
 const sales=el('button','Later sales · '+p.sales.length,'inspect-sales');sales.type='button';sales.setAttribute('aria-label','View later sales for '+p.wallet+' in this group');sales.addEventListener('click',()=>{participantSaleScope={groupId:g.id,wallet:p.wallet};filter.value=p.wallet;renderParticipant();$('participantEvidence').scrollIntoView({block:'start'});});
 row.append(button,sales,el('p','Buys in group: '+p.buys.length+' → '+(p.sales.length?'later observed sales: '+p.sales.length:'no later sales found'),'source-line'));card.append(row);
 }
 card.append(el('p','Matching token and time, not proof of coordination. A sale does not mean a complete exit; balances and PnL are unknown. Later events: up to 50 in the saved sample; other protocols and gaps may hide actions.','coverage-line'),inspectTokenButton(g.mint));
 const receipts=el('details',undefined,'group-receipts');receipts.append(el('summary','Verify buys and subsequent actions · exact transactions'));
 receipts.append(el('h4','1 · Buys that formed the group'));
 for(const p of story.buyers)for(const t of p.buys)receipts.append(tradeCard(t));
 receipts.append(el('h4','2 · After the group formed · same token'));
 if(!story.later.length)receipts.append(el('p','No later actions in the saved sample. Coverage is limited.','empty'));
 for(const t of story.later)receipts.append(tradeCard(t));card.append(receipts);cards.append(card);
 }
 cards.append(el('h3','Shared observed SOL source · '+s.commonSources.length));
 if(!s.commonSources.length)cards.append(el('p','No matching shared sources in this sample.','empty'));
 const transferById=new Map(s.transfers.map(t=>[t.id,t]));
 for(const g of s.commonSources){const card=el('article',undefined,'evidence-card');card.append(el('h3','SOL transfers within a window of '+g.windowSeconds+' sec.'),el('p',g.source,'address'),el('p','Not common ownership or proof of coordination.','coverage-line'));for(const a of g.destinations)card.append(link(short(a),'https://solscan.io/account/'+a));for(const id of g.evidenceIds){const t=transferById.get(id);if(t)card.append(receiptLink(t));}cards.append(card);}
 $('monitorCoverage').textContent=JSON.stringify(c,null,2);renderParticipant();
 if(!busy)billState(gaps||s.runtime.error?'error':s.trades.length?'evidence':active&&s.runtime.running&&!c.paused?'active':'finished');
}
async function renderParticipant(){
 const seq=++participantRequest,addr=$('participantFilter').value,root=$('participantEvidence');
 if(participantSaleScope){
  const scoped=participantSales(monitorSnapshot,participantSaleScope),key=JSON.stringify([participantSaleScope,scoped]);
  if(key===participantRenderKey)return;participantRenderKey=key;
  const heading=el('h3','Later observed sales · '+(scoped?scoped.rows.length:'unavailable'));heading.dataset.saleScope='true';
  const clear=el('button','Show all participant trades','clear-sale-scope');clear.type='button';clear.addEventListener('click',()=>{participantSaleScope=null;renderParticipant();});
  root.replaceChildren(heading,el('p','Participant: '+participantSaleScope.wallet,'address'),clear);
  if(!scoped){root.append(el('p','This group is no longer in the current snapshot. Its sales cannot be reconstructed here.','empty'));return;}
  root.append(el('p','Token: '+scoped.mint,'address'),el('p','After group formed: '+chainDate(scoped.triggerTime),'source-line'),el('p','Only this participant, this token, and sales after this group formed. Up to 50 retained group events; not a complete exit, balance or PnL.','coverage-line'));
  if(!scoped.rows.length)root.append(el('p','No later sales found in the retained group events. This does not mean the participant still holds the token.','empty'));
  for(const t of [...scoped.rows].reverse())root.append(tradeCard(t));return;
 }
 try{const rows=addr?(await privateAPI('/api/monitor/wallet?address='+encodeURIComponent(addr))).trades:(monitorSnapshot?.trades||[]).slice(-50);
 if(seq!==participantRequest)return;const key=JSON.stringify([addr,rows]);if(key===participantRenderKey)return;participantRenderKey=key;root.replaceChildren(el('h3','Saved Pump trades · '+rows.length+' / up to 50'));
 if(!rows.length)root.append(el('p','No decoded trades in this sample. Other protocols and ATAs are not covered.','empty'));
 for(const t of [...rows].reverse())root.append(tradeCard(t));dispatchEvent(new CustomEvent('bill-state',{detail:$('billArt').dataset.form}));
 }catch(e){if(seq===participantRequest){participantRenderKey='';root.replaceChildren(el('p',e.message,'error-note'));}}
}
async function refreshMonitor(){
 clearTimeout(monitorTimer);if(FomoData.preview){$('monitorStatus').textContent='Your live watchlist is not connected to this preview.';$('monitorAge').textContent='PREVIEW';$('telegramStatus').textContent='Connect Telegram in your live workspace. The setup below shows the steps.';$('telegramPair').disabled=true;return;}if(monitorBusy||document.hidden)return;monitorBusy=true;
 try{if(!identityReady){await privateAPI('/api/session',{});identityReady=true;}
 const s=await privateAPI('/api/monitor');renderMonitor(s);monitorUpdated=Date.now();monitorDelay=15000;
 try { const telegram=await privateAPI('/api/telegram');$('telegramStatus').textContent=telegram.availability==='ready'?(telegram.active?'Notifications enabled.':telegram.paired?'Bot paired. Send /subscribe to receive messages.':'Bot ready; pair it with separate consent.'):'Telegram unavailable: '+telegram.availability;$('telegramPair').disabled=telegram.availability!=='ready';$('telegramStop').hidden=!telegram.active;$('telegramBadge').textContent=telegram.active?'Alerts enabled':telegram.paired?'Paired':telegram.availability==='ready'?'Ready to connect':'Unavailable'; } catch (telegramError) { $('telegramStatus').textContent='Could not check Telegram: '+telegramError.message;$('telegramBadge').textContent='Status unknown';$('telegramPair').disabled=true; }
 }catch(e){$('monitorStatus').textContent='Snapshot not refreshed: '+e.message;monitorDelay=Math.min(120000,monitorDelay*2);if(!busy)billState('error');}
 finally{monitorBusy=false;monitorTimer=setTimeout(refreshMonitor,monitorDelay);}
}
async function changeWatch(action,address){
 if(!validAddress(address)){say('An exact Solana address without spaces is required.');return;}
 $('monitorStart').disabled=true;billState('loading');
 try{if(!identityReady){await privateAPI('/api/session',{});identityReady=true;}await privateAPI('/api/monitor/'+action,{address});$('monitorConsent').checked=false;await refreshMonitor();say(action==='watch'?'Monitoring enabled / renewed for 7 days.':'Monitoring stopped. Local bookmark retained.');}
 catch(e){$('monitorStatus').textContent=e.message;billState('error');}finally{$('monitorStart').disabled=false;}
}
$('monitorForm').addEventListener('submit',e=>{e.preventDefault();if($('monitorConsent').checked)changeWatch('watch',$('monitorAddress').value);});
$('participantFilter').addEventListener('change',()=>{participantSaleScope=null;renderParticipant();});
$('monitorRefresh').addEventListener('click',refreshMonitor);
$('telegramPair').addEventListener('click',async()=>{try{if(!confirm('Create a secret link to pair this browser? Do not share it. Messages are enabled separately with /subscribe.'))return;const d=await privateAPI('/api/telegram/pair',{});$('telegramLink').replaceChildren(link('Open dedicated bot · link valid for 10 minutes',d.url));setTimeout(()=>$('telegramLink').replaceChildren(),600000);}catch(e){$('telegramStatus').textContent=e.message;}});
$('telegramStop').addEventListener('click',async()=>{try{await privateAPI('/api/telegram/stop',{});await refreshMonitor();}catch(e){$('telegramStatus').textContent=e.message;}});
setInterval(()=>{updateMonitorFreshness();if(monitorUpdated){const age=Math.floor((Date.now()-monitorUpdated)/1000);$('monitorAge').textContent='BROWSER SNAPSHOT '+age+' SEC AGO'+(age>45?' · GETTING STALE':'');}},1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshMonitor();else clearTimeout(monitorTimer);});
refreshMonitor();
$('searchForm').addEventListener('submit',e=>{e.preventDefault();lookup();});
$('exampleButton').addEventListener('click',()=>{$('kind').value='wallet';$('address').value=FomoData.sampleWallet;lookup();});

// Token discovery adapter: the only boundary for the holders.v1 backend.
// GET requests never enroll wallets. All enrollment uses privateAPI + explicit consent.
const holderSelection=new Set();
let holderMint=null,holderLoading=false,followBusy=false,drawerRequest=0;
const holderAPI={
 async read(path){
  const control=new AbortController(),timer=setTimeout(()=>control.abort(),30000);
  try{const r=await FomoData.apiFetch(path,{credentials:'same-origin',headers:{Accept:'application/json'},signal:control.signal});
   let d;try{d=await r.json();}catch(_){throw Error('Source returned non-JSON data (HTTP '+r.status+').');}
   if(!r.ok)throw Error(d.error||'HTTP '+r.status);return d;
  }finally{clearTimeout(timer);}
 },
 async holders(mint){
  const d=await this.read('/api/holders?address='+encodeURIComponent(mint));
  if((d.mint??d.address)!==mint||!['ok','partial','empty','unavailable'].includes(d.status)||!Array.isArray(d.holders))throw Error('Holder response does not match the token.');
  const seen=new Set();
  const rows=d.holders.map(h=>({...h,address:h.owner??h.address}));
  if(rows.some(h=>!validAddress(h.address)||seen.has(h.address)||!seen.add(h.address)))throw Error('Invalid or duplicate holder address.');
  return {...d,holders:rows};
 },
 async trades(wallet,mint,cursor=null){
  const d=await this.read('/api/holder-trades?address='+encodeURIComponent(wallet)+'&mint='+encodeURIComponent(mint)+(cursor===null?'':'&cursor='+encodeURIComponent(cursor)));
  if((d.wallet??d.address)!==wallet||d.mint!==mint||!['ok','partial','empty','unavailable'].includes(d.status)||!Array.isArray(d.trades))throw Error('Trade response does not match the wallet and token.');
  if(d.trades.some(t=>typeof t.id!=='string'||!t.id||t.wallet!==wallet||t.mint!==mint||!['buy','sell'].includes(t.side)))throw Error('Response contains trades for another participant or token.');
  return d;
 }
};
function coverageText(value){
 if(typeof value==='string')return value;
 if(!value||typeof value!=='object')return '';
 return Object.entries(value).filter(([,v])=>typeof v!=='object').map(([k,v])=>k+': '+String(v)).join(' · ');
}
function holderNumber(value,suffix=''){
 if(value===null||value===undefined||value===''||!/^\d+(\.\d+)?$/.test(String(value)))return '—';
 return String(value)+suffix; // Preserve decimal precision; do not invent prices or round large balances.
}
function holderEmpty(message){const row=el('tr'),cell=el('td');cell.colSpan=5;cell.append(el('div',message,'empty-state'));row.append(cell);$('holdersBody').replaceChildren(row);}
function updateHolderSelection(){
 const known=monitorSnapshot?.watches||[],count=holderSelection.size;
 $('selectedCount').textContent=count;
 $('followingCount').replaceChildren(document.createTextNode(monitorSnapshot?String(known.length)+' ':'— '),el('em','/ 8'));
 $('followBar').hidden=!count;
 if(count&&!monitorSnapshot)$('followStatus').textContent=FomoData.preview?'Start watching these wallets in your live workspace. This preview does not change your watchlist.':'Your watchlist is loading. Refresh it on the Wallets page before adding addresses.';
 $('followSelected').disabled=followBusy||!count||!$('holderConsent').checked||!monitorSnapshot;
 for(const box of document.querySelectorAll('.holder-select')){
  const followed=known.some(w=>w.address===box.value);
  box.disabled=followBusy||followed||(!box.checked&&known.length+count>=8);
  box.title=followed?'Already in your list':known.length+count>=8&&!box.checked?'Limit: 8 addresses per browser':'';
 }
}
let loadedHolders=[];
function compareBalance(a,b,direction){
 const valid=v=>v!==null&&v!==undefined&&/^\d+(\.\d+)?$/.test(String(v));
 if(!valid(a))return valid(b)?1:0;if(!valid(b))return -1;
 const [ai,af='']=String(a).split('.'),[bi,bf='']=String(b).split('.'),places=Math.max(af.length,bf.length);
 const x=BigInt(ai+af.padEnd(places,'0')),y=BigInt(bi+bf.padEnd(places,'0'));
 return (x<y?-1:x>y?1:0)*direction;
}
// Charts use only the validated, already-loaded sample. BigInt keeps decimal
// denominators exact; floating point is reserved for bounded SVG coordinates.
function chartDecimalSet(values){
 const valid=v=>v!==null&&v!==undefined&&/^\d+(\.\d+)?$/.test(String(v));
 const places=Math.max(0,...values.filter(valid).map(v=>(String(v).split('.')[1]||'').length));
 const scaled=values.map(v=>{if(!valid(v))return null;const [i,f='']=String(v).split('.');return BigInt(i+f.padEnd(places,'0'));});
 return {places,scaled,total:scaled.reduce((sum,v)=>sum+(v??0n),0n)};
}
function chartExact(value,places){const s=String(value).padStart(places+1,'0');return places?s.slice(0,-places)+'.'+s.slice(-places):s;}
function chartSVG(tag,attrs){const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));return node;}
function chartProvenance(data){
 let source=data?.source||'Source unspecified';try{source=new URL(source).hostname;}catch(_){}
 const strip=el('p',undefined,'chart-provenance');strip.title=data?.source||'Source unspecified';strip.append(el('span','SOURCE / '+source),el('span','RETRIEVED / '+(data?.observedAt?date(data.observedAt):'unknown')));return strip;
}
function renderHolderVisual(data){
 $('holderVisual')?.remove();
 if(data.status==='unavailable'||!data.holders.length)return;
 const amounts=chartDecimalSet(data.holders.map(h=>h.amount));
 const known=data.holders.map((h,i)=>({...h,value:amounts.scaled[i]})).filter(h=>h.value!==null).sort((a,b)=>a.value>b.value?-1:a.value<b.value?1:0);
 if(!amounts.total)return;
 const box=el('section',undefined,'sample-visual');box.id='holderVisual';box.setAttribute('aria-label','Loaded holder balance distribution');
 box.dataset.figures=JSON.stringify({totalScaled:String(amounts.total),places:amounts.places,known:known.length,loaded:data.holders.length});
 const head=el('div',undefined,'visual-heading');head.append(el('span','01 / SAMPLE DISTRIBUTION','terminal-label'),el('span',data.status==='partial'?'PARTIAL':'LOADED','terminal-state'));
 box.append(head,el('h3','Where the loaded balance sits'),chartProvenance(data),el('p',known.length+' known balances / '+data.holders.length+' loaded wallets. Percentages below use this loaded sample, not token supply.','visual-caption'));
 const items=known.slice(0,5).map(h=>({label:short(h.address),value:h.value,address:h.address}));
 if(known.length>5)items.push({label:'Other '+(known.length-5),value:known.slice(5).reduce((s,h)=>s+h.value,0n)});
 const bars=el('div',undefined,'balance-bars');
 for(const item of items){
  const pct=Number(item.value*10000n/amounts.total)/100;
  const row=el('div',undefined,'balance-bar-row'),label=el('span',item.label,'balance-label');
  const graphic=chartSVG('svg',{viewBox:'0 0 300 12',preserveAspectRatio:'none','aria-hidden':'true'});
  graphic.append(chartSVG('rect',{x:0,y:0,width:300,height:12,rx:2,class:'bar-track'}),chartSVG('rect',{x:0,y:0,width:pct*3,height:12,rx:2,class:item.address?'bar-fill':'bar-rest'}));
  row.tabIndex=0;row.title=(item.address||item.label)+' · '+chartExact(item.value,amounts.places)+' tokens · '+pct.toFixed(2)+'% of known loaded balance';row.setAttribute('aria-label',row.title);
  row.append(label,graphic,el('span',pct.toFixed(2)+'%','balance-percent'));bars.append(row);
 }
 box.append(bars);const detail=el('details',undefined,'chart-denominator');detail.append(el('summary','Denominator & exact balances'),el('p',chartExact(amounts.total,amounts.places)+' tokens across '+known.length+' known loaded balances. '+(data.holders.length-known.length)+' unknown balances excluded. Top five are ranked within this sample only. Balance timestamp: '+(data.sourceTime||'unknown')+'.'));
 for(const item of items)detail.append(el('p',(item.address||item.label)+': '+chartExact(item.value,amounts.places)+' tokens','address'));
 box.append(detail);$('holderTools').before(box);
}
function renderTradeVisual(root,observations,side,sourceData){
 root.replaceChildren();root.hidden=!observations.length;if(root.hidden)return;
 const selected=observations.filter(t=>side==='all'||t.side===side);
 const valid=selected.filter(t=>Number.isFinite(t.blockTime)&&t.blockTime>0&&t.tokenAmount!==null&&t.tokenAmount!==undefined&&/^\d+(\.\d+)?$/.test(String(t.tokenAmount))).sort((a,b)=>a.blockTime-b.blockTime||a.id.localeCompare(b.id));
 const head=el('div',undefined,'visual-heading');head.append(el('span','02 / OBSERVED TOKEN FLOW','terminal-label'),el('span','LOADED ONLY','terminal-state'));root.append(head,el('h3','Buys & sells, in sequence'),chartProvenance(sourceData));
 if(!valid.length){root.append(el('p',selected.length?'No chartable amount / timestamp in these loaded observations.':'No observations match this side filter.','visual-caption'));return;}
 const amounts=chartDecimalSet(valid.map(t=>t.tokenAmount)),max=amounts.scaled.reduce((m,v)=>v>m?v:m,0n);
 const time=t=>new Date(t*1000).toISOString().replace('T',' ').replace('.000Z',' UTC');
 root.append(el('p',time(valid[0].blockTime)+' — '+time(valid.at(-1).blockTime),'visual-caption'),el('p','Token quantity per observation · chronological order, not elapsed-time spacing. Gold = buy; coral = sell.','visual-caption'));
 const scale=el('div',undefined,'chart-scale');scale.append(el('span','MAX '+chartExact(max,amounts.places)+' TOKENS'),el('span','BASELINE 0'));root.append(scale);
 const plot=el('div',undefined,'trade-plot'),readout=el('p','Select a bar for its exact amount, time and protocol.','chart-readout');readout.setAttribute('role','status');
 valid.forEach((t,i)=>{const button=el('button',undefined,'trade-point '+t.side);button.type='button';button.dataset.id=t.id;button.dataset.amount=t.tokenAmount;
  const text=t.side.toUpperCase()+' · '+t.tokenAmount+' tokens · '+time(t.blockTime)+' · '+(t.protocol||'protocol unspecified');button.title=text;button.setAttribute('aria-label',text);
  const svg=chartSVG('svg',{viewBox:'0 0 44 100','aria-hidden':'true'}),height=max?Number(amounts.scaled[i]*9000n/max)/100:0;
  svg.append(chartSVG('line',{x1:0,y1:95,x2:44,y2:95,class:'plot-baseline'}),chartSVG('rect',{x:13,y:95-height,width:18,height:height,rx:2,class:'flow-bar'}),chartSVG('circle',{cx:22,cy:95-height,r:3,class:'flow-cap'}));
  button.append(svg,el('strong',t.side.toUpperCase()),el('small',new Date(t.blockTime*1000).toISOString().slice(11,19)));
  for(const event of ['click','focus','mouseenter'])button.addEventListener(event,()=>{readout.textContent=text;});plot.append(button);
 });
 root.append(plot,readout,el('p',valid.length+' charted / '+selected.length+' matching loaded observations. Missing amount/time excluded. Individual fills, not net position, price or profit. Source and page boundaries below.','visual-caption'));
}
function renderHolderView(){
 const query=$('holderSearch').value.trim(),sort=$('holderSort').value;
 const rows=loadedHolders.filter(h=>h.address.includes(query));
 if(sort!=='source')rows.sort((a,b)=>compareBalance(a.amount,b.amount,sort==='balance-asc'?1:-1));
 $('holdersBody').replaceChildren();
  for(const h of rows){
   const row=el('tr');row.dataset.wallet=h.address;
   const selectCell=el('td'),box=el('input');box.type='checkbox';box.className='holder-select';box.value=h.address;box.checked=holderSelection.has(h.address);box.setAttribute('aria-label','Select '+h.address);
   box.addEventListener('change',()=>{if(box.checked)holderSelection.add(h.address);else holderSelection.delete(h.address);$('holderConsent').checked=false;updateHolderSelection();});const choice=el('label',undefined,'holder-choice');choice.append(box);selectCell.append(choice);
   const owner=el('td'),ownerLink=link(short(h.address),'https://solscan.io/account/'+h.address);ownerLink.title=h.address;owner.append(ownerLink);
   if(h.classification&&h.classification!=='unknown'&&h.classificationEvidence?.length)owner.append(el('div',h.classification,'source-line'));
   const action=el('td'),open=el('button','Trades ↗','holder-trades');open.type='button';open.addEventListener('click',()=>openWalletDrawer(h.address,holderMint));action.append(open);
   row.append(selectCell,owner,el('td',holderNumber(h.amount)),el('td',holderNumber(h.supplyPercent,'%')),action);row.children[2]?.setAttribute('data-label','Token balance');row.children[3]?.setAttribute('data-label','Supply share');$('holdersBody').append(row);
  }
 if(!rows.length)holderEmpty('No matching wallets in the loaded sample. Clear the search to see all loaded holders.');
 $('holderViewStatus').textContent='Showing '+rows.length+' of '+loadedHolders.length+' loaded wallets · address search is case-sensitive · selection is retained across filters.';
 updateHolderSelection();
}
$('holderSearch').addEventListener('input',renderHolderView);
$('holderSort').addEventListener('change',renderHolderView);
$('holderReset').addEventListener('click',()=>{$('holderSearch').value='';$('holderSort').value='source';renderHolderView();});
async function discoverToken(){
 if(holderLoading||followBusy)return;
 const mint=$('tokenAddress').value;
 if(!validAddress(mint)){say('An exact Solana mint (32-byte base58) is required, without spaces. EVM / 0x is not supported.');$('tokenAddress').setAttribute('aria-invalid','true');return;}
 $('tokenAddress').removeAttribute('aria-invalid');$('tokenWelcome').hidden=true;$('holdersSection').hidden=false;$('tokenMetrics').hidden=false;holderLoading=true;holderMint=mint;loadedHolders=[];$('holderTools').hidden=true;$('holderSearch').value='';$('holderSort').value='source';holderSelection.clear();$('holderConsent').checked=false;updateHolderSelection();
 FomoMarket.show(mint);
 $('holderVisual')?.remove();$('holderSource').replaceChildren();$('holderDiagnostics').hidden=true;$('holderSubtitle').textContent='Requesting a bounded sample';
 $('tokenSearch').disabled=true;$('holderStatus').textContent='Loading';$('holderCount').textContent='—';$('holderContext').textContent=mint;$('holderCoverage').textContent='Requesting a bounded sample. Previous data cleared.';
 $('holdersSection').setAttribute('aria-busy','true');holderEmpty('Retrieving holders from a public source…');billState('loading');
 try{
  const d=await holderAPI.holders(mint);$('holderStatus').textContent=FomoData.preview?'Saved example · partial':d.status==='unavailable'?'Unavailable':d.status==='partial'?'Partial sample':'Source snapshot';
  $('holderCount').textContent=d.status==='unavailable'?'—':String(d.holders.length);
  $('holderSubtitle').textContent=d.commitment==='provider-report'?'Provider report · balance time unknown · not an on-chain check':d.commitment==='finalized'?'Finalized RPC · bounded token-account sample':'Bounded source sample';
  if(typeof d.source==='string'&&d.source.startsWith('https://'))$('holderSource').append(link('Source ↗',d.source));
  $('holderDiagnostics').hidden=false;$('holderRawCoverage').textContent=JSON.stringify({commitment:d.commitment??null,sourceTime:d.sourceTime??null,coverage:d.coverage??null,errors:d.errors??[]},null,2);

  $('holderCoverage').textContent=(FomoData.preview?'Saved snapshot: ':'Retrieved: ')+date(d.observedAt)+'. '+(d.sourceTime?'Balance time: '+d.sourceTime+'. ':'Balance time unknown. ')+'Partial account sample, not a global wallet ranking. Share of reported supply, not circulating supply.';
  loadedHolders=d.holders;renderHolderVisual(d);$('holderTools').hidden=!d.holders.length;
  if(d.holders.length)renderHolderView();
  else holderEmpty(d.status==='unavailable'?'Source unavailable. This does not mean no holders.':'No holders found in the available sample. This is not a complete registry.');
  if(d.errors?.length)$('holderCoverage').append(document.createTextNode(' Source errors: '+d.errors.length+' — details below.'));
  updateHolderSelection();say(d.status==='unavailable'?'Holder source unavailable. Monitoring unchanged.':'Holder snapshot received. Select wallets; monitoring is enabled separately.');billState(d.status==='unavailable'?'error':'evidence');
 }catch(e){$('holderStatus').textContent='Source error';holderEmpty(e.name==='AbortError'?'Source did not respond within 30 seconds. Retry the request.':'Could not retrieve holders: '+e.message);$('holderCoverage').textContent='No substitute data. Retry using the button above.';say('Holders unavailable. Monitoring unchanged.');billState('error');}
 finally{holderLoading=false;$('tokenSearch').disabled=false;$('holdersSection').setAttribute('aria-busy','false');}
}
async function openWalletDrawer(wallet,mint){
 const seq=++drawerRequest,dialog=$('walletDrawer'),root=$('drawerContent');$('drawerWallet').textContent=wallet;
 let cursor=null,loading=false;const seen=new Set(),cards=el('div'),meta=el('div'),status=el('p'),older=el('button','Load older observations');
 status.id='drawerPageStatus';status.setAttribute('role','status');older.id='drawerOlder';older.type='button';older.hidden=true;
 const summary=el('p',undefined,'observation-summary'),tools=el('div',undefined,'sample-tools drawer-tools'),label=el('label','Show loaded observations'),side=el('select'),filterStatus=el('p');
 summary.id='drawerSummary';summary.textContent='Observations not loaded yet.';side.id='drawerSide';label.htmlFor=side.id;filterStatus.id='drawerFilterStatus';filterStatus.setAttribute('role','status');
 for(const [value,text] of [['all','All sides'],['buy','Buys only'],['sell','Sells only']]){const option=el('option',text);option.value=value;side.append(option);}
 label.append(side);tools.append(label,filterStatus);const observations=[],visual=el('section',undefined,'sample-visual trade-visual');visual.hidden=true;let lastRetrieved=null;
 function renderObservationView(){
  renderTradeVisual(visual,observations,side.value,lastRetrieved);
  let visible=0;for(const card of cards.children){card.hidden=side.value!=='all'&&card.dataset.side!==side.value;if(!card.hidden)visible++;}
  if(observations.length){const buys=observations.filter(t=>t.side==='buy').length;summary.textContent=observations.length+' loaded observations · '+buys+' buy · '+(observations.length-buys)+' sell. Counts are observation rows, not complete swaps or unique transactions. AMM rows remain individual route fills; no amounts are aggregated.';}
  filterStatus.textContent=observations.length?(visible?'Showing '+visible+' of '+observations.length+' loaded observations.':'No matching observations in the loaded sample. Choose All sides or load another page.'):'Filters apply only to observations loaded in this drawer.';
 }
 side.addEventListener('change',renderObservationView);
 root.replaceChildren(el('p','Token: '+mint,'address'),summary,tools,visual,meta,cards,status,older);if(!dialog.open)dialog.showModal();
 async function loadPage(){
 if(loading||seq!==drawerRequest||!dialog.open)return;loading=true;older.disabled=true;status.className='loading';status.textContent='Loading observations…';
 try{const d=await holderAPI.trades(wallet,mint,cursor);if(seq!==drawerRequest||!dialog.open)return;
  if(d.status!=='unavailable')lastRetrieved=d;
  meta.replaceChildren(el('p','Bounded history: Pump bonding-curve, PumpSwap, Raydium AMM exact-in and Orca Whirlpool legacy. AMM rows are individual fills, not net route proceeds. PumpSwap quote amounts exclude separate fee transfers. Not all trades; a sale does not mean a complete exit. Entry cost and PnL are not calculated.','coverage-line'));
  meta.append(el('p',(FomoData.preview?'Saved snapshot: ':'Retrieved: ')+date(d.observedAt)+' · '+(d.source||'Source not specified'),'source-line'));const coverage=el('details');coverage.append(el('summary','History coverage and boundaries'),el('pre',JSON.stringify(d.coverage??{},null,2)));meta.append(coverage);
  sourceError(d.errors,meta);
  if(!d.trades.length)meta.append(el('p',d.status==='unavailable'?'History source unavailable. This does not mean no trades.':'No supported trades found in this sample. This does not mean the wallet has never traded.','empty'));
  for(const t of d.trades){
   if(seen.has(t.id))continue;seen.add(t.id);observations.push(t);
   const card=el('article',undefined,'evidence-card');card.dataset.side=t.side;card.dataset.evidence=t.id;card.append(el('h3',t.side==='buy'?'BUY · observed purchase':'SELL · observed sale'),el('p',chainDate(t.blockTime),'source-line'));
   if(t.protocol)card.append(el('p',t.protocol+' · '+(t.claim||'Individual AMM fill, not net route proceeds'),'coverage-line'));
   if(t.tokenAmount!==undefined)card.append(el('p','Token: '+holderNumber(t.tokenAmount)+' · mint '+mint,'address'));
   if(t.quoteAmount!==null&&t.quoteAmount!==undefined&&t.quoteUnit)card.append(el('p','Quote fill: '+holderNumber(t.quoteAmount)+' '+t.quoteUnit,'address'));
   const raw=el('details',undefined,'technical');raw.append(el('summary','Raw amounts'),el('p','Token raw: '+(t.tokenAmountRaw??'—')+' · Quote raw: '+(t.quoteAmountRaw??'—')+' · quote mint: '+(t.quoteMint??'unknown'),'address'));card.append(raw);
   if(typeof t.signature==='string'&&/^[1-9A-HJ-NP-Za-km-z]{64,88}$/.test(t.signature))card.append(receiptLink(t));cards.append(card);
  }
  if(!observations.length)summary.textContent=d.status==='unavailable'?'Observations unavailable. No activity count can be established.':'No supported observations loaded. This is not evidence of zero wallet activity.';
  renderObservationView();
  if(d.status==='unavailable'){older.hidden=false;older.textContent='Retry observations';status.className='error-note';status.textContent='Source unavailable. Retry this page; loaded observations are retained.';}
  else{cursor=FomoData.preview?null:d.coverage?.nextCursor??null;older.hidden=!cursor;older.textContent='Load older observations';status.className='source-line';status.textContent=FomoData.preview?'Saved example page. Open your live workspace to request older history.':cursor?'On request only: next page of the bounded sample.':'Available sample pages exhausted. This is not a complete wallet history.';}
 }catch(e){if(seq===drawerRequest&&dialog.open){if(!observations.length)summary.textContent='Observations unavailable. No activity count can be established.';renderObservationView();status.className='error-note';status.textContent=e.name==='AbortError'?'History did not respond within 30 seconds. Retry the request.':'History unavailable: '+e.message;older.hidden=false;older.textContent='Retry observations';}}
 finally{loading=false;if(seq===drawerRequest&&dialog.open)older.disabled=false;}
 }
 older.addEventListener('click',loadPage);await loadPage();
}
async function followSelectedHolders(){
 if(followBusy||!$('holderConsent').checked||!holderSelection.size||!monitorSnapshot)return;
 const selected=[...holderSelection];followBusy=true;updateHolderSelection();let confirmed=0;
 try{
  if(!identityReady){await privateAPI('/api/session',{});identityReady=true;}
  let snapshot=await privateAPI('/api/monitor');
  const fresh=selected.filter(a=>!snapshot.watches.some(w=>w.address===a));
  if(snapshot.watches.length+fresh.length>8)throw Error('Limit: 8 addresses. Remove a monitored address or reduce your selection.');
  for(const address of fresh){
   await privateAPI('/api/monitor/watch',{address});
   snapshot=await privateAPI('/api/monitor');
   if(!snapshot.watches.some(w=>w.address===address))throw Error('The server did not confirm monitoring for '+short(address)+'. Refresh the list.');
   confirmed++;holderSelection.delete(address);const box=[...document.querySelectorAll('.holder-select')].find(b=>b.value===address);if(box)box.checked=false;
  }
  renderMonitor(snapshot);$('followStatus').textContent='Confirmed addresses: '+confirmed+'. Monitoring for 7 days. Telegram is enabled separately with /subscribe.';say($('followStatus').textContent);
 }catch(e){$('followStatus').textContent='Confirmed: '+confirmed+'. '+e.message;say($('followStatus').textContent);}
 finally{followBusy=false;$('holderConsent').checked=false;updateHolderSelection();$('followBar').hidden=false;}
}
$('tokenForm').addEventListener('submit',e=>{e.preventDefault();discoverToken();});
$('holderConsent').addEventListener('change',updateHolderSelection);
$('followSelected').addEventListener('click',followSelectedHolders);
$('drawerClose').addEventListener('click',()=>$('walletDrawer').close());
$('walletDrawer').addEventListener('close',()=>{drawerRequest++;});
document.querySelectorAll('a[href^="#"]').forEach(a=>a.addEventListener('click',()=>{const target=document.getElementById(a.hash.slice(1));if(target?.tagName==='DETAILS')target.open=true;}));
