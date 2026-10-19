"""Read-only bounded public holder discovery. No credentials or import-time I/O."""
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from monitoring import address, RPCS, decode_transaction

REPORT = 'https://api.rugcheck.xyz/v1/tokens/'
PROGRAMS = {'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA', 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'}
MAX_BODY = 4 * 1024 * 1024

def stamp():
    return datetime.now(timezone.utc).isoformat()

def integer(value):
    if type(value) is int and 0 <= value <= 2**64-1:
        return value
    if isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]{0,19}', value):
        n = int(value)
        if n <= 2**64-1: return n
    raise ValueError('Expected unsigned exact u64 amount')

def decimals(value):
    if type(value) is not int or not 0 <= value <= 255:
        raise ValueError('Invalid token decimals')
    return value

def scaled(value, places):
    with localcontext() as ctx:
        ctx.prec = 300
        return format(Decimal(value).scaleb(-places), 'f').rstrip('0').rstrip('.') if places else str(value)

def percentage(value, supply):
    if not supply: return None
    with localcontext() as ctx:
        ctx.prec = 40
        return format(Decimal(value) * 100 / Decimal(supply), '.12f').rstrip('0').rstrip('.') or '0'

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Provider redirect rejected')

def public_transport(url, payload, *, deadline=None):
    if url not in RPCS:
        if not url.startswith(REPORT) or not url.endswith('/report'):
            raise ValueError('Provider URL not allowed')
        address(url[len(REPORT):-len('/report')])
        if payload is not None: raise ValueError('Report must use GET')
    req = Request(url, data=None if payload is None else json.dumps(payload).encode(),
                  headers={'Content-Type':'application/json', 'User-Agent':'FOMOBILL-holders/1'})
    import time
    timeout = 6 if deadline is None else min(3, max(0.001, deadline-time.monotonic()))
    try:
        with build_opener(NoRedirect()).open(req, timeout=timeout) as response:
            chunks=[]; size=0
            while size <= MAX_BODY:
                if deadline is not None and time.monotonic() >= deadline: raise ValueError('History deadline reached')
                chunk=response.read1(min(65536,MAX_BODY+1-size))
                if not chunk: break
                chunks.append(chunk); size+=len(chunk)
            return b''.join(chunks), response.status, dict(response.headers)
    except HTTPError as exc:
        return exc.read(MAX_BODY+1), exc.code, dict(exc.headers)

