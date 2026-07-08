from __future__ import annotations

import copy
import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import yfinance as yf

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except Exception:  # pragma: no cover - handled in the UI at runtime.
    firebase_admin = None
    credentials = None
    firestore = None


DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vQv9goZ9xZ2mJDFu5cmQDlXEtTsfm1D5fMmW8kwVghxQuOosh3k-0q_w3u7h5lBO7f6_KVR988NQzOj/"
    "pub?gid=636992052&single=true&output=csv"
)
DEFAULT_BASE_TOTAL = 22_495_000
DEFAULT_PROFILE_ID = "personal"

DEFAULT_ASSET_ROWS = [
    ("위험자산", "선진국", "KRX:379800", "KODEX 미국 S&P500TR", 2_699_400, 26_010),
    ("위험자산", "선진국", "KRX:379810", "KODEX 미국 나스닥100", 2_699_400, 29_835),
    ("위험자산", "신흥국", "KRX:294400", "Kiwoom 200 TR", 1_799_600, 161_975),
    ("위험자산", "신흥국", "KRX:283580", "KODEX 차이나CSI300", 1_799_600, 17_385),
    ("위험자산", "신흥국", "KRX:453810", "KODEX 인도 NIFTY50", 1_799_600, 13_225),
    ("대체투자", "금", "KRX:0072R0", "TIGER KRX금현물", 1_730_960, 13_340),
    ("대체투자", "금", "KRX:411060", "ACE KRX금현물", 2_768_040, 27_960),
    ("안전자산", "한국 국채", "KRX:148070", "KIWOOM 국고채10년", 1_574_650, 104_810),
    ("안전자산", "한국 국채", "KRX:385560", "RISE KIS 국고채30년 Enhanced", 1_574_650, 53_270),
    ("안전자산", "미국 국채", "KRX:0085P0", "ACE 미국10년 국채액티브", 1_574_650, 11_010),
    ("안전자산", "미국 국채", "KRX:476760", "ACE 미국30년 국채액티브", 1_574_650, 10_230),
    ("현금성 자산", "단기금리", "KRX:469830", "SOL 초단기 액티브", 899_800, 54_075),
]


def default_assets() -> list[dict[str, Any]]:
    return [
        {
            "id": ticker,
            "category": category,
            "group": group,
            "ticker": ticker,
            "name": name,
            "targetWeight": target_value / DEFAULT_BASE_TOTAL,
            "currentShares": 0.0,
            "fallbackPrice": float(fallback_price),
            "manualPrice": None,
        }
        for category, group, ticker, name, target_value, fallback_price in DEFAULT_ASSET_ROWS
    ]


def default_account() -> dict[str, Any]:
    return {
        "accountName": "연금저축",
        "totalBalance": float(DEFAULT_BASE_TOTAL),
        "principal": 0.0,
        "cash": 0.0,
        "sheetUrl": DEFAULT_SHEET_URL,
        "assets": default_assets(),
    }


def to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    text = text.replace(",", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def to_optional_float(value: Any) -> float | None:
    number = to_float(value, 0.0)
    return number if number > 0 else None


def clean_text(value: Any) -> str:
    return str(value or "").replace("\ufeff", "").strip()


def format_krw(value: float) -> str:
    return f"{value:,.0f}원"


def format_percent(value: float) -> str:
    return f"{value * 100:,.2f}%"


def sanitize_profile_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9가-힣_.-]+", "-", value.strip())
    return cleaned[:80] or DEFAULT_PROFILE_ID


def normalize_asset(asset: dict[str, Any]) -> dict[str, Any]:
    ticker = clean_text(asset.get("ticker") or asset.get("id"))
    return {
        "id": ticker,
        "category": clean_text(asset.get("category")) or "기타",
        "group": clean_text(asset.get("group")) or clean_text(asset.get("category")) or "기타",
        "ticker": ticker,
        "name": clean_text(asset.get("name")) or ticker,
        "targetWeight": to_float(asset.get("targetWeight")),
        "currentShares": to_float(asset.get("currentShares")),
        "fallbackPrice": to_optional_float(asset.get("fallbackPrice")),
        "manualPrice": to_optional_float(asset.get("manualPrice")),
    }


