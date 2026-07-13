from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import yfinance as yf

try:
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account as google_service_account
except Exception:  # pragma: no cover - handled in the UI at runtime.
    AuthorizedSession = None
    google_service_account = None

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except Exception:  # pragma: no cover - handled in the UI at runtime.
    firebase_admin = None
    credentials = None
    firestore = None


DEFAULT_BASE_TOTAL = 22_495_000
DEFAULT_PROFILE_ID = "personal"
DEFAULT_SYNC_SHEET_URL = ""
DEFAULT_SYNC_SHEET_NAME = "StreamlitSync"
GOOGLE_SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
GOLD_GROUP_NAME = "금"
DEFAULT_GOLD_TARGET_WEIGHT = 0.20
GOLD_INITIAL_ALLOCATION = {
    "KRX:0072R0": 1_730_960 / 4_499_000,
    "KRX:411060": 2_768_040 / 4_499_000,
}

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
            "targetWeight": 0.0 if group == GOLD_GROUP_NAME else target_value / DEFAULT_BASE_TOTAL,
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
        "goldTargetWeight": DEFAULT_GOLD_TARGET_WEIGHT,
        "syncSheetUrl": DEFAULT_SYNC_SHEET_URL,
        "syncSheetName": DEFAULT_SYNC_SHEET_NAME,
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


def read_secret(name: str, default: str = "") -> str:
    try:
        return clean_text(st.secrets.get(name, default))
    except Exception:
        return default


def oidc_is_configured() -> bool:
    try:
        auth = st.secrets.get("auth")
    except Exception:
        return False
    if not auth:
        return False
    return all(clean_text(auth.get(key)) for key in ("redirect_uri", "cookie_secret", "client_id", "client_secret"))


def authenticated_profile() -> tuple[str, str] | None:
    if not oidc_is_configured():
        return None

    if not st.user.is_logged_in:
        st.subheader("Google 계정으로 로그인")
        st.write("로그인한 사용자마다 원금, 보유수량과 Google Sheet 설정이 별도로 저장됩니다.")
        st.button("Google 계정으로 로그인", type="primary", on_click=st.login)
        st.stop()

    email = clean_text(st.user.get("email")).lower()
    subject = clean_text(st.user.get("sub")) or email
    if not subject:
        st.error("로그인 사용자 식별 정보를 확인할 수 없습니다.")
        st.stop()

    legacy_owner_email = read_secret("LEGACY_OWNER_EMAIL").lower()
    if legacy_owner_email and email == legacy_owner_email:
        profile_id = DEFAULT_PROFILE_ID
    else:
        profile_id = f"google-{hashlib.sha256(subject.encode('utf-8')).hexdigest()[:32]}"

    display_name = clean_text(st.user.get("name")) or email or "사용자"
    return profile_id, display_name