class Holders:
    def __init__(self, transport=None):
        self.transport = transport or public_transport

    def _fetch(self, url, payload=None):
        raw, status, headers = self.transport(url, payload)
        if status != 200: raise ValueError('HTTP '+str(status))
        if len(raw) > MAX_BODY: raise ValueError('Response exceeds byte bound')
        data = json.loads(raw)
        if not isinstance(data, dict): raise ValueError('Expected JSON object')
        if payload is not None:
            if data.get('error'): raise ValueError('RPC '+str(data['error'])[:180])
            if data.get('result') is None: raise ValueError('Null/missing RPC result')
            return data['result']
        return data

    @staticmethod
    def _error(out, source, exc, code='provider_error'):
        out['errors'].append({'source':source,'code':code,'message':str(exc)[:220]})

    @staticmethod
    def _aggregate(rows, supply, places, source, limit, known=None):
        if known is not None and not isinstance(known, dict): raise ValueError('Invalid knownAccounts map')
        owners = {}; seen = set()
        for row in rows:
            account = address(row['address']); owner = address(row['owner'])
            amount = integer(row['amount'])
            if decimals(row['decimals']) != places: raise ValueError('Decimals mismatch')
            if account in seen: raise ValueError('Duplicate token account')
            seen.add(account)
            item = owners.setdefault(owner, {'owner':owner,'tokenAccounts':[], '_amount':0,
                'classification':'unknown','classificationEvidence':[], 'url':'https://solscan.io/account/'+owner})
            item['tokenAccounts'].append(account); item['_amount'] += amount
        if supply and sum(x['_amount'] for x in owners.values()) > supply:
            raise ValueError('Observed balances exceed reported supply')
        result = sorted(owners.values(), key=lambda x:(-x['_amount'],x['owner']))
        for rank, item in enumerate(result, 1):
            # Only explicit owner labels apply to an aggregated owner. A single
            # labelled vault must not classify unrelated balances of that owner.
            label = (known or {}).get(item['owner'], {})
            kind = {'AMM':'pool', 'TREASURY':'treasury'}.get(label.get('type')) if isinstance(label,dict) else None
            if kind:
                item['classification'] = kind
                item['classificationEvidence'] = [{'source':source,'account':item['owner'],
                    'label':str(label.get('name') or label['type'])[:160]}]
            amount = item.pop('_amount')
            item.update(rank=rank, amountRaw=str(amount), amount=scaled(amount,places) or '0', supplyPercent=percentage(amount,supply))
        return result[:limit], len(seen), len(owners)

    def wallet_history_page(self, mint, wallet, limit=5, cursor=None):
        """One round-robin page across verified current token accounts + owner.

        Opaque continuation is bound to mint/wallet, never an arbitrary RPC address.
        Closed accounts, other DEX formats and older provider history remain gaps.
        The application transport must enforce its existing 20s lookup deadline.
        """
        import base64
        import time
        deadline=time.monotonic()+20
        def bounded_transport(url, payload):
            if time.monotonic() >= deadline: raise ValueError('History deadline reached; continue explicitly')
            if self.transport is public_transport: return public_transport(url,payload,deadline=deadline)
            return self.transport(url,payload)
        client=Holders(bounded_transport)
        from holder_history_sources import signature
        address(mint); address(wallet)
        if type(limit) is not int or not 1 <= limit <= 10: raise ValueError('limit must be 1..10')
        state = None
        if cursor is not None:
            try:
                if not isinstance(cursor,str) or not 1 <= len(cursor) <= 4096: raise ValueError()
                state = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
                if set(state) != {'v','mint','wallet','addresses','before','done','index'}: raise ValueError()
                if state['v'] != 1 or state['mint'] != mint or state['wallet'] != wallet: raise ValueError()
                aa=state['addresses']; bb=state['before']; dd=state['done']; ii=state['index']
                if not isinstance(aa,list) or not 1 <= len(aa) <= 5 or len(set(aa)) != len(aa) or aa[-1] != wallet: raise ValueError()
                for a in aa: address(a)
                if not isinstance(bb,list) or not isinstance(dd,list) or len(bb)!=len(aa) or len(dd)!=len(aa): raise ValueError()
                for b in bb:
                    if b is not None: signature(b)
                if any(type(d) is not bool for d in dd) or type(ii) is not int or not 0 <= ii < len(aa) or dd[ii]: raise ValueError()
            except (ValueError,KeyError,TypeError,UnicodeError):
                raise ValueError('Invalid history cursor') from None
        errors=[]; accounts=None; endpoint_used=None
        for endpoint in RPCS:
            try:
                response=client._fetch(endpoint,{'jsonrpc':'2.0','id':1,'method':'getTokenAccountsByOwner',
                    'params':[wallet,{'mint':mint},{'encoding':'jsonParsed','commitment':'finalized'}]})
                rows=response['value']
                if not isinstance(rows,list): raise ValueError('Invalid token account response')
                found=set()
                for row in rows:
                    a=address(row['pubkey']); info=row['account']; parsed=info['data']['parsed']
                    if info['owner'] not in PROGRAMS or parsed['type'] != 'account' or parsed['info']['mint'] != mint or parsed['info']['owner'] != wallet:
                        raise ValueError('Token account owner/mint mismatch')
                    if a in found or a==wallet: raise ValueError('Invalid duplicate token account')
                    found.add(a)
                accounts=sorted(found); endpoint_used=endpoint; break
            except (ValueError,KeyError,TypeError,OSError) as exc:
                errors.append({'source':endpoint,'code':'account_discovery_unavailable','message':str(exc)[:220]})
        if state is None:
            aa=(accounts or [])[:4]+[wallet]
            state={'v':1,'mint':mint,'wallet':wallet,'addresses':aa,'before':[None]*len(aa),'done':[False]*len(aa),'index':0}
        aa=state['addresses']; i=state['index']; target=aa[i]
        # Revalidate account ownership on every continuation; a client cursor is
        # not evidence. Never turn a removed/closed account into wallet history.
        if target != wallet and (accounts is None or target not in accounts):
            out={'schema':'holder-history.v1','mint':mint,'wallet':wallet,'status':'unavailable',
                 'observedAt':stamp(),'source':endpoint_used,'commitment':'finalized','trades':[],
                 'errors':errors+[{'source':endpoint_used,'code':'account_no_longer_verified','message':'Current token-account ownership unavailable; continuation retained'}],
                 'coverage':{'complete':False,'nextCursor':cursor,'hasMore':None,'gaps':[]}}
        else:
            out=client.wallet_history(mint,wallet,limit=limit,before=state['before'][i],_discovery_address=target)
            out['errors']=errors+out['errors']
            cov=out['coverage']
            if out['status'] != 'unavailable':
                if cov['nextBefore'] is not None: state['before'][i]=cov['nextBefore']
                if not cov['gaps'] and cov['signaturesObserved'] < limit: state['done'][i]=True
            pending=[(i+n)%len(aa) for n in range(1,len(aa)+1) if not state['done'][(i+n)%len(aa)]]
            if pending: state['index']=pending[0]
            cov['nextCursor']=base64.urlsafe_b64encode(json.dumps(state,separators=(',',':')).encode()).decode() if pending else None
            cov['hasMore']=None if pending else False
        out['coverage'].update(ataExpanded=accounts is not None and bool(aa[:-1]),
            discoveryAddress=target,discoveryAddresses=aa,tokenAccountsObserved=len(accounts) if accounts is not None else None,
            tokenAccountLimit=4,tokenAccountsTruncated=accounts is not None and len(accounts)>4,
            accountDiscoverySource=endpoint_used,
            scope='Round-robin bounded current token-account and exact-owner signature pages; Pump bonding-curve, PumpSwap, Raydium AMM exact-in and Orca legacy fills; closed accounts and other DEX formats excluded')
        return out

    def wallet_history(self, mint, wallet, limit=10, before=None, *, _discovery_address=None):
        # Local import avoids the sibling decoder's shared validation import cycle.
        from holder_history_sources import decode_amm_transaction
        address(mint); address(wallet)
        if type(limit) is not int or not 1 <= limit <= 10: raise ValueError('limit must be 1..10')
        def sig_valid(value):
            if not isinstance(value,str) or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{64,88}',value):
                raise ValueError('Invalid signature')
            alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
            n = 0
            for char in value: n = n * 58 + alphabet.index(char)
            if (n.bit_length()+7)//8 + len(value)-len(value.lstrip('1')) != 64:
                raise ValueError('Signature must decode to 64 bytes')
            return value
        if before is not None: sig_valid(before)
        out = {'schema':'holder-history.v1','mint':mint,'wallet':wallet,'status':'unavailable',
            'observedAt':stamp(),'source':None,'commitment':'finalized','trades':[],'errors':[],
            'coverage':{'complete':False,'scope':'exact wallet signatures; Pump bonding-curve events plus PumpSwap / Raydium AMM exact-in / Orca Whirlpool legacy individual fills; not net route proceeds',
                'ataExpanded':False,'ammSupported':True,'ammScope':['pumpswap','raydium-amm-exact-in','orca-whirlpool-legacy-swap'],'signatureLimit':limit,'signaturesObserved':0,
                'transactionsFetched':0,'failedTransactions':0,'before':before,'nextBefore':None,
                'hasMore':None,'oldestBlockTime':None,'newestBlockTime':None,'gaps':[]}}
        rows = None
        for endpoint in RPCS:
            try:
                config = {'limit':limit,'commitment':'finalized'}
                if before: config['before'] = before
                candidate = self._fetch(endpoint,{'jsonrpc':'2.0','id':1,
                    'method':'getSignaturesForAddress','params':[address(_discovery_address) if _discovery_address else wallet,config]})
                if not isinstance(candidate,list) or len(candidate)>limit: raise ValueError('Invalid signature sample')
                seen = set()
                for row in candidate:
                    sig = sig_valid(row['signature'])
                    if sig in seen: raise ValueError('Duplicate signature')
                    seen.add(sig)
                    if 'err' not in row: raise ValueError('Missing signature execution status')
                    if row.get('blockTime') is not None and (type(row['blockTime']) is not int or row['blockTime']<0):
                        raise ValueError('Invalid signature time')
                rows = candidate
                out.update(status='partial',source=endpoint)
                break
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self._error(out,endpoint,exc)
        if rows is None: return out
        coverage = out['coverage']; coverage['signaturesObserved'] = len(rows)
        times = [r['blockTime'] for r in rows if r.get('blockTime') is not None]
        if times: coverage.update(oldestBlockTime=min(times),newestBlockTime=max(times))
        seen_events = set(); stalled = False
        for row in rows:
            sig = row['signature']
            if row['err'] is not None:
                coverage['failedTransactions'] += 1
                if not stalled: coverage['nextBefore'] = sig
                continue
            try:
                tx = self._fetch(out['source'],{'jsonrpc':'2.0','id':1,'method':'getTransaction',
                    'params':[sig,{'commitment':'finalized','encoding':'jsonParsed','maxSupportedTransactionVersion':1}]})
                coverage['transactionsFetched'] += 1
                decoded = decode_transaction(tx,sig)
                amm = decode_amm_transaction(tx,sig,mint,wallet)
                # Decode the SAME receipt; no extra RPC per protocol. Each decoder
                # fails closed independently; any gap prevents cursor advancement.
                for event in (decoded['trades'] if not decoded['gaps'] else []) + amm['trades']:
                    if event['mint'] != mint or event['wallet'] != wallet or event['id'] in seen_events: continue
                    seen_events.add(event['id'])
                    item = dict(event)
                    if 'protocol' not in item:
                        native=scaled(integer(event['solAmountRaw']),9) or '0' if event['solAmountRaw'] is not None else None
                        item.update(nativeAmount=native,nativeUnit='SOL' if native is not None else None,
                                    quoteAmount=native,quoteUnit='SOL' if native is not None else event.get('quoteMint'),
                                    claim='Observed Pump bonding-curve event; not full wallet proceeds')
                    else:
                        item.update(source='Verified AMM fill',schema='holder-amm-fill.v1')
                    item.update(usdAmount=None,usdUnit='USD',observedAt=out['observedAt'],
                                rpcSource=out['source'],commitment='finalized')
                    out['trades'].append(item)
                if decoded['gaps'] or amm['gaps']:raise ValueError('; '.join(decoded['gaps']+amm['gaps']))
                if not stalled: coverage['nextBefore'] = sig
            except (ValueError, KeyError, TypeError, OSError) as exc:
                stalled = True
                coverage['gaps'].append({'signature':sig,'message':str(exc)[:220]})
                self._error(out,out['source'],exc,'transaction_unavailable')
                # Do not amplify a denied/throttled provider into ten calls.
                if str(exc).startswith(('HTTP 403','HTTP 429','HTTP 401')): break
        return out

    def top_holders(self, mint, limit=20):
        address(mint)
        if type(limit) is not int or not 1 <= limit <= 20: raise ValueError('limit must be 1..20')
        out = {'schema':'holders.v1','mint':mint,'status':'unavailable','observedAt':stamp(),
               'source':None,'sourceTime':None,'commitment':None,'supplyRaw':None,'decimals':None,
               'supply':None,'holders':[],'errors':[], 'coverage':{'complete':False,
               'scope':'largest token-account sample aggregated by owner; not global owner ranking',
               'accountLimit':20,'accountsObserved':0,'ownersObserved':0,'returnedOwners':0,
               'sampleSupplyPercent':None,'snapshotAtomic':False,'slots':{},'sourceDetectedAt':None}}
        for endpoint in RPCS:
            try:
                def rpc(method, params):
                    return self._fetch(endpoint, {'jsonrpc':'2.0','id':1,'method':method,'params':params})
                supply_result = rpc('getTokenSupply',[mint,{'commitment':'finalized'}])
                supply = integer(supply_result['value']['amount'])
                places = decimals(supply_result['value']['decimals'])
                largest = rpc('getTokenLargestAccounts',[mint,{'commitment':'finalized'}])
                candidates = largest['value']
                if not isinstance(candidates,list) or not 1 <= len(candidates) <= 20:
                    raise ValueError('No bounded largest-account sample')
                accounts = [address(r['address']) for r in candidates]
                resolved = rpc('getMultipleAccounts',[accounts,{'encoding':'jsonParsed','commitment':'finalized'}])
                if not isinstance(resolved['value'],list) or len(resolved['value']) != len(accounts):
                    raise ValueError('Account resolution length mismatch')
                rows = []
                for account, info in zip(accounts,resolved['value']):
                    if not isinstance(info,dict) or info.get('owner') not in PROGRAMS:
                        raise ValueError('Missing account or unsupported token program')
                    parsed = info['data']['parsed']
                    if parsed['type'] != 'account' or parsed['info']['mint'] != mint:
                        raise ValueError('Resolved token account mint/type mismatch')
                    token = parsed['info']
                    rows.append({'address':account,'owner':token['owner'],
                        'amount':token['tokenAmount']['amount'],'decimals':token['tokenAmount']['decimals']})
                result, count, owners = self._aggregate(rows,supply,places,endpoint,limit)
                slots = {'supply':supply_result['context']['slot'],'largest':largest['context']['slot'],
                         'accounts':resolved['context']['slot']}
                if any(type(s) is not int or s < 0 for s in slots.values()): raise ValueError('Invalid context slot')
                out.update(status='partial',source=endpoint,commitment='finalized',supplyRaw=str(supply),
                           decimals=places,supply=scaled(supply,places) or '0',holders=result)
                out['coverage'].update(accountsObserved=count,ownersObserved=owners,returnedOwners=len(result),
                    sampleSupplyPercent=percentage(sum(integer(r['amount']) for r in rows),supply),slots=slots)
                return out
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self._error(out,endpoint,exc)
        url = REPORT+mint+'/report'
        try:
            data = self._fetch(url)
            if data.get('mint') != mint: raise ValueError('Report mint mismatch')
            if data.get('tokenProgram') not in PROGRAMS: raise ValueError('Unsupported token program')
            supply = integer(data['token']['supply']); places = decimals(data['token']['decimals'])
            rows = data.get('topHolders')
            if not isinstance(rows, list) or not 1 <= len(rows) <= 20: raise ValueError('No bounded holder sample available')
            result, accounts, owners = self._aggregate(rows,supply,places,url,limit,data.get('knownAccounts'))
            out.update(status='partial',source=url,commitment='provider-report',supplyRaw=str(supply),
                       decimals=places,supply=scaled(supply,places) or '0',holders=result)
            out['coverage'].update(accountsObserved=accounts,ownersObserved=owners,returnedOwners=len(result),
                sampleSupplyPercent=percentage(sum(integer(r['amount']) for r in rows),supply),
                sourceDetectedAt=data.get('detectedAt'))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self._error(out,url,exc)
        return out