def normalize_account(raw: dict[str, Any] | None) -> dict[str, Any]:
    fallback = default_account()
    if not raw:
        return fallback

    assets_by_ticker = {asset["ticker"]: asset for asset in fallback["assets"]}
    for asset in raw.get("assets", []):
        normalized = normalize_asset(asset)
        if normalized["ticker"]:
            assets_by_ticker[normalized["ticker"]] = {
                **assets_by_ticker.get(normalized["ticker"], {}),
                **normalized,
            }

    return {
        "accountName": clean_text(raw.get("accountName")) or fallback["accountName"],
        "totalBalance": to_float(raw.get("totalBalance"), fallback["totalBalance"]),
        "principal": to_float(raw.get("principal")),
        "cash": to_float(raw.get("cash")),
        "sheetUrl": clean_text(raw.get("sheetUrl")) or fallback["sheetUrl"],
        "assets": [normalize_asset(asset) for asset in assets_by_ticker.values()],
    }


def read_secret_section(name: str) -> dict[str, Any] | None:
    try:
        section = st.secrets.get(name)
    except Exception:
        return None
    if not section:
        return None
    return {key: value for key, value in section.items()}


@st.cache_resource(show_spinner=False)
def get_firestore_client() -> Any | None:
    if firebase_admin is None or credentials is None:
        return None

    service_account = read_secret_section("firebase_service_account")
    app_name = "streamlit-firestore-rebalancer"

    if app_name in firebase_admin._apps:
        app = firebase_admin.get_app(app_name)
        return firestore.client(app)

    if service_account:
        service_account["private_key"] = service_account.get("private_key", "").replace("\\n", "\n")
        cred = credentials.Certificate(service_account)
        app = firebase_admin.initialize_app(cred, name=app_name)
        return firestore.client(app)

    json_secret = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if json_secret:
        service_account = json.loads(json_secret)
        service_account["private_key"] = service_account.get("private_key", "").replace("\\n", "\n")
        cred = credentials.Certificate(service_account)
        app = firebase_admin.initialize_app(cred, name=app_name)
        return firestore.client(app)

    local_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if local_file:
        cred = credentials.Certificate(local_file)
        app = firebase_admin.initialize_app(cred, name=app_name)
        return firestore.client(app)

    return None


def account_ref(db: Any, profile_id: str) -> Any:
    return (
        db.collection("streamlit_accounts")
        .document(profile_id)
        .collection("accounts")
        .document("default")
    )


def load_account(db: Any | None, profile_id: str) -> dict[str, Any]:
    if db is None:
        return default_account()
    snapshot = account_ref(db, profile_id).get()
    return normalize_account(snapshot.to_dict() if snapshot.exists else None)


def save_account(db: Any | None, profile_id: str, account: dict[str, Any]) -> None:
    if db is None:
        return
    payload = normalize_account(account)
    payload["updatedAt"] = firestore.SERVER_TIMESTAMP
    account_ref(db, profile_id).set(payload, merge=True)


def password_gate() -> bool:
    configured_password = ""
    try:
        configured_password = str(st.secrets.get("APP_PASSWORD", ""))
    except Exception:
        configured_password = ""

    if not configured_password:
        st.sidebar.warning("APP_PASSWORD가 없어 현재 배포는 링크를 아는 사람이 열 수 있습니다.")
        return True

    if st.session_state.get("password_ok"):
        return True

    st.sidebar.subheader("접근 보호")
    password = st.sidebar.text_input("앱 비밀번호", type="password")
    if st.sidebar.button("잠금 해제", use_container_width=True):
        if password == configured_password:
            st.session_state["password_ok"] = True
            st.rerun()
        st.sidebar.error("비밀번호가 맞지 않습니다.")
    return False


def fetch_sheet_csv(url: str) -> str:
    # pandas handles Google Sheets published CSV URLs cleanly and works well on Streamlit Cloud.
    with pd.io.common.urlopen(url) as response:
        return response.read().decode("utf-8-sig")


def row_get(row: list[str], index: int) -> str:
    if 0 <= index < len(row):
        return row[index]
    return ""


def find_header_index(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows):
        if (
            any(clean_text(cell) == "구분" for cell in row)
            and any(clean_text(cell) == "티커" for cell in row)
            and any("현재가" in clean_text(cell) for cell in row)
        ):
            return index
    raise ValueError("시트에서 포트폴리오 헤더를 찾지 못했습니다.")


