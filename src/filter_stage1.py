"""
filter_stage1.py — ETF 籌碼標記（第一階）
Annotates each TW stock dict in-place with籌碼 fields from load_data.
All signals are already computed by load_data.py; this module just validates
and surfaces them as the canonical stage-1 output fields.
"""


def run(tw_data):
    """
    tw_data: {symbol: data_dict} as returned by load_data.load_all
    Adds/confirms these keys on each entry (already set by load_data):
        etf_consensus_buy_count, etf_consensus_sell_count,
        is_continuous_buy, is_abnormal_ignition,
        manager_divergence, divergence_net_sign
    Returns the same dict (modified in-place) for pipeline chaining.
    """
    for symbol, d in tw_data.items():
        # Ensure required fields exist (should always be present from load_data)
        d.setdefault("etf_consensus_buy_count", 0)
        d.setdefault("etf_consensus_sell_count", 0)
        d.setdefault("is_continuous_buy", False)
        d.setdefault("is_abnormal_ignition", False)
        d.setdefault("manager_divergence", False)
        d.setdefault("divergence_net_sign", 0)

    return tw_data