def normalize_asset(asset: dict[str, Any]) -> dict[str, Any]:
    ticker = clean_text(asset.get("ticker") or asset.get("id"))
    group = clean_text(asset.get("group")) or clean_text(asset.get("category")) or "기타"
    return {
        "id": ticker,
        "category": clean_text(asset.get("category")) or "기타",
        "group": group,
        "ticker": ticker,
        "name": clean_text(asset.get("name")) or ticker,
        "targetWeight": 0.0 if group == GOLD_GROUP_NAME else to_float(asset.get("targetWeight")),
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
        "goldTargetWeight": to_float(raw.get("goldTargetWeight"), DEFAULT_GOLD_TARGET_WEIGHT),
        "syncSheetUrl": clean_text(raw.get("syncSheetUrl") or raw.get("sheetEditUrl")) or fallback["syncSheetUrl"],
        "syncSheetName": clean_text(raw.get("syncSheetName")) or fallback["syncSheetName"],
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


def read_service_account_info() -> dict[str, Any] | None:
    service_account = read_secret_section("firebase_service_account")
    if service_account:
        info = dict(service_account)
    else:
        json_secret = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        if json_secret:
            info = json.loads(json_secret)
        else:
            local_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if not local_file:
                return None
            with open(local_file, encoding="utf-8") as file:
                info = json.load(file)

    info["private_key"] = str(info.get("private_key", "")).replace("\\n", "\n")
    return info


@st.cache_resource(show_spinner=False)
def get_firestore_client() -> Any | None:
    if firebase_admin is None or credentials is None:
        return None

    service_account = read_service_account_info()
    app_name = "streamlit-firestore-rebalancer"

    if app_name in firebase_admin._apps:
        app = firebase_admin.get_app(app_name)
        return firestore.client(app)

    if service_account:
        cred = credentials.Certificate(service_account)
        app = firebase_admin.initialize_app(cred, name=app_name)
        return firestore.client(app)

    return None


@st.cache_resource(show_spinner=False)
def get_google_sheets_session() -> Any | None:
    if AuthorizedSession is None or google_service_account is None:
        return None

    service_account = read_service_account_info()
    if not service_account:
        return None

    creds = google_service_account.Credentials.from_service_account_info(
        service_account,
        scopes=[GOOGLE_SHEETS_SCOPE],
    )
    return AuthorizedSession(creds)


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


def row_get(row: list[Any], index: int) -> Any:
    if 0 <= index < len(row):
        return row[index]
    return ""


def spreadsheet_id_from_url(value: str) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", text)
    if match:
        return match.group(1)
    if "/d/e/" in text or "pub?" in text:
        return None
    if re.fullmatch(r"[a-zA-Z0-9-_]{20,}", text):
        return text
    return None


def normalize_sheet_name(value: str) -> str:
    return clean_text(value) or DEFAULT_SYNC_SHEET_NAME


def quoted_sheet_range(sheet_name: str, cell_range: str = "A1:Z1000") -> str:
    escaped_name = normalize_sheet_name(sheet_name).replace("'", "''")
    return f"'{escaped_name}'!{cell_range}"


def column_letter(index: int) -> str:
    result = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result or "A"


def sheets_json_response(response: Any) -> dict[str, Any]:
    if response.ok:
        return response.json() if response.content else {}
    try:
        message = response.json().get("error", {}).get("message", response.text)
    except Exception:
        message = response.text
    raise RuntimeError(message)


def get_spreadsheet_titles(session: Any, spreadsheet_id: str) -> list[str]:
    response = session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
        params={"fields": "sheets.properties.title"},
        timeout=15,
    )
    data = sheets_json_response(response)
    return [sheet["properties"]["title"] for sheet in data.get("sheets", [])]


def ensure_sheet_tab(session: Any, spreadsheet_id: str, sheet_name: str) -> None:
    sheet_name = normalize_sheet_name(sheet_name)
    if sheet_name in get_spreadsheet_titles(session, spreadsheet_id):
        return

    response = session.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate",
        json={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
        timeout=15,
    )
    sheets_json_response(response)


def read_google_sheet_values(session: Any, spreadsheet_id: str, sheet_name: str) -> list[list[Any]]:
    range_a1 = quoted_sheet_range(sheet_name)
    response = session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{quote(range_a1, safe='')}",
        params={"valueRenderOption": "UNFORMATTED_VALUE"},
        timeout=15,
    )
    data = sheets_json_response(response)
    return data.get("values", [])


def sheet_cell(value: Any) -> Any:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    if isinstance(value, float):
        if value == float("inf") or value == float("-inf"):
            return ""
        return round(value, 6)
    return value


