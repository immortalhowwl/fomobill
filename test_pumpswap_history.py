import copy,json,pathlib,unittest
from holder_history_sources import decode_amm_transaction
P=pathlib.Path(__file__).parent/'tests/fixtures/pumpswap'
class PumpSwapTests(unittest.TestCase):
 def load(self,name):return json.loads((P/(name+'.json')).read_text())['result']
 def decode(self,t):
  return decode_amm_transaction(t,t['transaction']['signatures'][0],self.mint,self.wallet)
 def test_current_holder_buy(self):
  self.mint='GetLnVcK3csLW6aTb5mBTHaw9V4vN3fx9oMWQrQxpump';self.wallet='5xLueovn3TiU6hdXnEXKAzTWkJdoj6HRv3ymXgUsgdaH'
  t=self.load('buy');r=self.decode(t)
  self.assertEqual(len(r['trades']),1,r)
  x=r['trades'][0];self.assertEqual((x['side'],x['tokenAmountRaw'],x['quoteAmountRaw']),('buy','12469979354336','12456598054'))
  self.assertEqual(x['protocol'],'pumpswap');self.assertIsNone(x['usdAmount'])
 def test_second_token_holder_sell_v2(self):
  self.mint='6hjaMcRPvB4DEGpHqgHxmgipb2vrGQWMewZBAVSLpump';self.wallet='VJSDW6S74YXR4rRR9P4xwhMvLZJQMhrUb8XMFirUsy1'
  r=self.decode(self.load('sell'));self.assertEqual(len(r['trades']),1,r)
  x=r['trades'][0];self.assertEqual((x['side'],x['tokenAmountRaw'],x['quoteAmountRaw']),('sell','2061352557908','5007446580'))
 def test_real_additional_variants(self):
  from holder_history_sources import PUMPSWAP
  for name in ['buy-v2','exact-in','exact-in-v2']:
   with self.subTest(name=name):
    t=self.load(name);ins=t['transaction']['message']['instructions']+[i for g in t['meta']['innerInstructions'] for i in g['instructions']]
    ix=next(i for i in ins if i.get('programId')==PUMPSWAP and len(i.get('accounts',[]))>10)
    self.mint=ix['accounts'][3];self.wallet=ix['accounts'][1]
    r=self.decode(t);self.assertEqual(len(r['trades']),1,r);self.assertEqual(r['trades'][0]['side'],'buy')
 def test_fail_closed_receipt_mutations(self):
  self.mint='GetLnVcK3csLW6aTb5mBTHaw9V4vN3fx9oMWQrQxpump';self.wallet='5xLueovn3TiU6hdXnEXKAzTWkJdoj6HRv3ymXgUsgdaH'
  for kind in ['failed','truncated','amount','owner','mint','transfer_only','token_fee','wrong_program']:
   with self.subTest(kind=kind):
    t=self.load('buy');ix=t['transaction']['message']['instructions'][6]
    if kind=='failed':t['meta']['err']={'InstructionError':[6,'failure']}
    if kind=='truncated':t['meta']['logMessages'].pop()
    if kind=='amount':t['meta']['innerInstructions'][-1]['instructions'][1]['parsed']['info']['tokenAmount']['amount']='1'
    if kind=='owner':self.wallet='VJSDW6S74YXR4rRR9P4xwhMvLZJQMhrUb8XMFirUsy1'
    if kind=='mint':ix['accounts'][3]='So11111111111111111111111111111111111111112'
    if kind=='transfer_only':ix['data']='1'
    if kind=='token_fee':t['meta']['postTokenBalances'][1]['uiTokenAmount']['amount']='12469979354335'
    if kind=='wrong_program':ix['accounts'][11]='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
    self.assertEqual(self.decode(t)['trades'],[],kind)
    self.wallet='5xLueovn3TiU6hdXnEXKAzTWkJdoj6HRv3ymXgUsgdaH'
if __name__=='__main__':unittest.main()
