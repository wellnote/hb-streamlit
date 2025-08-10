import os
import time
import math
import requests
import pandas as pd
import streamlit as st

# ---------- Settings / Secrets ----------
API_BASE = st.secrets.get("API_BASE", os.getenv("API_BASE", "")).rstrip("/")
API_TOKEN = st.secrets.get("API_TOKEN", os.getenv("API_TOKEN", ""))  # optional
VERIFY_SSL = st.secrets.get("VERIFY_SSL", True)

HEADERS = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}

# ---------- Small helpers ----------
@st.cache_data(ttl=10)
def api_get(path: str, params=None):
    """GET with graceful fallback and caching."""
    if not API_BASE:
        raise RuntimeError("API_BASE is empty. Add it to Streamlit Secrets.")
    url = f"{API_BASE}/{path.lstrip('/')}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=8, verify=VERIFY_SSL)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        # bubble up 404/401 etc with readable message
        raise RuntimeError(f"HTTP {r.status_code} for {url}: {r.text}") from e
    except Exception as e:
        raise RuntimeError(f"Request failed: {url} -> {e}") from e

def df_safe(obj, columns=None):
    """Turn API response (list/dict) into DataFrame nicely."""
    if obj is None:
        return pd.DataFrame()
    if isinstance(obj, dict):
        # common shape: {"data":[...]} / {"result":[...]} / flat dict
        for key in ("data", "result", "items"):
            if key in obj and isinstance(obj[key], list):
                return pd.DataFrame(obj[key])
        return pd.DataFrame([obj])
    if isinstance(obj, list):
        return pd.DataFrame(obj)
    return pd.DataFrame()

def kfmt(x):
    if x is None or pd.isna(x):
        return "-"
    try:
        x = float(x)
    except Exception:
        return str(x)
    if abs(x) >= 1_000_000:
        return f"{x/1_000_000:.2f}M"
    if abs(x) >= 1_000:
        return f"{x/1_000:.2f}K"
    return f"{x:.2f}"

# ---------- UI ----------
st.set_page_config(page_title="HB Live Dashboard", layout="wide")
st.title("Hummingbot — Live Dashboard")

# Sidebar controls
with st.sidebar:
    st.subheader("Connection")
    st.write(f"API: `{API_BASE or 'not set'}`")
    refresh_sec = st.slider("Auto-refresh (sec)", 3, 60, 10, help="Refresh interval for live data")
    st.checkbox("Verify SSL", value=VERIFY_SSL, disabled=True)
    st.markdown("---")
    st.caption("Tips: set API_BASE and API_TOKEN in Streamlit **Secrets**.")
    st.caption("If your API is local, expose it via ngrok/cloudflared for the Cloud app.")

# ---------- Health ----------
col1, col2, col3, col4 = st.columns(4)
try:
    health = api_get("/health")
    col1.metric("API status", "OK", help=str(health))
except Exception as e:
    col1.metric("API status", "ERROR")
    st.error(e)
    st.stop()

# ---------- Bots / Instances ----------
# Try both shapes: /bots and /instances
bots_json = None
instances_json = None
err_bots = err_inst = None
try:
    bots_json = api_get("/bots")
except Exception as e:
    err_bots = str(e)

try:
    instances_json = api_get("/instances")
except Exception as e:
    err_inst = str(e)

bots_df = df_safe(bots_json)
inst_df = df_safe(instances_json)

running_col = col2
bots_running = 0
if not bots_df.empty:
    # guess status fields
    status_col = next((c for c in bots_df.columns if c.lower() in ("status", "state", "running")), None)
    if status_col:
        bots_running = int(bots_df[status_col].astype(str).str.contains("run|true|active|online", case=False).sum())
    else:
        bots_running = len(bots_df)
    running_col.metric("Bots (running)", bots_running, help=f"{len(bots_df)} total")