def account_to_sheet_values(
    account: dict[str, Any],
    model_df: pd.DataFrame,
    metrics: dict[str, float],
    profile_id: str,
) -> list[list[Any]]:
    assets_by_ticker = {asset["ticker"]: asset for asset in account.get("assets", [])}
    values: list[list[Any]] = [
        ["연금저축 리밸런서 동기화"],
        ["updatedAt", datetime.now().isoformat(timespec="seconds")],
        ["profileId", profile_id],
        ["accountName", account.get("accountName", "연금저축")],
        ["리밸런싱 기준금액", to_float(account.get("totalBalance"))],
        ["원금", to_float(account.get("principal"))],
        ["예수금", to_float(account.get("cash"))],
        ["금 ETF 합산 목표비중(%)", to_float(account.get("goldTargetWeight"), DEFAULT_GOLD_TARGET_WEIGHT) * 100],
        ["현재 평가액", metrics.get("currentValue", 0.0)],
        [],
        [
            "구분",
            "분류",
            "티커",
            "상품",
            "목표비중(%)",
            "보유수량",
            "수동현재가",
            "시트가격",
            "현재적용가",
            "가격출처",
            "평가금액",
            "현재비중(%)",
            "목표금액",
            "목표수량",
            "리밸런싱수량",
            "거래금액",
        ],
    ]

    for _, row in model_df.iterrows():
        ticker = clean_text(row.get("티커"))
        asset = assets_by_ticker.get(ticker, {})
        values.append(
            [
                row.get("구분", ""),
                row.get("분류", ""),
                ticker,
                row.get("상품", ""),
                to_float(row.get("목표비중")) * 100,
                to_float(asset.get("currentShares")),
                to_float(asset.get("manualPrice")) or "",
                to_float(asset.get("fallbackPrice")) or "",
                to_float(row.get("현재가")),
                row.get("가격출처", ""),
                to_float(row.get("평가금액")),
                to_float(row.get("현재비중")) * 100,
                to_float(row.get("목표금액")),
                to_float(row.get("목표수량")),
                to_float(row.get("리밸런싱수량")),
                to_float(row.get("거래금액")),
            ]
        )

    return [[sheet_cell(cell) for cell in row] for row in values]


def write_google_sheet_values(
    session: Any,
    spreadsheet_id: str,
    sheet_name: str,
    values: list[list[Any]],
) -> None:
    ensure_sheet_tab(session, spreadsheet_id, sheet_name)
    clear_range = quoted_sheet_range(sheet_name)
    session.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{quote(clear_range, safe='')}:clear",
        json={},
        timeout=15,
    )

    row_count = max(len(values), 1)
    column_count = max((len(row) for row in values), default=1)
    update_range = quoted_sheet_range(sheet_name, f"A1:{column_letter(column_count)}{row_count}")
    response = session.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{quote(update_range, safe='')}",
        params={"valueInputOption": "USER_ENTERED"},
        json={"range": update_range, "majorDimension": "ROWS", "values": values},
        timeout=20,
    )
    sheets_json_response(response)


def find_sync_header_index(rows: list[list[Any]]) -> int:
    for index, row in enumerate(rows):
        normalized = [clean_text(cell).replace(" ", "") for cell in row]
        if "티커" in normalized and "보유수량" in normalized and (
            "목표비중(%)" in normalized or "목표비중" in normalized
        ):
            return index
    raise ValueError("동기화 탭에서 자산 목록 헤더를 찾지 못했습니다.")


def header_lookup(header: list[Any], *names: str) -> int:
    normalized = [clean_text(cell).replace(" ", "") for cell in header]
    for name in names:
        compact = name.replace(" ", "")
        if compact in normalized:
            return normalized.index(compact)
    return -1


