(() => {
  'use strict';
  const routes = {
    tokens:{label:'Explore a token',heading:'tokensTitle'},
    wallets:{label:'Watch wallets',heading:'walletsTitle'},
    alerts:{label:'Telegram alerts',heading:'alertsTitle'},
    lookup:{label:'Check an address',heading:'lookupTitle'}
  };
  const aliases = {discover:'tokens',holdersSection:'tokens',monitorSection:'wallets',telegramPanel:'alerts',manualTools:'lookup',results:'lookup'};
  let active = 'tokens';
  const byId = id => document.getElementById(id);
  function renderRoute(page, focus = true) {
    if (!routes[page]) page = 'tokens';
    const changed = active !== page; active = page;
    document.querySelectorAll('[data-page]').forEach(node => {node.hidden = node.dataset.page !== page});
    document.querySelectorAll('[data-nav]').forEach(node => {
      if (node.dataset.nav === page) node.setAttribute('aria-current','page');
      else node.removeAttribute('aria-current');
    });
    byId('breadcrumbCurrent').textContent = routes[page].label;
    document.title = routes[page].label + ' · FOMOBILL';
    if (changed) byId('feedback').hidden = true;
    if (focus) {byId(routes[page].heading).focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'});}
  }
  function navigate(page, focus = true) {
    page = aliases[page] || page;
    if (!routes[page]) page = 'tokens';
    if (location.hash !== '#' + page) history.pushState(null,'','#'+page);
    renderRoute(page,focus);
  }
  function fromLocation(focus = true) {
    const target = location.hash.slice(1);
    if (target !== 'sourceLimits' && byId('coverageDialog').open) byId('coverageDialog').close();
    renderRoute(aliases[target] || target || 'tokens',focus);
    if (target === 'sourceLimits' && !byId('coverageDialog').open) byId('coverageDialog').showModal();
  }
  window.FomoUI = {navigate};
  document.querySelectorAll('a[href^="#"]').forEach(link => {
    const target = link.getAttribute('href').slice(1);
    if (!routes[target] && !aliases[target]) return;
    link.addEventListener('click',event=>{event.preventDefault();navigate(target)});
  });
  addEventListener('hashchange',()=>fromLocation());
  addEventListener('popstate',()=>fromLocation());
  fromLocation(false);
  byId('dismissFeedback').addEventListener('click',()=>{byId('feedback').hidden=true});
  byId('sampleToken').addEventListener('click',()=>{byId('tokenAddress').value=FomoData.sampleMint;discoverToken()});
  byId('focusMonitor').addEventListener('click',()=>{byId('monitorAddress').focus();byId('monitorForm').scrollIntoView({block:'center',behavior:'smooth'})});
  const dialogSources = new WeakMap();
  function openDialog(id,trigger) {const dialog=byId(id);dialogSources.set(dialog,trigger);if(!dialog.open)dialog.showModal()}
  document.querySelectorAll('[data-help]').forEach(button=>button.addEventListener('click',()=>openDialog('helpDialog',button)));
  document.querySelectorAll('[data-coverage]').forEach(button=>button.addEventListener('click',()=>openDialog('coverageDialog',button)));
  document.querySelectorAll('[data-case]').forEach(button=>button.addEventListener('click',()=>openDialog('caseDialog',button)));
  document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
  document.querySelectorAll('dialog').forEach(dialog=>{
    dialog.addEventListener('click',event=>{
      if(event.target!==dialog)return;
      const box=dialog.getBoundingClientRect();
      if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();
    });
    dialog.addEventListener('close',()=>dialogSources.get(dialog)?.focus({preventScroll:true}));
  });
  byId('startTour').addEventListener('click',()=>{byId('helpDialog').close();navigate('tokens');byId('tokenAddress').focus()});
  if (FomoData.preview) {
    byId('previewNotice').hidden=false;
    byId('telegramBadge').textContent='Preview';
    byId('monitorStart').disabled=true;
    byId('monitorStart').title='Start monitoring on your live Railway site.';
    const links=[['monitorForm','https://fomobill-production.up.railway.app/#monitorSection','Watch wallets on your live site ↗'],['telegramLink','https://fomobill-production.up.railway.app/#telegramPanel','Open live Telegram setup ↗']];
    for (const [id,href,text] of links) {const a=document.createElement('a');a.className='text-button preview-live-link';a.href=href;a.target='_blank';a.rel='noopener';a.textContent=text;byId(id).insertAdjacentElement('afterend',a)}
  } else {
    byId('sampleToken').textContent='Try an example token ↗';
    byId('exampleButton').textContent='Try an example wallet ↗';
  }
  // This small accent follows the pointer without competing with the data.
  const pupil=byId('guidePupil'),bill=byId('billArt'),reduce=matchMedia('(prefers-reduced-motion: reduce)');
  let frame=0,pending=null;
  function followEye(event) {
    if(reduce.matches||active!=='tokens'||!bill.getClientRects().length)return;
    pending={x:event.clientX,y:event.clientY};
    if(frame)return;
    frame=requestAnimationFrame(()=>{
      frame=0;if(!pending)return;
      const b=bill.getBoundingClientRect(),x=Math.max(-6,Math.min(6,(pending.x-b.left-b.width/2)/65)),y=Math.max(-3,Math.min(3,(pending.y-b.top-b.height*.55)/100));
      pupil.setAttribute('transform',`translate(${x.toFixed(2)} ${y.toFixed(2)})`);
    });
  }
  document.addEventListener('pointermove',followEye,{passive:true});
  document.addEventListener('pointerdown',followEye,{passive:true});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelAnimationFrame(frame);frame=0}});
})();