def find_column(header: list[str], keyword: str) -> int:
    for index, cell in enumerate(header):
        if keyword in clean_text(cell):
            return index
    return 0


def find_first_money(row: list[str]) -> float:
    for cell in row:
        value = to_float(cell)
        if value > 0:
            return value
    return 0.0


def detect_account_name(rows: list[list[str]], header_index: int) -> str:
    for row in rows[:header_index]:
        for cell in row:
            text = clean_text(cell)
            if "연금" in text and len(text) <= 30:
                return text
    return "연금저축"


def find_principal_and_current(rows: list[list[str]], header_index: int) -> tuple[float, float | None]:
    for index in range(header_index, min(len(rows) - 1, header_index + 30)):
        row = rows[index]
        principal_index = next(
            (cell_index for cell_index, cell in enumerate(row) if clean_text(cell) == "원금"),
            -1,
        )
        if principal_index >= 0:
            next_row = rows[index + 1]
            principal = to_float(row_get(next_row, principal_index))
            current_value = to_float(row_get(next_row, principal_index + 1))
            return principal, current_value
    return 0.0, None


def parse_sheet_portfolio(csv_text: str) -> dict[str, Any]:
    rows = list(csv.reader(io.StringIO(csv_text)))
    header_index = find_header_index(rows)
    header = rows[header_index]

    ticker_index = find_column(header, "티커")
    name_index = find_column(header, "상품")
    target_value_index = find_column(header, "총자산 분배")
    price_index = find_column(header, "현재가")
    holding_index = find_column(header, "보유 수량")

    total_balance = find_first_money(rows[header_index + 1]) or DEFAULT_BASE_TOTAL
    account_name = detect_account_name(rows, header_index)
    assets: list[dict[str, Any]] = []
    last_category = ""
    last_group = ""

    for row in rows[header_index + 2 :]:
        if not row:
            continue
        if any(clean_text(cell) == "구분" for cell in row) and any(clean_text(cell) == "티커" for cell in row):
            break
        if any("사용법" in clean_text(cell) for cell in row):
            break

        ticker = clean_text(row_get(row, ticker_index))
        ticker_ok = ticker.startswith("KRX:") or ticker == "현금" or bool(re.match(r"^[A-Z0-9.-]+$", ticker))
        if not ticker or not ticker_ok:
            continue

        category = clean_text(row_get(row, 0)) or last_category or "기타"
        group = clean_text(row_get(row, 1)) or last_group or category
        last_category = category
        last_group = group

        target_value = (
            to_float(row_get(row, price_index - 1))
            or to_float(row_get(row, target_value_index))
            or to_float(row_get(row, target_value_index + 1))
        )
        fallback_price = to_optional_float(row_get(row, price_index))
        current_shares = to_float(row_get(row, holding_index))

        assets.append(
            {
                "id": ticker,
                "category": category,
                "group": group,
                "ticker": ticker,
                "name": clean_text(row_get(row, name_index)) or ticker,
                "targetWeight": target_value / total_balance if total_balance else 0.0,
                "currentShares": current_shares,
                "fallbackPrice": fallback_price,
                "manualPrice": None,
            }
        )

    principal, current_value = find_principal_and_current(rows, header_index)
    holdings_value = sum(to_float(asset["currentShares"]) * to_float(asset["fallbackPrice"]) for asset in assets)
    cash = max(0.0, (current_value or 0.0) - holdings_value) if current_value else 0.0

    return normalize_account(
        {
            "accountName": account_name,
            "totalBalance": total_balance,
            "principal": principal,
            "cash": cash,
            "sheetUrl": DEFAULT_SHEET_URL,
            "assets": assets,
        }
    )


def yahoo_symbols_for_ticker(ticker: str) -> list[str]:
    ticker = clean_text(ticker)
    if ticker == "현금":
        return ["CASH"]
    if ticker.startswith("KRX:"):
        code = ticker.replace("KRX:", "").strip()
        return [f"{code}.KS", f"{code}.KQ"]
    return [ticker]