def parse_google_sync_values(
    values: list[list[Any]],
    current: dict[str, Any],
) -> dict[str, Any]:
    header_index = find_sync_header_index(values)
    header = values[header_index]
    metadata: dict[str, Any] = {}
    for row in values[:header_index]:
        if len(row) >= 2:
            key = clean_text(row[0])
            if key:
                metadata[key] = row[1]

    ticker_index = header_lookup(header, "티커")
    name_index = header_lookup(header, "상품")
    category_index = header_lookup(header, "구분")
    group_index = header_lookup(header, "분류")
    target_index = header_lookup(header, "목표비중(%)", "목표비중")
    shares_index = header_lookup(header, "보유수량")
    manual_price_index = header_lookup(header, "수동현재가")
    fallback_price_index = header_lookup(header, "시트가격", "현재가")

    assets: list[dict[str, Any]] = []
    for row in values[header_index + 1 :]:
        ticker = clean_text(row_get(row, ticker_index))
        if not ticker:
            continue
        target_value = to_float(row_get(row, target_index))
        target_weight = target_value / 100 if target_value > 1 else target_value
        assets.append(
            normalize_asset(
                {
                    "category": row_get(row, category_index) or "기타",
                    "group": row_get(row, group_index) or row_get(row, category_index) or "기타",
                    "ticker": ticker,
                    "name": row_get(row, name_index) or ticker,
                    "targetWeight": target_weight,
                    "currentShares": to_float(row_get(row, shares_index)),
                    "manualPrice": to_optional_float(row_get(row, manual_price_index)),
                    "fallbackPrice": to_optional_float(row_get(row, fallback_price_index)),
                }
            )
        )

    return normalize_account(
        {
            **current,
            "accountName": clean_text(metadata.get("accountName")) or current.get("accountName", "연금저축"),
            "totalBalance": to_float(metadata.get("리밸런싱 기준금액"), current.get("totalBalance")),
            "principal": to_float(metadata.get("원금"), current.get("principal")),
            "cash": to_float(metadata.get("예수금"), current.get("cash")),
            "goldTargetWeight": to_float(
                metadata.get("금 ETF 합산 목표비중(%)"),
                to_float(current.get("goldTargetWeight"), DEFAULT_GOLD_TARGET_WEIGHT) * 100,
            )
            / 100,
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


def read_naver_quote(code: str) -> dict[str, Any] | None:
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
                quote_time = next(
                    (
                        clean_text(quote.get(key))
                        for key in ("localTradedAt", "tradedAt", "tradeTime", "dateTime", "time")
                        if clean_text(quote.get(key))
                    ),
                    "",
                )
                return {"price": price, "asOf": quote_time or None}
        except Exception:
            continue
    return None


def read_naver_price(code: str) -> float | None:
    quote = read_naver_quote(code)
    return to_optional_float(quote.get("price")) if quote else None


@st.cache_data(ttl=30, show_spinner=False)
def fetch_price_snapshot(tickers: tuple[str, ...], refresh_key: int) -> dict[str, dict[str, Any]]:
    del refresh_key
    snapshot: dict[str, dict[str, Any]] = {}

    for ticker in tickers:
        errors: list[str] = []
        queried_at = datetime.now(timezone.utc).isoformat()
        naver_code = naver_code_for_ticker(ticker)
        if naver_code:
            naver_quote = read_naver_quote(naver_code)
            if naver_quote and to_float(naver_quote.get("price")) > 0:
                snapshot[ticker] = {
                    "ok": True,
                    "price": naver_quote["price"],
                    "source": "Naver",
                    "symbol": naver_code,
                    "asOf": naver_quote.get("asOf"),
                    "queriedAt": queried_at,
                }
                continue
            errors.append(f"Naver {naver_code}: no price")

        for symbol in yahoo_symbols_for_ticker(ticker):
            if symbol == "CASH":
                snapshot[ticker] = {
                    "ok": True,
                    "price": 1.0,
                    "source": "현금",
                    "symbol": symbol,
                    "asOf": None,
                    "queriedAt": queried_at,
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
                        "asOf": None,
                        "queriedAt": queried_at,
                    }
                    break
                errors.append(f"{symbol}: no price")
            except Exception as error:
                errors.append(f"{symbol}: {error}")
        else:
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
    gold_mask = df["분류"].eq(GOLD_GROUP_NAME)
    if gold_mask.any():
        gold_target_weight = to_float(account.get("goldTargetWeight"), DEFAULT_GOLD_TARGET_WEIGHT)
        gold_values = df.loc[gold_mask, "평가금액"]
        if float(gold_values.sum()) > 0:
            gold_allocation = gold_values / float(gold_values.sum())
        else:
            initial = df.loc[gold_mask, "티커"].map(GOLD_INITIAL_ALLOCATION).fillna(0.0)
            if float(initial.sum()) <= 0:
                initial = pd.Series(1.0, index=df.index[gold_mask])
            gold_allocation = initial / float(initial.sum())
        df.loc[gold_mask, "목표비중"] = gold_target_weight * gold_allocation
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
                "목표비중(%)": None
                if asset.get("group") == GOLD_GROUP_NAME
                else to_float(asset.get("targetWeight")) * 100,
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
                    "targetWeight": 0.0
                    if clean_text(row.get("분류")) == GOLD_GROUP_NAME
                    else to_float(row.get("목표비중(%)")) / 100,
                    "currentShares": to_float(row.get("보유수량")),
                    "manualPrice": to_optional_float(row.get("수동현재가")),
                    "fallbackPrice": to_optional_float(row.get("시트가격")),
                }
            )
        )
    return assets


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
    st.caption("국내 가격은 Naver를 우선 조회하고, 계좌 설정은 Firestore에 저장하며 Google Sheet와 동기화할 수 있습니다.")

    identity = authenticated_profile()
    if identity is None and not password_gate():
        st.stop()

    db = get_firestore_client()
    sheets_session = get_google_sheets_session()

    with st.sidebar:
        st.header("계좌")
        if identity is not None:
            profile_id, display_name = identity
            st.write(f"{display_name}님")
            if st.button("로그아웃", use_container_width=True):
                st.logout()
        else:
            profile_default = read_secret("DEFAULT_PROFILE_ID", DEFAULT_PROFILE_ID)
            profile_id = sanitize_profile_id(st.text_input("프로필 ID", value=profile_default))
            st.warning("Google 로그인이 설정되지 않아 공용 비밀번호 방식으로 실행 중입니다.")

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
        account["goldTargetWeight"] = float(
            st.number_input(
                "금 ETF 합산 목표비중(%)",
                min_value=0.0,
                max_value=100.0,
                value=float(to_float(account.get("goldTargetWeight"), DEFAULT_GOLD_TARGET_WEIGHT) * 100),
                step=0.5,
                format="%.1f",
            )
            / 100
        )
        st.divider()
        st.subheader("Google Sheet 동기화")
        account["syncSheetUrl"] = st.text_area(
            "편집용 Sheet URL 또는 ID",
            value=account.get("syncSheetUrl", ""),
            height=70,
            help="docs.google.com/spreadsheets/d/... 형식의 편집 URL을 넣고 서비스 계정 이메일에 편집 권한을 공유하세요.",
        )
        account["syncSheetName"] = st.text_input(
            "동기화 탭 이름",
            value=normalize_sheet_name(account.get("syncSheetName", DEFAULT_SYNC_SHEET_NAME)),
        )
        sync_import_clicked = st.button("Sheet에서 앱으로 가져오기", use_container_width=True)
        sync_export_clicked = st.button("앱에서 Sheet로 내보내기", use_container_width=True)

        st.divider()
        refresh_clicked = st.button("가격 새로고침", use_container_width=True)
        save_clicked = st.button("Firestore에 저장", type="primary", use_container_width=True)
        reload_clicked = st.button("Firestore에서 다시 불러오기", use_container_width=True)

    if reload_clicked:
        st.session_state["account"] = load_account(db, profile_id)
        st.rerun()

    if sync_import_clicked:
        try:
            spreadsheet_id = spreadsheet_id_from_url(account.get("syncSheetUrl", ""))
            if sheets_session is None:
                raise RuntimeError("Google Sheets 연동에 사용할 서비스 계정 secret이 없습니다.")
            if not spreadsheet_id:
                raise RuntimeError("편집 가능한 Google Sheet URL 또는 스프레드시트 ID를 입력하세요.")

            with st.spinner("Google Sheet 동기화 탭에서 가져오는 중입니다."):
                values = read_google_sheet_values(
                    sheets_session,
                    spreadsheet_id,
                    normalize_sheet_name(account.get("syncSheetName", DEFAULT_SYNC_SHEET_NAME)),
                )
                account = parse_google_sync_values(values, account)
            st.session_state["account"] = account
            save_account(db, profile_id, account)
            st.success(f"Google Sheet에서 {len(account['assets'])}개 자산을 가져왔습니다.")
        except Exception as error:
            st.error(f"Google Sheet 가져오기에 실패했습니다: {error}")

    if refresh_clicked:
        st.session_state["price_refresh_key"] = st.session_state.get("price_refresh_key", 0) + 1

    st.subheader(account.get("accountName", "연금저축"))
    st.write("자산별 목표비중, 보유수량, 수동현재가를 수정한 뒤 저장하면 Firestore에 반영됩니다.")
    st.caption("금 ETF는 개별 목표비중을 사용하지 않습니다. 두 금 종목의 합산 목표를 현재 평가액 비율로 나눠 계산합니다.")

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
    with st.spinner("현재가를 확인하는 중입니다."):
        prices = fetch_price_snapshot(tickers, st.session_state.get("price_refresh_key", 0))

    model_df, metrics = build_model(account, prices)

    if sync_export_clicked:
        try:
            spreadsheet_id = spreadsheet_id_from_url(account.get("syncSheetUrl", ""))
            if sheets_session is None:
                raise RuntimeError("Google Sheets 연동에 사용할 서비스 계정 secret이 없습니다.")
            if not spreadsheet_id:
                raise RuntimeError("편집 가능한 Google Sheet URL 또는 스프레드시트 ID를 입력하세요.")

            with st.spinner("현재 앱 데이터를 Google Sheet로 내보내는 중입니다."):
                write_google_sheet_values(
                    sheets_session,
                    spreadsheet_id,
                    normalize_sheet_name(account.get("syncSheetName", DEFAULT_SYNC_SHEET_NAME)),
                    account_to_sheet_values(account, model_df, metrics, profile_id),
                )
            save_account(db, profile_id, account)
            st.success(f"Google Sheet의 {normalize_sheet_name(account.get('syncSheetName', DEFAULT_SYNC_SHEET_NAME))} 탭에 내보냈습니다.")
        except Exception as error:
            st.error(f"Google Sheet 내보내기에 실패했습니다: {error}")

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
                    "조회 심볼": price_info.get("symbol", ", ".join(yahoo_symbols_for_ticker(ticker))),
                    "현재 적용가": price,
                    "적용 출처": source,
                    "시세시각": price_info.get("asOf") or "확인 불가",
                    "조회시각(UTC)": price_info.get("queriedAt") or "",
                    "조회 성공": bool(price_info.get("ok")),
                    "오류": price_info.get("error", ""),
                }
            )
        st.dataframe(pd.DataFrame(price_rows), use_container_width=True, hide_index=True)
        st.caption("수동현재가가 온라인 가격보다 우선합니다. 국내 종목은 Naver, Yahoo 순서로 조회합니다.")

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
                "syncSheetName": normalize_sheet_name(account.get("syncSheetName", DEFAULT_SYNC_SHEET_NAME)),
                "syncSheetConfigured": bool(spreadsheet_id_from_url(account.get("syncSheetUrl", ""))),
                "targetBase": metrics["targetBase"],
                "updatedAt": datetime.now().isoformat(timespec="seconds"),
            }
        )


if __name__ == "__main__":
    main()
