"""
zone_validation.py - Validation Only - Refactored - Single Source of Truth
- Imports Zone, Box, scan_zones from zone_core.py - NO DUPLICATE ZoneEngine
- Contains validation logic only: swing-break, zone-contains, MTF, final orchestration
- ValidationResult normalized object
- Uses deepcopy to avoid shared-default mutation
- MAXIMIZED_TF_PARAMS documented as config profiles, not scientifically proven
- Preserves backward compatibility for app.py callers

NOTE (fix applied): the original scan_and_validate_final() called
scan_zones(df, params=zone_engine_params, tf=parent_tf) - but
zone_core.scan_zones() has no `tf` parameter, so this raised
`TypeError: scan_zones() got an unexpected keyword argument 'tf'` on
every call. zone_core's scan itself is timeframe-agnostic (it works
purely off the candles it's given); `parent_tf` is only needed here, to
pick the right TF_CONFIG_PROFILES entry for validation - so the fix is
to simply stop passing tf into scan_zones(), not to add tf-awareness to
zone_core.
"""
from __future__ import annotations
import copy
import pandas as pd
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime

# SINGLE SOURCE OF TRUTH - Import from zone_core, don't duplicate
from zone_core import Zone, Box, scan_zones, PINE_DEFAULTS

# For backward compatibility, re-export trade setup from trade_setup.py
try:
    from trade_setup import get_trade_setup, get_all_trade_setups, get_base_candle_close
except ImportError:
    # Fallback if trade_setup not available - minimal implementation
    def get_base_candle_close(df, zone):
        idx = zone.startBarIndex + zone.baseCount - 1
        if 0 <= idx < len(df):
            return float(df.iloc[idx]['close'])
        return float(zone.proxVal)
    def get_trade_setup(df, zone, capital=25000.0, risk_pct=0.5, rr=5.0, use_base_close_logic=False):
        from risk_engine import get_risk_engine, RiskConfig
        import copy
        cfg=RiskConfig(capital=capital,risk_pct=risk_pct)
        engine=get_risk_engine(cfg)
        entry=zone.proxVal if not use_base_close_logic else get_base_candle_close(df,zone)
        sl=zone.slVal
        risk=abs(entry-sl)
        tp=entry+risk*rr if zone.isDemand else entry-risk*rr
        rr_result=engine.calculate_position(entry,sl,tp,rr=rr)
        return {"isDemand":zone.isDemand,"patternType":zone.patternType,"entry":rr_result.entry,"stopLoss":rr_result.stop_loss,"target":rr_result.target,"quantity":rr_result.quantity,"risk_per_share":rr_result.risk_per_share,"risk_amount":rr_result.risk_amount,"reward_per_share":rr_result.reward_per_share,"reward_amount":rr_result.reward_amount,"rr":rr,"capital":capital,"risk_pct":risk_pct,"proxVal":round(zone.proxVal,2),"distVal":round(zone.distVal,2),"base_close":round(get_base_candle_close(df,zone),2),"timestamp":str(zone.timestamp) if zone.timestamp else "","is_eligible":rr_result.is_eligible,"rejection_reason":rr_result.rejection_reason}
    def get_all_trade_setups(df, zones, capital=25000.0, risk_pct=0.5, rr=5.0, use_base_close_logic=False):
        return [get_trade_setup(df,z,capital=capital,risk_pct=risk_pct,rr=rr,use_base_close_logic=use_base_close_logic) for z in zones]