def read_fast_price(symbol: str) -> float | None:
    ticker = yf.Ticker(symbol)
    try:
        fast_info = ticker.fast_info
        price = fast_info.get("last_price") if hasattr(fast_info, "get") else fast_info["last_price"]
        if price and float(price) > 0:
            return float(price)
    except Exception:
        pass

    for period, interval in [("1d", "1m"), ("5d", "1d")]:
        try:
            history = ticker.history(period=period, interval=interval, auto_adjust=False)
            if not history.empty and "Close" in history:
                close = history["Close"].dropna()
                if not close.empty and float(close.iloc[-1]) > 0:
                    return float(close.iloc[-1])
        except Exception:
            continue
    return None


def naver_code_for_ticker(ticker: str) -> str | None:
    ticker = clean_text(ticker)
    if ticker.startswith("KRX:"):
        return ticker.replace("KRX:", "").strip()
    if re.fullmatch(r"[0-9A-Z]{6}", ticker):
        return ticker
    return None


def parse_market_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def find_naver_quote_object(payload: Any, code: str) -> dict[str, Any] | None:
    stack = [payload]
    while stack:
        value = stack.pop()
        if not isinstance(value, (dict, list)):
            continue
        if isinstance(value, dict):
            item_code = value.get("itemCode") or value.get("stockCode") or value.get("code") or value.get("cd")
            has_price = (
                value.get("closePrice")
                or value.get("currentPrice")
                or value.get("tradePrice")
                or value.get("now")
                or value.get("nv")
                or value.get("lastPrice")
            )
            if has_price and (not item_code or str(item_code) == str(code)):
                return value
            stack.extend(child for child in value.values() if isinstance(child, (dict, list)))
        else:
            stack.extend(child for child in value if isinstance(child, (dict, list)))
    return None


def read_naver_price(code: str) -> float | None:
    urls = [
        f"https://api.stock.naver.com/stock/{code}/basic",
        f"https://m.stock.naver.com/api/stock/{code}/basic",
        f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}",
    ]
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0",
    }

    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=8)
            response.raise_for_status()
            quote = find_naver_quote_object(response.json(), code)
            if not quote:
                continue
            price = parse_market_number(
                quote.get("closePrice")
                or quote.get("currentPrice")
                or quote.get("tradePrice")
                or quote.get("now")
                or quote.get("nv")
                or quote.get("lastPrice")
            )
            if price and price > 0:
                return price
        except Exception:
            continue
    return None


@st.cache_data(ttl=60, show_spinner=False)
def fetch_price_snapshot(tickers: tuple[str, ...], refresh_key: int) -> dict[str, dict[str, Any]]:
    del refresh_key
    snapshot: dict[str, dict[str, Any]] = {}

    for ticker in tickers:
        errors: list[str] = []
        for symbol in yahoo_symbols_for_ticker(ticker):
            if symbol == "CASH":
                snapshot[ticker] = {
                    "ok": True,
                    "price": 1.0,
                    "source": "현금",
                    "symbol": symbol,
                    "asOf": datetime.now(timezone.utc).isoformat(),
                }
                break
            try:
                price = read_fast_price(symbol)
                if price and price > 0:
                    snapshot[ticker] = {
                        "ok": True,
                        "price": price,
                        "source": "Yahoo",
                        "symbol": symbol,
                        "asOf": datetime.now(timezone.utc).isoformat(),
                    }
                    break
                errors.append(f"{symbol}: no price")
            except Exception as error:
                errors.append(f"{symbol}: {error}")
        else:
            naver_code = naver_code_for_ticker(ticker)
            if naver_code:
                naver_price = read_naver_price(naver_code)
                if naver_price and naver_price > 0:
                    snapshot[ticker] = {
                        "ok": True,
                        "price": naver_price,
                        "source": "Naver",
                        "symbol": naver_code,
                        "asOf": datetime.now(timezone.utc).isoformat(),
                    }
                    continue
                errors.append(f"Naver {naver_code}: no price")
            snapshot[ticker] = {"ok": False, "price": None, "source": "없음", "error": " / ".join(errors)}

    return snapshot


def resolve_price(asset: dict[str, Any], live_price: dict[str, Any] | None) -> tuple[float, str]:
    manual_price = to_float(asset.get("manualPrice"))
    if manual_price > 0:
        return manual_price, "수동"
    if live_price and live_price.get("ok") and to_float(live_price.get("price")) > 0:
        symbol = live_price.get("symbol") or ""
        return to_float(live_price.get("price")), f"{live_price.get('source', '실시간')} {symbol}".strip()
    fallback_price = to_float(asset.get("fallbackPrice"))
    if fallback_price > 0:
        return fallback_price, "시트"
    return 0.0, "없음"


