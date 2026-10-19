/* Same-origin production API. Hosted design preview reads only dated snapshots. */
(() => {
  'use strict';
  const config = window.FOMOBILL_CONFIG || {};
  const preview = typeof config.preview === 'boolean' ? config.preview : location.hostname.endsWith('.chatgpt.site') || ['localhost', '127.0.0.1', ''].includes(location.hostname);
  const sampleMint = '2SpVp5EyXBNc1dwh8fzQ1Gw1yeK9ufNSYyMK5zrfpump';
  const sampleWallet = '84UBp52FJZ5xwD5sGUqddzujNUUUAjNzNtLBfVeLXW84';
  const root = new URL('./', document.currentScript.src);
  const cached = new Map();
  async function snapshot(name, signal) {
    if (cached.has(name)) return structuredClone(cached.get(name));
    const response = await fetch(new URL('examples/' + name, root), {signal});
    if (!response.ok) throw Error('The saved example could not be opened. Please retry.');
    const data = await response.json();
    cached.set(name, data);
    return structuredClone(data);
  }
  async function apiFetch(path, options = {}) {
    if (!preview) return fetch(path, options);
    if ((options.method || 'GET').toUpperCase() !== 'GET') throw Error('Use your live workspace to change monitoring or connect Telegram. This preview does not change your account.');
    const url = new URL(path, location.href), address = url.searchParams.get('address');
    let file;
    if (url.pathname === '/api/holders' && address === sampleMint) file = 'holders-snapshot.json';
    if (url.pathname === '/api/token' && address === sampleMint) file = 'token-snapshot.json';
    if (url.pathname === '/api/wallet' && address === sampleWallet) file = 'wallet-snapshot.json';
    if (url.pathname === '/api/holder-trades' && url.searchParams.get('mint') === sampleMint && !url.searchParams.has('cursor')) {
      const index = await snapshot('holder-trades-index.json', options.signal);
      file = index[address];
    }
    if (!file) throw Error('This preview includes saved examples only. Choose “Try a saved example”, or open your live workspace to check another address.');
    const data = await snapshot(file, options.signal);
    return new Response(JSON.stringify(data), {status:200, headers:{'Content-Type':'application/json'}});
  }
  window.FomoData = {preview, sampleMint, sampleWallet, apiFetch};
})();