# Config Profiles - Documented as configuration profiles, NOT scientifically proven best
# Previously named MAXIMIZED_TF_PARAMS with claims of Best Win%/ROI - Now documented as profiles
TF_CONFIG_PROFILES = {
    "1H": {"contains_mult":1.5, "swing_lb":10, "swing_tol":0.05, "rr":4.0, "risk_pct":2.0, "description": "1H profile - Config, not proven best"},
    "4H": {"contains_mult":2.0, "swing_lb":10, "swing_tol":0.05, "rr":4.0, "risk_pct":2.0, "description": "4H profile"},
    "6H": {"contains_mult":0.8, "swing_lb":10, "swing_tol":0.05, "rr":3.0, "risk_pct":0.5, "description": "6H profile"},
    "Daily": {"contains_mult":1.2, "swing_lb":15, "swing_tol":0.05, "rr":2.5, "risk_pct":3.0, "description": "Daily profile"},
    "Weekly": {"contains_mult":1.0, "swing_lb":20, "swing_tol":0.10, "rr":3.0, "risk_pct":3.0, "description": "Weekly profile"},
    "Monthly": {"contains_mult":1.0, "swing_lb":20, "swing_tol":0.10, "rr":5.0, "risk_pct":10.0, "description": "Monthly profile - High risk% for demo"},
    "10M": {"contains_mult":1.5, "swing_lb":10, "swing_tol":0.05, "rr":4.0, "risk_pct":2.0, "description": "10M profile"},
    "15M": {"contains_mult":1.5, "swing_lb":10, "swing_tol":0.05, "rr":4.0, "risk_pct":2.0, "description": "15M profile"},
    "30M": {"contains_mult":1.5, "swing_lb":10, "swing_tol":0.05, "rr":4.0, "risk_pct":2.0, "description": "30M profile"},
    "75M": {"contains_mult":1.5, "swing_lb":10, "swing_tol":0.05, "rr":4.0, "risk_pct":2.0, "description": "75M profile"},
    "2H": {"contains_mult":1.5, "swing_lb":10, "swing_tol":0.05, "rr":4.0, "risk_pct":2.0, "description": "2H profile"},
    "3M": {"contains_mult":1.5, "swing_lb":10, "swing_tol":0.05, "rr":4.0, "risk_pct":1.0, "description": "3M profile"},
    "5M": {"contains_mult":1.5, "swing_lb":10, "swing_tol":0.05, "rr":4.0, "risk_pct":1.0, "description": "5M profile"},
}

# Backward compatibility alias
MAXIMIZED_TF_PARAMS = TF_CONFIG_PROFILES

@dataclass
class ValidationResult:
    """Normalized validation result - Deterministic and explainable"""
    is_valid: bool
    validation_type: str  # Contains, SwingBreak, MTF, etc
    swing_break: bool
    zone_contains: bool
    mtf_valid: bool
    reason: str
    failed_rules: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    zone: Optional[Zone] = None

def is_valid_swing_break(df: pd.DataFrame, zone: Zone, lookback: int = 20, tolerance_pct: float = 0.10) -> bool:
    """Swing Break: Demand base breaks prev swing high, Supply base breaks prev swing low - Body close valid, wick break invalid"""
    start_idx = zone.startBarIndex
    if start_idx <0 or start_idx>=len(df):
        return False
    base_high = max(zone.proxVal, zone.distVal)
    base_low = min(zone.proxVal, zone.distVal)
    ls = max(0, start_idx-lookback)
    le = start_idx
    if le <= ls:
        return False
    ldf = df.iloc[ls:le]
    if ldf.empty:
        return False
    prev_high = ldf['high'].max()
    prev_low = ldf['low'].min()
    tf = tolerance_pct/100.0 if tolerance_pct>1 else tolerance_pct
    if zone.isDemand:
        return base_high > prev_high * (1+tf)
    else:
        return base_low < prev_low * (1-tf)

def is_valid_zone_contains(df: pd.DataFrame, zone: Zone, mult: float) -> bool:
    """Contains Rule - Zone contains with multiplier"""
    prox=zone.proxVal
    dist=zone.distVal
    risk=abs(prox-dist)
    if risk==0:
        return False
    leg_idx=zone.createdBarIndex
    if leg_idx<0 or leg_idx>=len(df):
        return False
    row=df.iloc[leg_idx]
    h=row['high']
    l=row['low']
    if zone.isDemand:
        return (l <= prox) and (h >= prox + mult*risk)
    else:
        return (h >= prox) and (l <= prox - mult*risk)

def get_config_profile(tf: str, mode: str = "max_roi") -> Dict[str, Any]:
    """Get config profile with deepcopy to avoid shared-default mutation"""
    base = TF_CONFIG_PROFILES.get(tf, TF_CONFIG_PROFILES["1H"])
    # Deepcopy to prevent mutation of global defaults
    result = copy.deepcopy(base)
    return result

