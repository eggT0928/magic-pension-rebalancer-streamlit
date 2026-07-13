# Streamlit + Firestore 연금저축 리밸런서

Streamlit 서버에서 `yfinance`로 현재가를 조회하고, 계좌 설정과 보유수량은 Firebase Firestore에 저장하는 버전입니다.
국내 ETF는 Yahoo Finance가 실패하면 Naver Finance API를 한 번 더 시도합니다.
Google Sheet는 편집 가능한 스프레드시트의 전용 탭과 양방향 동기화할 수 있습니다.
Google OIDC를 설정하면 로그인한 Google 계정별로 Firestore 데이터가 완전히 분리됩니다.

## 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud 배포

1. 이 폴더를 GitHub 저장소에 올립니다.
2. Streamlit Community Cloud에서 `app.py`를 선택해 배포합니다.
3. 앱 Settings의 Secrets에 `.streamlit/secrets.example.toml` 형식으로 값을 넣습니다.

Firebase 서비스 계정 키는 Firebase Console의 `Project settings > Service accounts > Generate new private key`에서 발급합니다. 발급된 JSON 내용을 TOML 형식으로 옮기되, `private_key`의 줄바꿈은 `\n`으로 넣습니다.

## 여러 사용자 로그인

Google Cloud Console에서 OAuth 동의 화면과 `웹 애플리케이션` OAuth 클라이언트를 만든 뒤 승인된 리디렉션 URI에 다음 주소를 등록합니다.

```text
https://magic-pension-rebalancer.streamlit.app/oauth2callback
```

Streamlit Cloud Secrets에 다음 설정을 추가합니다.

```toml
[auth]
redirect_uri = "https://magic-pension-rebalancer.streamlit.app/oauth2callback"
cookie_secret = "충분히 긴 무작위 문자열"
client_id = "Google OAuth 클라이언트 ID"
client_secret = "Google OAuth 클라이언트 secret"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

OAuth 앱이 `테스트` 상태라면 Google Cloud Console의 Audience에서 테스트 사용자를 추가해야 합니다. 누구나 Google 계정으로 로그인하게 하려면 앱 상태를 `게시됨`으로 변경합니다.

로그인 계정은 Google 사용자 고유값을 해시한 Firestore 문서 ID를 사용합니다. 화면에서 프로필 ID를 변경할 수 없으므로 다른 사용자의 데이터에 접근할 수 없습니다. 기존 `personal` 문서를 계속 사용할 소유자는 Secrets에 `LEGACY_OWNER_EMAIL = "소유자 Google 이메일"`을 추가할 수 있습니다. 이 값은 공개 저장소에 커밋하지 않습니다.

## Google Sheet 동기화

앱의 기본 저장소는 Firestore입니다. Google Sheet는 편집 가능한 스프레드시트의 전용 탭을 통해 함께 사용할 수 있습니다.

- 편집용 Sheet URL 또는 ID: 앱에서 수정한 원금, 예수금, 목표비중, 보유수량, 계산 결과를 `StreamlitSync` 같은 전용 탭에 내보내고 다시 가져옵니다.

동기화를 쓰려면 Google Sheet의 공유 메뉴에서 Secrets의 `client_email` 서비스 계정에 편집 권한을 주세요. 공개 배포 URL(`/d/e/.../pub?...output=csv`)은 사용하지 않습니다.

## Firestore 저장 위치

```text
streamlit_accounts/{프로필 ID}/accounts/default
```

Google 로그인을 설정한 경우 프로필 ID는 앱이 로그인 사용자별로 자동 생성합니다. Google 로그인이 설정되지 않은 환경에서만 기존 `APP_PASSWORD`와 수동 프로필 ID 방식으로 동작합니다.

이 앱은 서버에서 Firebase Admin SDK로 Firestore에 접근하므로 Firestore 보안 규칙을 우회합니다. 서비스 계정 secret과 Google OAuth secret을 반드시 Streamlit Secrets에서만 관리해야 합니다.

## 가격 조회

- `KRX:379800` 같은 국내 티커는 `379800.KS`, `379800.KQ` 순서로 Yahoo Finance를 조회합니다.
- Yahoo 조회에 실패한 국내 티커는 Naver Finance를 보조로 조회합니다.
- 둘 다 실패하면 Google Sheet에서 가져온 시트 가격을 사용합니다.
- `수동현재가`를 입력하면 Yahoo 가격보다 우선 적용됩니다.

