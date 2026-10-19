"""Keyless, bounded receipt-backed AMM fills. No import-time/network side effects.

Only recognized swaps with exact user accounts and two SPL cash/token legs qualify.
These are individual AMM fills, including routed fills, not whole-route proceeds.
"""
import hashlib
import re
import struct
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from holders import Holders, integer, address

TOKEN = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN22 = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
PUMPSWAP = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'
RAY = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
ORCA = 'whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc'
WSOL = 'So11111111111111111111111111111111111111112'
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def unbase58(s):
    if not isinstance(s, str) or not 1 <= len(s) <= 2048:
        raise ValueError('Invalid base58 length')
    n = 0
    for c in s:
        if c not in ALPHABET: raise ValueError('Invalid base58')
        n = n * 58 + ALPHABET.index(c)
    return b'\0' * (len(s)-len(s.lstrip('1'))) + n.to_bytes((n.bit_length()+7)//8, 'big')


def signature(s):
    if len(unbase58(s)) != 64: raise ValueError('Invalid signature')
    return s


def scaled(n, d):
    with localcontext() as ctx:
        ctx.prec = 100
        return format(Decimal(n).scaleb(-d), 'f')


def _log_invocations(logs):
    if not isinstance(logs, list) or not logs: raise ValueError('Missing execution logs')
    stack = []; calls = []
    for line in logs:
        if ' failed:' in line or 'Log truncated' in line: raise ValueError('Failed/truncated execution')
        m = re.fullmatch(r'Program (\w+) invoke \[(\d+)\]', line)
        if m:
            pid, height = m[1], int(m[2])
            if height != len(stack)+1: raise ValueError('Invalid invocation depth')
            stack.append(pid); calls.append((pid, height))
        m = re.fullmatch(r'Program (\w+) success', line)
        if m:
            if not stack or stack.pop() != m[1]: raise ValueError('Invalid invocation return')
    if stack: raise ValueError('Unclosed invocation')
    return calls


def _instructions(tx):
    top = tx['transaction']['message']['instructions']
    groups = {}
    for g in tx['meta'].get('innerInstructions') or []:
        i = g['index']
        if type(i) is not int or i < 0 or i >= len(top) or i in groups:
            raise ValueError('Invalid inner instruction group')
        groups[i] = g['instructions']
    out = []
    for i, ix in enumerate(top):
        out.append((str(i), 1, ix))
        prev = 1
        for j, child in enumerate(groups.get(i, [])):
            h = child.get('stackHeight')
            if type(h) is not int or h < 2 or h > prev+1: raise ValueError('Missing/invalid CPI depth')
            out.append((str(i)+'.'+str(j), h, child)); prev = h
    return out


def _accounts(tx, instructions):
    keys = tx['transaction']['message']['accountKeys']; result = {}; decimals = {}
    for row in tx['meta']['preTokenBalances'] + tx['meta']['postTokenBalances']:
        if row.get('programId') not in (TOKEN,TOKEN22): continue
        d = row['uiTokenAmount']['decimals']
        if type(d) is not int or not 0 <= d <= 38: raise ValueError('Invalid decimals')
        key = keys[row['accountIndex']]['pubkey']
        value = (address(row['mint']), address(row['owner']), d)
        if key in result and result[key] != value: raise ValueError('Account metadata changed')
        if value[0] in decimals and decimals[value[0]] != d: raise ValueError('Mint decimals conflict')
        result[key] = value; decimals[value[0]] = d
    # Temporarily created then closed WSOL accounts have neither balance record.
    # Successful SPL initializeAccount is explicit ownership/mint evidence.
    for _, _, ix in instructions:
        p = ix.get('parsed', {})
        if ix.get('programId') in (TOKEN,TOKEN22) and p.get('type') in ('initializeAccount','initializeAccount2','initializeAccount3'):
            info = p['info']; mint = info['mint']
            if mint in decimals:
                val = (address(mint), address(info['owner']), decimals[mint])
                key = address(info['account'])
                if key in result and result[key] != val: raise ValueError('Initialized account conflict')
                result[key] = val
    return result


def _swap(ix):
    """Official source instruction tags/accounts; deliberately narrow schemas."""
    pid = ix.get('programId')
    if pid not in (RAY, ORCA, PUMPSWAP): return None
    data = unbase58(ix.get('data','')); a = ix['accounts']
    if pid == PUMPSWAP:
        names = ('buy','buy_v2','buy_exact_quote_in','buy_exact_quote_in_v2','sell','sell_v2')
        name = next((n for n in names if data[:8] == hashlib.sha256(('global:'+n).encode()).digest()[:8]),None)
        if name is None: return None
        v2=name.endswith('_v2'); sell=name.startswith('sell')
        # Legacy deployments also accepted omitted trailing OptionBool.
        if len(data) not in ((24,) if v2 or sell else (24,25)) or (len(data)==25 and data[24] not in (0,1)):
            raise ValueError('Unsupported PumpSwap instruction length')
        if (v2 and len(a)!=17) or (not v2 and not (21 if sell else 23)<=len(a)<=32):
            raise ValueError('Unsupported PumpSwap account layout')
        ti=9 if v2 else 11
        if a[ti] not in (TOKEN,TOKEN22) or a[ti+1]!=TOKEN or a[16]!=PUMPSWAP:
            return None
        amount,threshold=struct.unpack('<QQ',data[8:24])
        src,dst=(a[5],a[6]) if sell else (a[6],a[5])
        return dict(protocol='pumpswap',pool=a[0],user=a[1],src=src,dst=dst,
                    vaults=(a[7],a[8]),authority=a[0],amount=amount,threshold=threshold,
                    exactIn=sell or 'exact_quote' in name,pumpName=name,base=a[3],quote=a[4],
                    baseAccount=a[5],baseProgram=a[ti])
    if pid == RAY:
        if len(data) != 17 or data[0] not in (9, 16): return None
        if data[0] == 16 and len(a) == 8:
            token, pool, auth, va, vb, src, dst, user = a
        elif data[0] == 9 and len(a) == 18:
            token, pool, auth = a[:3]; va,vb = a[5:7]; src,dst,user = a[15:18]
        else: return None
        if token != TOKEN: return None
        amount, threshold = struct.unpack('<QQ',data[1:])
        return dict(protocol='raydium-amm',pool=pool,user=user,src=src,dst=dst,
                    vaults=(va,vb),authority=auth,amount=amount,threshold=threshold,exactIn=True)
    if data[:8] != hashlib.sha256(b'global:swap').digest()[:8] or len(data) != 42 or len(a) != 11:
        return None
    if a[0] != TOKEN or data[40] not in (0,1) or data[41] not in (0,1): return None
    amount, threshold = struct.unpack('<QQ',data[8:24])
    src,vin,dst,vout = (a[3],a[4],a[5],a[6]) if data[41] else (a[5],a[6],a[3],a[4])
    return dict(protocol='orca-whirlpool',pool=a[2],user=a[1],src=src,dst=dst,
                vaults=(vin,vout),authority=a[2],amount=amount,threshold=threshold,exactIn=bool(data[40]))


def _validate_pumpswap(tx, ins, accounts, spec, legs, ain, aout):
    """Recognized pool legs, fee-aware bounds, no Token-2022 net inference."""
    sell=spec['pumpName'].startswith('sell')
    src,dst=spec['src'],spec['dst']; base,quote=spec['base'],spec['quote']
    for row in tx['meta']['preTokenBalances']+tx['meta']['postTokenBalances']:
        if row['mint'] in (base,quote) and row.get('programId')!=(spec['baseProgram'] if row['mint']==base else TOKEN):
            raise ValueError('PumpSwap token program mismatch')
    if accounts[src][0] != (base if sell else quote) or accounts[dst][0] != (quote if sell else base):
        raise ValueError('PumpSwap instruction mint mismatch')
    if sell and ain != spec['amount']: raise ValueError('PumpSwap sell amount mismatch')
    if not sell and not spec['exactIn'] and aout != spec['amount']: raise ValueError('PumpSwap buy amount mismatch')
    # Additional transfers are quote fees, never additional purchased tokens.
    qsource=spec['vaults'][1] if sell else src
    for leg in legs:
        if leg['source']==(src if sell else spec['vaults'][0]) and leg['destination']==(spec['vaults'][0] if sell else dst):
            continue
        if leg['source']!=qsource or leg.get('authority')!=(spec['pool'] if sell else spec['user']):
            raise ValueError('Unexpected PumpSwap fee leg')
        if accounts.get(leg['destination'],(None,))[0]!=quote:
            raise ValueError('Unknown PumpSwap fee destination mint')
    if not sell:
        spent=sum(integer(l['amount']) for l in legs if l['source']==src)
        if spent > (spec['amount'] if spec['exactIn'] else spec['threshold']):
            raise ValueError('PumpSwap quote budget exceeded')
    # Token-2022 transfer-fee/hooks can change credited amounts. Reconcile all
    # checked base legs against BOTH user/vault deltas; reject unreconciled cases.
    if spec['baseProgram']==TOKEN22:
        keys=tx['transaction']['message']['accountKeys']
        before={keys[r['accountIndex']]['pubkey']:integer(r['uiTokenAmount']['amount']) for r in tx['meta']['preTokenBalances'] if r['mint']==base}
        after={keys[r['accountIndex']]['pubkey']:integer(r['uiTokenAmount']['amount']) for r in tx['meta']['postTokenBalances'] if r['mint']==base}
        for key in (spec['baseAccount'],spec['vaults'][0]):
            delta=0
            for _,_,ix in ins:
                p=ix.get('parsed',{}); info=p.get('info',{})
                if ix.get('programId')!=TOKEN22 or p.get('type')!='transferChecked' or info.get('mint')!=base:continue
                n=integer(info['tokenAmount']['amount'])
                delta+=n*(int(info['destination']==key)-int(info['source']==key))
            if after.get(key,0)-before.get(key,0)!=delta:
                raise ValueError('Token-2022 base delta does not reconcile; fee/hook or unsupported movement')


def decode_amm_transaction(tx, sig, mint, wallet):
    """Return trades/gaps for an RPC jsonParsed successful receipt, never transfers.

    Raises on invalid caller identifiers; malformed receipts produce a coverage gap.
    Quote WSOL is not a claim about net native SOL balance or USD value.
    """
    address(mint); address(wallet); signature(sig)
    transaction = tx.get('transaction') if isinstance(tx,dict) else None
    signatures = transaction.get('signatures') if isinstance(transaction,dict) else None
    if not isinstance(signatures,list) or not signatures or not isinstance(signatures[0],str):
        return {'trades':[], 'gaps':['Malformed receipt signatures']}
    if signatures[0] != sig:
        raise ValueError('Receipt signature mismatch')
    out = {'trades':[], 'gaps':[]}
    try:
        if tx['meta']['err'] is not None: return out
        if type(tx['slot']) is not int or tx['slot'] < 0: raise ValueError('Invalid slot')
        if tx.get('blockTime') is not None and (type(tx['blockTime']) is not int or tx['blockTime'] < 0):
            raise ValueError('Invalid block time')
        calls = _log_invocations(tx['meta']['logMessages'])
        ins = _instructions(tx); accounts = _accounts(tx, ins)
        # Compare recognized program invocations to receipt instruction order/depth.
        if [(p,h) for p,h in calls if p in (RAY,ORCA,PUMPSWAP)] != [(i['programId'],h) for _,h,i in ins if i.get('programId') in (RAY,ORCA,PUMPSWAP)]:
            raise ValueError('AMM invocation/instruction mismatch')
        signers = [a['pubkey'] for a in tx['transaction']['message']['accountKeys'] if a.get('signer') is True]
        if wallet not in signers: return out
        for pos,(path,depth,ix) in enumerate(ins):
            spec = _swap(ix)
            if not spec or spec['user'] != wallet: continue
            src,dst = spec['src'],spec['dst']
            if src not in accounts or dst not in accounts:
                out['gaps'].append('Unknown user token account metadata at '+path); continue
            im,io,idc = accounts[src]; om,oo,odc = accounts[dst]
            if io != wallet or oo != wallet or im == om or mint not in (im,om): continue
            legs = []
            for _,h,child in ins[pos+1:]:
                if h <= depth: break
                if h != depth+1: continue
                parsed = child.get('parsed',{})
                if child.get('programId') == TOKEN and parsed.get('type') == 'transfer':
                    legs.append(parsed['info'])
                elif spec['protocol']=='pumpswap' and child.get('programId') in (TOKEN,TOKEN22) and parsed.get('type')=='transferChecked':
                    info=dict(parsed['info']); meta=accounts.get(info['source'])
                    if not meta or meta[0]!=info['mint'] or meta[2]!=info['tokenAmount']['decimals']:
                        raise ValueError('Checked transfer metadata mismatch')
                    info['amount']=info['tokenAmount']['amount'];legs.append(info)
            if spec['protocol']!='pumpswap' and len(legs) != 2: out['gaps'].append('Expected exactly two SPL legs at '+path); continue
            incoming = [l for l in legs if l.get('source') == src and l.get('destination') in spec['vaults'] and l.get('authority') == wallet]
            outgoing = [l for l in legs if l.get('destination') == dst and l.get('source') in spec['vaults'] and l.get('authority') == spec['authority']]
            if len(incoming) != 1 or len(outgoing) != 1: raise ValueError('Swap cash/token legs mismatch')
            li,lo = incoming[0],outgoing[0]
            if li['destination'] == lo['source']: raise ValueError('Same swap vault')
            for key,m,d in ((li['destination'],im,idc),(lo['source'],om,odc)):
                if key not in accounts or accounts[key] != (m,spec['authority'],d): raise ValueError('Pool vault metadata mismatch')
            ain,aout = integer(li['amount']),integer(lo['amount'])
            if not ain or not aout: raise ValueError('Zero swap leg')
            if spec['protocol']=='pumpswap':
                _validate_pumpswap(tx,ins,accounts,spec,legs,ain,aout)
            # Orca can partially fill at a price limit; do not require equality.
            if spec['exactIn']:
                if ain > spec['amount'] or aout < spec['threshold']: raise ValueError('Swap input/threshold mismatch')
                if spec['protocol']=='raydium-amm' and ain != spec['amount']: raise ValueError('Raydium input mismatch')
            elif aout > spec['amount'] or ain > spec['threshold']: raise ValueError('Swap output/threshold mismatch')
            buy = om == mint
            token,td,quote,qd,qmint = (aout,odc,ain,idc,im) if buy else (ain,idc,aout,odc,om)
            out['trades'].append({'id':sig+':amm:'+path,'signature':sig,'instructionPath':path,
                'protocol':spec['protocol'],'programId':ix['programId'],'pool':spec['pool'],
                'mint':mint,'wallet':wallet,'side':'buy' if buy else 'sell','tokenAmountRaw':str(token),
                'tokenDecimals':td,'tokenAmount':scaled(token,td),'quoteMint':qmint,
                'quoteAmountRaw':str(quote),'quoteDecimals':qd,'quoteAmount':scaled(quote,qd),
                'quoteUnit':{WSOL:'WSOL',USDC:'USDC'}.get(qmint,qmint),
                'nativeAmount':scaled(quote,qd) if qmint==WSOL else None,
                'nativeUnit':'WSOL' if qmint==WSOL else None,'solAmountRaw':None,'usdAmount':None,
                'slot':tx['slot'],'blockTime':tx.get('blockTime'),'signers':signers,
                'invocationDepth':depth,'url':'https://solscan.io/tx/'+sig,
                'claim':('Verified PumpSwap pool leg; quote excludes separate fee transfers; not net wallet proceeds' if spec['protocol']=='pumpswap' else 'Verified AMM fill; may be one routed leg, not net wallet proceeds'),
                'evidence':{'inputAccount':src,'outputAccount':dst,'inputVault':li['destination'],
                            'outputVault':lo['source'],'validation':'successful swap instruction + exact SPL legs + signer/owner/mint'}})
    except (ValueError,KeyError,TypeError,IndexError,struct.error) as exc:
        out['trades'] = []; out['gaps'].append(str(exc))
    return out


def wallet_history(mint, wallet, limit=10, before=None, transport=None):
    """One bounded exact-wallet signature page, finalized RPC receipts, no API key.

    Separate from Pump history: parent may decode both using the same receipts.
    Reuses the existing allowlisted, byte/time-bounded public transport.
    """
    from monitoring import RPCS
    address(mint); address(wallet)
    if type(limit) is not int or not 1 <= limit <= 10: raise ValueError('limit must be 1..10')
    if before is not None: signature(before)
    client = Holders(transport)
    now = datetime.now(timezone.utc).isoformat()
    out = {'schema':'holder-amm-history.v1','mint':mint,'wallet':wallet,'status':'unavailable',
           'observedAt':now,'source':None,'commitment':'finalized','trades':[],'errors':[],
           'coverage':{'complete':False,'scope':'exact wallet signature page; Raydium AMM exact-in and Orca Whirlpool legacy swap fills',
             'ataExpanded':False,'ammSupported':True,'signatureLimit':limit,'signaturesObserved':0,
             'transactionsFetched':0,'failedTransactions':0,'before':before,'nextBefore':None,'hasMore':None,'gaps':[]}}
    def rpc(endpoint, method, params):
        return client._fetch(endpoint,{'jsonrpc':'2.0','id':1,'method':method,'params':params})
    rows = None
    for endpoint in RPCS:
        try:
            config = {'limit':limit,'commitment':'finalized'}
            if before: config['before'] = before
            candidate = rpc(endpoint,'getSignaturesForAddress',[wallet,config])
            if not isinstance(candidate,list) or len(candidate)>limit: raise ValueError('Invalid signature page')
            seen = set()
            for row in candidate:
                s = signature(row['signature'])
                if s in seen or 'err' not in row: raise ValueError('Invalid/duplicate signature row')
                seen.add(s)
            rows=candidate; out.update(source=endpoint,status='partial'); break
        except (ValueError,KeyError,TypeError,OSError) as exc:
            out['errors'].append({'source':endpoint,'message':str(exc)[:220]})
    if rows is None: return out
    cov=out['coverage']; cov['signaturesObserved']=len(rows); stalled=False
    for row in rows:
        sig=row['signature']
        if row['err'] is not None:
            cov['failedTransactions']+=1
            if not stalled: cov['nextBefore']=sig
            continue
        try:
            tx=rpc(out['source'],'getTransaction',[sig,{'encoding':'jsonParsed','commitment':'finalized','maxSupportedTransactionVersion':1}])
            cov['transactionsFetched']+=1
            decoded=decode_amm_transaction(tx,sig,mint,wallet)
            for event in decoded['trades']:
                event.update(rpcSource=out['source'],observedAt=now,commitment='finalized')
                out['trades'].append(event)
            if decoded['gaps']: raise ValueError('; '.join(decoded['gaps']))
            if not stalled: cov['nextBefore']=sig
        except (ValueError,KeyError,TypeError,OSError) as exc:
            stalled=True; cov['gaps'].append({'signature':sig,'message':str(exc)[:220]})
            if str(exc).startswith(('HTTP 401','HTTP 403','HTTP 429')): break
    return out