elif not inst_df.empty:
    status_col = next((c for c in inst_df.columns if c.lower() in ("status", "state", "running")), None)
    if status_col:
        bots_running = int(inst_df[status_col].astype(str).str.contains("run|true|active|online", case=False).sum())
    else:
        bots_running = len(inst_df)
    running_col.metric("Instances (running)", bots_running, help=f"{len(inst_df)} total")
else:
    running_col.metric("Bots", "0")
    st.info("No bots/instances endpoint found. If your API differs, tell me the paths.")

# ---------- PnL ----------
pnl_df = pd.DataFrame()
pnl_err = None
for path in ("/pnl", "/stats/pnl", "/performance/pnl"):
    try:
        raw = api_get(path)
        pnl_df = df_safe(raw)
        if not pnl_df.empty:
            break
    except Exception as e:
        pnl_err = str(e)

if not pnl_df.empty:
    # Try to detect columns
    maybe_cols = {
        "ts": ["timestamp", "time", "ts", "date"],
        "pnl": ["pnl", "net_pnl", "profit", "netProfit"],
        "fees": ["fees", "fee", "commission"],
        "vol": ["volume", "quote_volume", "turnover"],
        "equity": ["equity", "balance", "net_worth"],
    }
    def pick(df, names):
        for n in names:
            if n in df.columns:
                return n
        return None

    ts_col = pick(pnl_df, maybe_cols["ts"])
    pnl_col = pick(pnl_df, maybe_cols["pnl"])
    vol_col = pick(pnl_df, maybe_cols["vol"])

    pnl_latest = pnl_df[pnl_col].iloc[-1] if pnl_col else None
    vol_latest = pnl_df[vol_col].iloc[-1] if vol_col else None
    col3.metric("Latest PnL", kfmt(pnl_latest))
    col4.metric("Turnover", kfmt(vol_latest))

    with st.expander("PnL table", expanded=False):
        st.dataframe(pnl_df.tail(200), use_container_width=True)
else:
    col3.metric("Latest PnL", "-")
    if pnl_err:
        st.caption(f"PnL fetch error: {pnl_err}")

# ---------- Tabs: Orders / Trades / Bots ----------
tabs = st.tabs(["🔔 Orders", "🧾 Trades", "🤖 Bots / Instances"])

with tabs[0]:
    orders_df = pd.DataFrame()
    orders_err = None
    for path in ("/orders/open", "/orders", "/active_orders", "/bots/orders"):
        try:
            raw = api_get(path)
            orders_df = df_safe(raw)
            if not orders_df.empty:
                break
        except Exception as e:
            orders_err = str(e)

    if orders_df.empty:
        st.info("No orders found (or endpoint unknown).")
        if orders_err:
            st.caption(f"Orders error: {orders_err}")
    else:
        # pretty subset if present
        cols_pref = [c for c in ["id","bot_id","pair","symbol","side","price","size","status","created_at"] if c in orders_df.columns]
        st.dataframe(orders_df[cols_pref] if cols_pref else orders_df, use_container_width=True)

with tabs[1]:
    trades_df = pd.DataFrame()
    trades_err = None
    for path in ("/trades", "/executions", "/fills"):
        try:
            raw = api_get(path, params={"limit": 500})
            trades_df = df_safe(raw)
            if not trades_df.empty:
                break
        except Exception as e:
            trades_err = str(e)

    if trades_df.empty:
        st.info("No trades found (or endpoint unknown).")
        if trades_err:
            st.caption(f"Trades error: {trades_err}")
    else:
        cols_pref = [c for c in ["id","bot_id","pair","symbol","side","price","qty","fee","pnl","ts","timestamp"] if c in trades_df.columns]
        st.dataframe(trades_df[cols_pref] if cols_pref else trades_df, use_container_width=True)

with tabs[2]:
    if not bots_df.empty:
        st.subheader("Bots")
        st.dataframe(bots_df, use_container_width=True)
    if not inst_df.empty:
        st.subheader("Instances")
        st.dataframe(inst_df, use_container_width=True)

# ---------- Auto refresh ----------
st.caption("Auto-refresh is on.")
time.sleep(refresh_sec)
st.rerun()