def build_model(account: dict[str, Any], prices: dict[str, dict[str, Any]]) -> tuple[pd.DataFrame, dict[str, float]]:
    rows = []
    for asset in account["assets"]:
        price, source = resolve_price(asset, prices.get(asset["ticker"]))
        current_shares = to_float(asset.get("currentShares"))
        current_value = current_shares * price
        rows.append(
            {
                "구분": asset["category"],
                "분류": asset["group"],
                "티커": asset["ticker"],
                "상품": asset["name"],
                "목표비중": to_float(asset.get("targetWeight")),
                "현재가": price,
                "가격출처": source,
                "보유수량": current_shares,
                "평가금액": current_value,
            }
        )

    df = pd.DataFrame(rows)
    holdings_value = float(df["평가금액"].sum()) if not df.empty else 0.0
    current_value = holdings_value + to_float(account.get("cash"))
    target_base = to_float(account.get("totalBalance")) or current_value

    if df.empty:
        metrics = {
            "holdingsValue": 0.0,
            "currentValue": current_value,
            "targetBase": target_base,
            "principal": to_float(account.get("principal")),
            "profit": 0.0,
            "returnRate": 0.0,
            "totalTargetWeight": 0.0,
            "buyValue": 0.0,
            "sellValue": 0.0,
            "netTradeValue": 0.0,
        }
        return df, metrics

    df["현재비중"] = df["평가금액"] / current_value if current_value > 0 else 0.0
    df["목표금액"] = target_base * df["목표비중"]
    df["목표수량"] = (df["목표금액"] / df["현재가"].replace(0, pd.NA)).fillna(0).astype(float).apply(int)
    df["리밸런싱수량"] = df["목표수량"] - df["보유수량"]
    df["거래금액"] = df["리밸런싱수량"] * df["현재가"]

    principal = to_float(account.get("principal"))
    profit = current_value - principal if principal > 0 else 0.0
    return_rate = profit / principal if principal > 0 else 0.0

    metrics = {
        "holdingsValue": holdings_value,
        "currentValue": current_value,
        "targetBase": target_base,
        "principal": principal,
        "profit": profit,
        "returnRate": return_rate,
        "totalTargetWeight": float(df["목표비중"].sum()),
        "buyValue": float(df.loc[df["거래금액"] > 0, "거래금액"].sum()),
        "sellValue": float(abs(df.loc[df["거래금액"] < 0, "거래금액"].sum())),
        "netTradeValue": float(df["거래금액"].sum()),
    }
    return df, metrics


def assets_to_editor_frame(assets: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "구분": asset["category"],
                "분류": asset["group"],
                "티커": asset["ticker"],
                "상품": asset["name"],
                "목표비중(%)": to_float(asset.get("targetWeight")) * 100,
                "보유수량": to_float(asset.get("currentShares")),
                "수동현재가": to_float(asset.get("manualPrice")),
                "시트가격": to_float(asset.get("fallbackPrice")),
            }
            for asset in assets
        ]
    )


def editor_frame_to_assets(frame: pd.DataFrame) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for _, row in frame.fillna("").iterrows():
        ticker = clean_text(row.get("티커"))
        if not ticker:
            continue
        assets.append(
            normalize_asset(
                {
                    "category": row.get("구분") or "기타",
                    "group": row.get("분류") or row.get("구분") or "기타",
                    "ticker": ticker,
                    "name": row.get("상품") or ticker,
                    "targetWeight": to_float(row.get("목표비중(%)")) / 100,
                    "currentShares": to_float(row.get("보유수량")),
                    "manualPrice": to_optional_float(row.get("수동현재가")),
                    "fallbackPrice": to_optional_float(row.get("시트가격")),
                }
            )
        )
    return assets


