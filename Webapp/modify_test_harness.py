# Simple harness to test modify vs replace GTT logic
from Webapp import app

class FakeKite:
    GTT_TYPE_OCO = 'oco'
    GTT_TYPE_SINGLE = 'single'
    TRANSACTION_TYPE_SELL = 'SELL'
    PRODUCT_CNC = 'CNC'
    ORDER_TYPE_LIMIT = 'LIMIT'

    def __init__(self, mode='modify_ok'):
        self.mode = mode
        self.calls = []

    def modify_gtt(self, **kwargs):
        self.calls.append(('modify_gtt', kwargs))
        if self.mode == 'modify_ok':
            # Return dict with same trigger id to emulate success
            return {'trigger_id': kwargs.get('trigger_id')}
        elif self.mode == 'modify_new_id':
            return {'trigger_id': str(kwargs.get('trigger_id')) + '_updated'}
        else:
            raise RuntimeError('modify failed')

    def delete_gtt(self, gtt_id):
        self.calls.append(('delete_gtt', gtt_id))
        if self.mode == 'delete_fails':
            raise RuntimeError('delete failed')
        return {'status': 'deleted', 'id': gtt_id}

    def place_gtt(self, **kwargs):
        self.calls.append(('place_gtt', kwargs))
        # Emulate a response with trigger id
        return {'trigger_id': 999999}


def run_modify_flow():
    kite = FakeKite(mode='modify_ok')
    st = {'gtt_id': {'trigger_id': 303400699}, 'trigger': 1664.23, 'qty': 1, 'sl_pct': 0.025, 'tick': 0.05}
    sym = 'TECHM'
    ltp = 1666.03
    new_trig = app._round_to_tick(ltp * (1 - app.TRAIL_THRESHOLD), st['tick'])
    new_target = None
    # Simulate modify branch
    normalized_id = app._extract_trigger_id(st.get('gtt_id'))
    print('Before modify:', st)
    try:
        resp = kite.modify_gtt(trigger_id=normalized_id, trigger_type=kite.GTT_TYPE_SINGLE, tradingsymbol=sym, exchange='NSE', trigger_values=[new_trig], last_price=ltp, orders=[])
        new_id = app._extract_trigger_id(resp.get('trigger_id') or resp.get('id') if isinstance(resp, dict) else resp)
        if new_id:
            st['gtt_id'] = new_id
        st['trigger'] = new_trig
        print('Modify success, after:', st)
    except Exception as e:
        print('Modify failed:', e)


def run_replace_flow():
    kite = FakeKite(mode='modify_fail')
    st = {'gtt_id': {'trigger_id': 303500700}, 'trigger': 1664.23, 'qty': 1, 'sl_pct': 0.025, 'tick': 0.05}
    sym = 'TECHM'
    ltp = 1666.03
    new_trig = app._round_to_tick(ltp * (1 - app.TRAIL_THRESHOLD), st['tick'])
    print('Before replace:', st)
    normalized_id = app._extract_trigger_id(st.get('gtt_id'))
    # attempt delete
    try:
        if normalized_id and hasattr(kite, 'delete_gtt'):
            try:
                kite.delete_gtt(normalized_id)
            except Exception as e:
                print('delete failed (ignored):', e)
        new_id = app._place_sl_gtt(kite, sym, st['qty'], ref_price=ltp, sl_pct=st['sl_pct'])
        st['gtt_id'] = new_id
        st['trigger'] = new_trig
        print('Replace success, after:', st)
    except Exception as e:
        print('Replace failed:', e)

if __name__ == '__main__':
    print('Testing modify flow')
    run_modify_flow()
    print('\nTesting replace flow')
    run_replace_flow()
