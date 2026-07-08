# Streamlit + Firestore 연금저축 리밸런서

Streamlit 서버에서 `yfinance`로 현재가를 조회하고, 계좌 설정과 보유수량은 Firebase Firestore에 저장하는 버전입니다.
국내 ETF는 Yahoo Finance가 실패하면 Naver Finance API를 한 번 더 시도합니다.
Google Sheet는 공개 CSV에서 초기 데이터를 가져오거나, 편집 가능한 스프레드시트의 전용 탭과 양방향 동기화할 수 있습니다.

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

## Google Sheet 동기화

앱의 기본 저장소는 Firestore입니다. Google Sheet는 다음 두 방식으로 함께 사용할 수 있습니다.

- 공개 CSV URL: 기존 스프레드시트 구조를 읽어서 앱으로 가져옵니다.
- 편집용 Sheet URL 또는 ID: 앱에서 수정한 원금, 예수금, 목표비중, 보유수량, 계산 결과를 `StreamlitSync` 같은 전용 탭에 내보내고 다시 가져옵니다.

쓰기 동기화를 쓰려면 Google Sheet의 공유 메뉴에서 Secrets의 `client_email` 서비스 계정에 편집 권한을 주세요. 공개 배포 URL(`/d/e/.../pub?...output=csv`)은 읽기 전용이라 내보내기에는 사용할 수 없습니다.

## Firestore 저장 위치

```text
streamlit_accounts/{프로필 ID}/accounts/default
```

이 앱은 서버에서 Firebase Admin SDK로 Firestore에 접근하므로 Firestore 보안 규칙을 우회합니다. 그래서 Streamlit 앱에는 반드시 `APP_PASSWORD`를 설정하는 편이 좋습니다.

## 가격 조회

- `KRX:379800` 같은 국내 티커는 `379800.KS`, `379800.KQ` 순서로 Yahoo Finance를 조회합니다.
- Yahoo 조회에 실패한 국내 티커는 Naver Finance를 보조로 조회합니다.
- 둘 다 실패하면 Google Sheet에서 가져온 시트 가격을 사용합니다.
- `수동현재가`를 입력하면 Yahoo 가격보다 우선 적용됩니다.
