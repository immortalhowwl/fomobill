'use strict';
// Market prices are independent of holder balances and decoded wallet receipts.
(function(root){
 const address=x=>typeof x==='string'&&/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(x);
 const amount=x=>x!==null&&x!==''&&typeof x!=='boolean'&&Number.isFinite(Number(x))&&Number(x)>=0;
 function selectPair(data,mint){
  if(data?.chain!=='solana'||data.address!==mint||data.status!=='ok'||!Array.isArray(data.pairs))return null;
  return data.pairs.filter(p=>p.tokenAddress===mint&&address(p.pairAddress)).sort((a,b)=>(amount(b.liquidityUsd)?Number(b.liquidityUsd):-1)-(amount(a.liquidityUsd)?Number(a.liquidityUsd):-1))[0]||null;
 }
 function embedUrl(pair){return 'https://dexscreener.com/solana/'+pair.pairAddress+'?embed=1&info=0&trades=0&theme=dark';}
 if(typeof module!=='undefined')module.exports={selectPair,embedUrl};
 let serial=0,controller=null;
 async function show(mint){
  const box=document.getElementById('marketChart');if(!box)return;
  const seq=++serial;controller?.abort();controller=new AbortController();const request=controller;const timer=setTimeout(()=>request.abort(),20000);
  box.hidden=false;box.dataset.mint=mint;box.replaceChildren();
  const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;};
  box.append(node('span','MARKET / SOLANA','eyebrow'),node('h2','Token price'),node('p','Resolving the exact token’s market…','market-status'));
  try{
   const response=await fetch('/api/token?address='+encodeURIComponent(mint),{signal:controller.signal});if(!response.ok)throw Error('Source request failed');const data=await response.json();if(seq!==serial)return;
   const pair=selectPair(data,mint),status=box.querySelector('.market-status');
   if(!pair){status.textContent=data.status==='empty'?'No supported base-token pool found. Chart unavailable for this token.':'Market source unavailable. Retry the token search. Holders work independently.';return;}
   box.querySelector('h2').textContent=(pair.symbol||'Token')+' / '+(pair.quoteSymbol||'quote')+' · Price';
   status.textContent='DEX Screener · '+(pair.dex||'DEX')+' · highest reported USD liquidity among returned base-token pools.';
   const metrics=node('div',null,'market-metrics');
   for(const [label,value] of [['Price (USD)',pair.priceUsd],['Pool liquidity (USD)',pair.liquidityUsd],['Pool volume · 24h (USD)',pair.volume24hUsd]]){
    const cell=node('div');cell.append(node('span',label),node('strong',amount(value)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumSignificantDigits:6}).format(Number(value)):'Unavailable'));metrics.append(cell);
   }
   box.append(metrics,node('p','Metrics retrieved '+(data.observedAt?new Date(data.observedAt).toLocaleString():'at an unknown time')+(data.cache?.hit?' · cached '+data.cache.ageSeconds+'s':'')+'. The chart updates independently.','market-note'));
   const frame=node('iframe');frame.title='DEX Screener live token candlestick chart';frame.src=embedUrl(pair);frame.loading='eager';frame.referrerPolicy='strict-origin-when-cross-origin';frame.setAttribute('allow','fullscreen');box.append(frame);
   const note=node('p',null,'market-note');note.append(document.createTextNode('Chart blank or blocked? '));const fallback=node('a','Open this pool on DEX Screener');fallback.href='https://dexscreener.com/solana/'+pair.pairAddress;fallback.target='_blank';fallback.rel='noopener noreferrer';note.append(fallback);box.append(note,node('p','Live market data from DEX Screener, not wallet receipt analysis. Pool: '+pair.pairAddress,'market-note'));
  }catch(error){if(seq===serial)box.querySelector('.market-status').textContent='Market source unavailable. Retry the token search. Holders work independently.';}
  finally{clearTimeout(timer);}
 }
 root.FomoMarket={selectPair,embedUrl,show};
})(typeof window==='undefined'?globalThis:window);