def merge_imported_assets(imported: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    existing_by_ticker = {asset["ticker"]: asset for asset in current.get("assets", [])}
    merged_assets = []
    for asset in imported["assets"]:
        existing = existing_by_ticker.get(asset["ticker"], {})
        if to_float(existing.get("manualPrice")) > 0:
            asset["manualPrice"] = existing["manualPrice"]
        merged_assets.append(asset)
    imported["assets"] = merged_assets
    return imported


def render_metric_row(metrics: dict[str, float]) -> None:
    cols = st.columns(5)
    cols[0].metric("현재 평가액", format_krw(metrics["currentValue"]))
    cols[1].metric("원금", format_krw(metrics["principal"]))
    cols[2].metric("수익률", format_percent(metrics["returnRate"]), format_krw(metrics["profit"]))
    cols[3].metric("순매수 필요액", format_krw(metrics["netTradeValue"]))
    cols[4].metric("목표비중 합계", format_percent(metrics["totalTargetWeight"]))


def main() -> None:
    st.set_page_config(page_title="연금저축 리밸런서", page_icon="W", layout="wide")
    st.title("연금저축 리밸런서")
    st.caption("가격은 Streamlit 서버에서 yfinance로 조회하고, 계좌 설정은 Firestore에 저장합니다.")

    if not password_gate():
        st.stop()

    db = get_firestore_client()

    with st.sidebar:
        st.header("계좌")
        profile_default = DEFAULT_PROFILE_ID
        try:
            profile_default = str(st.secrets.get("DEFAULT_PROFILE_ID", DEFAULT_PROFILE_ID))
        except Exception:
            pass
        profile_id = sanitize_profile_id(st.text_input("프로필 ID", value=profile_default))

        if st.session_state.get("loaded_profile_id") != profile_id:
            st.session_state["account"] = load_account(db, profile_id)
            st.session_state["loaded_profile_id"] = profile_id
            st.session_state.setdefault("price_refresh_key", 0)

        account = copy.deepcopy(st.session_state.get("account", default_account()))
        if db is None:
            st.info("Firestore secrets가 없어서 현재는 세션 저장 모드로 실행 중입니다.")
        else:
            st.success("Firestore 연결됨")

        account["accountName"] = st.text_input("계좌 이름", value=account.get("accountName", "연금저축"))
        account["totalBalance"] = float(
            st.number_input(
                "리밸런싱 기준금액",
                min_value=0.0,
                value=float(to_float(account.get("totalBalance"))),
                step=100_000.0,
                format="%.0f",
            )
        )
        account["principal"] = float(
            st.number_input(
                "원금",
                min_value=0.0,
                value=float(to_float(account.get("principal"))),
                step=100_000.0,
                format="%.0f",
            )
        )
        account["cash"] = float(
            st.number_input(
                "예수금",
                min_value=0.0,
                value=float(to_float(account.get("cash"))),
                step=10_000.0,
                format="%.0f",
            )
        )
        account["sheetUrl"] = st.text_area("Google Sheet CSV URL", value=account.get("sheetUrl", DEFAULT_SHEET_URL), height=90)

        import_clicked = st.button("시트 가져오기", use_container_width=True)
        refresh_clicked = st.button("가격 새로고침", use_container_width=True)
        save_clicked = st.button("Firestore에 저장", type="primary", use_container_width=True)
        reload_clicked = st.button("Firestore에서 다시 불러오기", use_container_width=True)

    if reload_clicked:
        st.session_state["account"] = load_account(db, profile_id)
        st.rerun()

    if import_clicked:
        try:
            with st.spinner("Google Sheet 포트폴리오를 가져오는 중입니다."):
                imported_account = parse_sheet_portfolio(fetch_sheet_csv(account["sheetUrl"]))
            imported_account["sheetUrl"] = account["sheetUrl"]
            account = merge_imported_assets(imported_account, account)
            st.session_state["account"] = account
            save_account(db, profile_id, account)
            st.success(f"{len(account['assets'])}개 자산을 가져왔습니다.")
        except Exception as error:
            st.error(f"시트 가져오기에 실패했습니다: {error}")

    if refresh_clicked:
        st.session_state["price_refresh_key"] = st.session_state.get("price_refresh_key", 0) + 1

    st.subheader(account.get("accountName", "연금저축"))
    st.write("자산별 목표비중, 보유수량, 수동현재가를 수정한 뒤 저장하면 Firestore에 반영됩니다.")

    editor_frame = assets_to_editor_frame(account["assets"])
    edited_frame = st.data_editor(
        editor_frame,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config={
            "목표비중(%)": st.column_config.NumberColumn("목표비중(%)", min_value=0.0, max_value=100.0, step=0.01),
            "보유수량": st.column_config.NumberColumn("보유수량", min_value=0.0, step=1.0),
            "수동현재가": st.column_config.NumberColumn("수동현재가", min_value=0.0, step=10.0),
            "시트가격": st.column_config.NumberColumn("시트가격", min_value=0.0, step=10.0),
        },
        key=f"asset_editor_{profile_id}",
    )

    account["assets"] = editor_frame_to_assets(edited_frame)
    st.session_state["account"] = account

    if save_clicked:
        save_account(db, profile_id, account)
        st.success("Firestore에 저장했습니다." if db else "현재 세션에 저장했습니다.")

    tickers = tuple(asset["ticker"] for asset in account["assets"] if asset.get("ticker"))
    with st.spinner("Yahoo Finance 현재가를 확인하는 중입니다."):
        prices = fetch_price_snapshot(tickers, st.session_state.get("price_refresh_key", 0))

    model_df, metrics = build_model(account, prices)
    render_metric_row(metrics)

    tab_summary, tab_prices, tab_export = st.tabs(["리밸런싱", "가격 상태", "내보내기"])

    with tab_summary:
        table = model_df.copy()
        if not table.empty:
            display_table = table[
                [
                    "구분",
                    "분류",
                    "티커",
                    "상품",
                    "목표비중",
                    "현재가",
                    "가격출처",
                    "보유수량",
                    "평가금액",
                    "현재비중",
                    "목표금액",
                    "목표수량",
                    "리밸런싱수량",
                    "거래금액",
                ]
            ].copy()
            display_table["목표비중"] = display_table["목표비중"] * 100
            display_table["현재비중"] = display_table["현재비중"] * 100
            st.dataframe(
                display_table,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "목표비중": st.column_config.NumberColumn("목표비중", format="%.2f%%"),
                    "현재비중": st.column_config.NumberColumn("현재비중", format="%.2f%%"),
                    "현재가": st.column_config.NumberColumn("현재가", format="%d원"),
                    "평가금액": st.column_config.NumberColumn("평가금액", format="%d원"),
                    "목표금액": st.column_config.NumberColumn("목표금액", format="%d원"),
                    "거래금액": st.column_config.NumberColumn("거래금액", format="%d원"),
                },
            )

            chart_df = table[table["목표금액"] > 0].copy()
            if not chart_df.empty:
                chart_df["목표비중표시"] = chart_df["목표비중"] * 100
                fig = px.bar(
                    chart_df,
                    x="상품",
                    y="목표비중표시",
                    color="구분",
                    labels={"목표비중표시": "목표비중(%)", "상품": ""},
                    height=420,
                )
                fig.update_layout(margin=dict(l=10, r=10, t=20, b=120), xaxis_tickangle=-35)
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("자산이 없습니다. 시트를 가져오거나 표에 자산을 추가하세요.")

    with tab_prices:
        price_rows = []
        for asset in account["assets"]:
            ticker = asset["ticker"]
            price_info = prices.get(ticker, {})
            price, source = resolve_price(asset, price_info)
            price_rows.append(
                {
                    "티커": ticker,
                    "Yahoo 심볼": price_info.get("symbol", ", ".join(yahoo_symbols_for_ticker(ticker))),
                    "현재 적용가": price,
                    "적용 출처": source,
                    "조회 성공": bool(price_info.get("ok")),
                    "오류": price_info.get("error", ""),
                }
            )
        st.dataframe(pd.DataFrame(price_rows), use_container_width=True, hide_index=True)
        st.caption("수동현재가가 입력된 자산은 수동현재가가 Yahoo 가격보다 우선 적용됩니다.")

    with tab_export:
        csv_bytes = model_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "리밸런싱 CSV 다운로드",
            data=csv_bytes,
            file_name=f"pension_rebalancing_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.json(
            {
                "profileId": profile_id,
                "firestorePath": f"streamlit_accounts/{profile_id}/accounts/default",
                "targetBase": metrics["targetBase"],
                "updatedAt": datetime.now().isoformat(timespec="seconds"),
            }
        )


if __name__ == "__main__":
    main()