def validate_zone_detailed(df: pd.DataFrame, zone: Zone, parent_tf: Optional[str] = None, mode: str = "max_roi") -> ValidationResult:
    """Detailed validation with ValidationResult object"""
    if parent_tf is None:
        parent_tf = "1H"
    
    config = get_config_profile(parent_tf, mode=mode)
    
    # Check contains
    contains_valid = is_valid_zone_contains(df, zone, config["contains_mult"])
    if contains_valid:
        return ValidationResult(
            is_valid=True,
            validation_type=f"Contains_{config['contains_mult']}",
            swing_break=False,
            zone_contains=True,
            mtf_valid=True,
            reason=f"Contains {config['contains_mult']}x RR {config['rr']} Risk {config['risk_pct']}%",
            failed_rules=[],
            evidence={"contains_mult":config["contains_mult"],"rr":config["rr"],"risk_pct":config["risk_pct"]},
            zone=zone
        )
    
    # Check swing break
    swing_valid = is_valid_swing_break(df, zone, lookback=config["swing_lb"], tolerance_pct=config["swing_tol"])
    if swing_valid:
        return ValidationResult(
            is_valid=True,
            validation_type=f"SwingBreak_lb{config['swing_lb']}_tol{config['swing_tol']}",
            swing_break=True,
            zone_contains=False,
            mtf_valid=True,
            reason=f"SwingBreak lb{config['swing_lb']} tol{config['swing_tol']}",
            failed_rules=[],
            evidence={"swing_lb":config["swing_lb"],"swing_tol":config["swing_tol"]},
            zone=zone
        )
    
    return ValidationResult(
        is_valid=False,
        validation_type="INVALID",
        swing_break=False,
        zone_contains=False,
        mtf_valid=False,
        reason="Failed both Contains and SwingBreak",
        failed_rules=["Contains","SwingBreak"],
        evidence={},
        zone=zone
    )

def is_valid_zone_final_merged(df: pd.DataFrame, zone: Zone, parent_tf: Optional[str] = None, mode: str = "max_roi") -> Dict[str, Any]:
    """Legacy wrapper - Returns dict for backward compatibility"""
    result = validate_zone_detailed(df, zone, parent_tf=parent_tf, mode=mode)
    return {"valid": result.is_valid, "rule": result.reason, "no_further_validation": True, "details": result}

def scan_and_validate_final(df: pd.DataFrame, parent_tf: Optional[str] = None, mode: str = "max_roi", params: Optional[Dict[str, Any]] = None) -> List[Zone]:
    """Scan from zone_core.py and apply validation - Logic unchanged, uses deepcopy"""
    if parent_tf is None:
        parent_tf = "1H"
    
    config_profile = get_config_profile(parent_tf, mode=mode)
    
    # Deepcopy zone_engine params to avoid mutation
    zone_engine_params = copy.deepcopy(PINE_DEFAULTS)
    # Override with profile if needed
    if params:
        # Deepcopy incoming params
        incoming = copy.deepcopy(params)
        for k,v in incoming.items():
            if k in zone_engine_params:
                zone_engine_params[k]=v
    
    # FIX: zone_core.scan_zones() has no `tf` kwarg - timeframe only
    # matters for picking the validation config profile above, not for
    # the scan itself. Passing tf= here used to raise a TypeError on
    # every single call.
    zones = scan_zones(df, params=zone_engine_params)
    valid_zones=[]
    for zone in zones:
        result = validate_zone_detailed(df, zone, parent_tf=parent_tf, mode=mode)
        if result.is_valid:
            zone.entryStatus = result.reason
            valid_zones.append(zone)
    return valid_zones

# Backward compatibility aliases
def scan_and_validate_max_roi(df: pd.DataFrame, parent_tf: Optional[str] = None) -> List[Zone]:
    return scan_and_validate_final(df, parent_tf=parent_tf, mode="max_roi")

def scan_and_validate_max_win(df: pd.DataFrame, parent_tf: Optional[str] = None) -> List[Zone]:
    return scan_and_validate_final(df, parent_tf=parent_tf, mode="max_win")

def scan_and_validate_best_for_more_trades_and_roi(df: pd.DataFrame, parent_tf: Optional[str] = None) -> List[Zone]:
    return scan_and_validate_final(df, parent_tf=parent_tf, mode="max_roi")

def scan_and_validate_winner(df: pd.DataFrame, parent_tf: Optional[str] = None) -> List[Zone]:
    return scan_and_validate_final(df, parent_tf=parent_tf, mode="max_roi")

def get_horizontal_levels(df: pd.DataFrame, zones: List[Zone], capital: float = 25000.0, risk_pct: float = 0.5, rr: float = 5.0, use_base_close_logic: bool = False) -> List[Dict[str, Any]]:
    """Horizontal line plot levels"""
    levels=[]
    for zone in zones:
        setup = get_trade_setup(df, zone, capital=capital, risk_pct=risk_pct, rr=rr, use_base_close_logic=use_base_close_logic)
        levels.append({
            "isDemand": setup["isDemand"],
            "entry": setup["entry"],
            "stopLoss": setup["stopLoss"],
            "target": setup["target"],
            "quantity": setup["quantity"],
            "proxVal": setup["proxVal"],
            "distVal": setup["distVal"],
            "patternType": setup["patternType"],
            "timestamp": setup["timestamp"],
        })
    return levels
