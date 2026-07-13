# 프로젝트 인수인계: 연금저축 리밸런서

작성일: 2026-07-13 (Asia/Seoul)

## 1. 프로젝트 목적

Google Spreadsheet로 관리하던 연금저축 포트폴리오를 Streamlit 앱에서도 조회하고 수정할 수 있게 만든 프로젝트다.

핵심 목적은 다음과 같다.

- Yahoo Finance 기반의 비교적 최신 가격으로 월별 리밸런싱 수량과 금액 계산
- 사용자의 원금, 예수금, 보유수량, 목표 비중을 Firestore에 영구 저장
- Streamlit과 Google Sheet 양쪽에서 데이터를 수정하고 전용 탭을 통해 양방향 동기화
- 서버 운영 부담과 비용을 줄이기 위해 Streamlit Community Cloud와 Firebase 무료 범위 활용

GitHub 저장소: `eggT0928/magic-pension-rebalancer-streamlit`

배포 앱: https://magic-pension-rebalancer.streamlit.app/

## 2. 사용 기술과 실행 방법

- Python 3.12
- Streamlit
- pandas / Plotly
- yfinance
- Naver Finance 비공식 API(국내 종목 가격 보조 조회)
- Firebase Admin SDK / Cloud Firestore
- Google Sheets API / google-auth
- Streamlit Community Cloud

로컬 실행:

```powershell
cd "D:\OneDrive - 남이초등학교\파이썬 관련\Python test\주식관련\streamlit-firestore-rebalancer"
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

로컬에서 Firebase와 Google Sheets를 연결하려면 `.streamlit/secrets.toml`이 필요하다. Secrets가 없으면 앱은 실행되지만 Firestore 대신 현재 Streamlit 세션에만 저장되고, Google Sheet 동기화는 사용할 수 없다.

## 3. 현재까지 구현된 기능

- Google OIDC 로그인 및 로그아웃(Secrets의 `[auth]`가 설정된 경우)
- Google 사용자 고유값을 해시한 프로필 ID별 계좌 분리
- OIDC 미설정 환경에서는 기존 앱 비밀번호와 수동 프로필 ID 방식으로 대체 실행
- 계좌 이름, 리밸런싱 기준금액, 원금, 예수금 입력
- 자산별 구분, 분류, 티커, 상품명, 목표 비중, 보유수량, 수동현재가, 시트가격 편집
- Firestore 저장 및 다시 불러오기
- Yahoo Finance 현재가 조회
- `KRX:종목코드`를 `.KS`, `.KQ` 심볼로 변환해 순차 조회
- 국내 티커의 Yahoo 조회 실패 시 Naver Finance 가격 보조 조회
- 가격 적용 우선순위: 수동현재가 > 온라인 조회가 > 시트가격
- 목표금액, 목표수량, 리밸런싱 수량, 거래금액, 현재 비중, 수익률 계산
- 리밸런싱 표와 목표 비중 차트
- 가격 조회 상태 및 오류 확인
- 리밸런싱 결과 CSV 다운로드
- Google Sheet `StreamlitSync` 전용 탭으로 앱 데이터 내보내기
- Google Sheet 전용 탭에서 앱으로 가져오기(현재 알려진 버그는 5절 참고)
- 기존 공개 CSV URL 입력 및 `공개 CSV에서 가져오기` UI 제거

기본 편집용 Google Sheet:

`https://docs.google.com/spreadsheets/d/1jhM6cJONsqk3dvJ0AIa9LkJ3O0Crt3IN500rNFbVr5Y/edit?usp=sharing`

## 4. 수정한 주요 파일

- `app.py`: 앱 전체 UI, 가격 조회, 리밸런싱 계산, Firestore 저장, Google Sheets 양방향 동기화
- `README.md`: 실행, Streamlit 배포, Firestore 경로, Google Sheet 동기화 및 가격 조회 설명
- `requirements.txt`: Streamlit, yfinance, pandas, Plotly, Firebase Admin, requests, google-auth 의존성
- `runtime.txt`: Streamlit Cloud Python 3.12 지정
- `.gitignore`: Secrets, 서비스 계정 JSON, 가상환경 및 캐시 제외

최근 원격 저장소 반영 이력:

- `7510df0` 부근: 기존 공개 Google Sheet CSV 가져오기 UI 제거
- `904075e0d3bad1e21b23eba885f910f3748e9cd9`: README를 양방향 동기화 구조에 맞게 수정

현재 PC에서는 `git` 명령이 PATH에 없어 GitHub 웹 편집기와 연결 도구를 사용해 원격 저장소에 반영했다. 다른 PC에서는 저장소를 정상적으로 clone한 뒤 Git으로 작업하는 것을 권장한다.

## 5. 아직 해결되지 않은 문제

### Google 로그인 배포 설정 필요

계정별 데이터 분리 코드는 구현됐고 누락됐던 `row_get()`도 복구됐다. 다만 실제 배포 앱에서 Google 로그인을 활성화하려면 Google Cloud OAuth 웹 클라이언트를 만들고 Streamlit Secrets에 `[auth]` 설정을 추가해야 한다. 설정 전에는 기존 공용 비밀번호 방식으로 계속 실행된다.

그 밖의 확인 사항:

- Google Sheet 내보내기 후 다시 가져오는 왕복 테스트가 아직 충분하지 않다.
- Streamlit Cloud의 yfinance는 거래소 상황, 요청 제한 또는 네트워크 상태에 따라 지연/실패할 수 있다.
- Naver Finance 조회는 공식 공개 계약 API가 아니므로 응답 형식 변경 가능성이 있다.
- 동기화는 자동 실시간 동기화가 아니라 사용자가 가져오기/내보내기 버튼을 누르는 방식이다.
- Firestore Admin SDK는 보안 규칙을 우회하므로 앱 비밀번호와 서비스 계정 비밀 관리가 매우 중요하다.
- 여러 사용자가 같은 프로필 ID를 사용하면 같은 Firestore 문서를 공유한다. 사용자별 인증은 아직 없다.
- 로컬에 `__pycache__`가 있고 과거 `py_compile` 실행 시 쓰기 권한 오류가 있었으나 AST 구문 검사는 통과했다.

## 6. 다음에 해야 할 작업

1. Google Cloud에서 OAuth 동의 화면과 웹 애플리케이션 클라이언트를 만든다.
2. 승인된 리디렉션 URI에 `https://magic-pension-rebalancer.streamlit.app/oauth2callback`을 등록한다.
3. Streamlit Secrets에 `[auth]` 설정을 추가한다.
4. 기존 `personal` 데이터를 유지할 소유자 이메일을 `LEGACY_OWNER_EMAIL`에 설정한다.
5. 두 개 이상의 Google 계정으로 로그인해 Firestore 데이터가 분리되는지 확인한다.
6. 각 사용자의 Google Sheet를 서비스 계정 이메일에 편집자로 공유한 뒤 양방향 동기화를 테스트한다.
7. 변경 사항을 GitHub `main` 브랜치에 커밋하고 Streamlit 자동 재배포를 확인한다.
8. 필요하면 동기화 충돌 정책(앱 우선, 시트 우선, 최종 수정 시각 비교)을 설계한다.

## 7. 데이터베이스, Firebase, 환경변수 설정

Firebase 프로젝트 ID:

```text
magic-pension-rebalancer-2601
```

Firestore 문서 경로:

```text
streamlit_accounts/{프로필 ID}/accounts/default
```

현재 앱은 Firebase Admin SDK를 사용한다. 따라서 클라이언트용 Firebase 설정값이 아니라 서비스 계정 키가 필요하며, Firestore 보안 규칙과 무관하게 서버 권한으로 접근한다. 별도 Cloud Functions는 사용하지 않는다.

필요한 Streamlit Secrets 구조:

```toml
APP_PASSWORD = "실제 앱 비밀번호"
DEFAULT_PROFILE_ID = "personal"
LEGACY_OWNER_EMAIL = "기존 personal 데이터를 사용할 소유자 이메일"

[auth]
redirect_uri = "https://magic-pension-rebalancer.streamlit.app/oauth2callback"
cookie_secret = "충분히 긴 무작위 문자열"
client_id = "Google OAuth 클라이언트 ID"
client_secret = "Google OAuth 클라이언트 secret"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

[firebase_service_account]
type = "service_account"
project_id = "magic-pension-rebalancer-2601"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
universe_domain = "googleapis.com"
```

대체 환경변수:

- `FIREBASE_SERVICE_ACCOUNT_JSON`: 서비스 계정 JSON 전체 문자열
- `GOOGLE_APPLICATION_CREDENTIALS`: 로컬 서비스 계정 JSON 파일 경로

하나의 `[firebase_service_account]` 설정을 Firestore와 Google Sheets API가 함께 사용한다. Google Sheet 공유 설정에서 이 서비스 계정의 `client_email`에 편집 권한을 부여해야 한다. Google Cloud/Firebase 프로젝트에서 Google Sheets API도 활성화돼 있어야 한다.

민감정보 주의:

- `.streamlit/secrets.toml`과 `*-firebase-adminsdk-*.json`은 절대 커밋하지 않는다.
- 현재 로컬 폴더에 서비스 계정 JSON 파일이 존재한다. `.gitignore`의 `*.json` 규칙으로 제외되어 있지만, Git 상태를 확인한 뒤 커밋해야 한다.
- 실제 앱 비밀번호, private key, private_key_id 등은 이 문서에 기록하지 않았다.
- 키가 노출됐다고 의심되면 Firebase Console에서 기존 서비스 계정 키를 폐기하고 새 키를 발급한다.

## 8. 배포 방법

현재 방식은 GitHub `main` 브랜치와 Streamlit Community Cloud 자동 배포다.

1. GitHub 저장소 `eggT0928/magic-pension-rebalancer-streamlit`을 clone한다.
2. 변경 후 테스트하고 `main` 브랜치에 push한다.
3. Streamlit Community Cloud 앱 설정에서 저장소와 엔트리 파일 `app.py`가 연결되어 있는지 확인한다.
4. Streamlit 앱의 Settings > Secrets에 위 TOML 구조를 설정한다.
5. 필요하면 앱을 Reboot한다. 보통 `main` push 후 자동 재배포된다.
6. https://magic-pension-rebalancer.streamlit.app/ 에서 비밀번호 입력, `Firestore 연결됨`, Sheet 가져오기/내보내기, 가격 새로고침을 확인한다.

이 구조에서는 Firebase Hosting이나 Cloud Functions를 배포할 필요가 없다. Firebase는 Firestore 데이터 저장과 서비스 계정 프로젝트로만 사용하고, 웹 앱 자체는 Streamlit Cloud에서 호스팅한다.

## 9. 주의해야 할 기존 설계와 사용자 요구사항

- 사용자는 Google Sheet와 Streamlit 둘 다 계속 사용하기를 원한다.
- 앱에서 값을 기록할 수 있어야 하며, Google Sheet에서도 값을 수정할 수 있어야 한다.
- Google Sheet 연동은 공개 CSV URL 방식이 아니라 편집용 Sheet와 전용 탭 양방향 동기화 방식이다.
- 제거한 `공개 Google Sheet CSV URL`과 `공개 CSV에서 가져오기` UI를 다시 추가하지 않는다.
- 가격은 Google Sheet 가격만 쓰지 않고 Yahoo Finance를 우선 조회해야 한다.
- 국내 ETF는 Yahoo 실패 시 Naver 가격을 보조로 사용한다.
- 사용자가 입력한 수동현재가는 온라인 가격보다 우선한다.
- 원금, 보유수량 등은 앱 종료 후에도 유지돼야 하므로 Firestore가 기본 저장소다.
- 무료 운영을 선호하므로 현재 단계에서는 Cloud Functions를 추가하지 않는다.
- Google OIDC가 설정되면 사용자는 프로필 ID를 직접 선택하지 않으며 Google 사용자 고유값의 SHA-256 해시가 Firestore 문서 ID가 된다.
- 기존 소유자만 `personal` 데이터를 계속 사용하게 하려면 `LEGACY_OWNER_EMAIL`을 설정한다.
- OIDC가 설정되지 않은 로컬 환경에서는 기존 `APP_PASSWORD`와 프로필 ID 방식이 유지된다.
- 서비스 계정 키와 앱 비밀번호를 코드, README, HANDOFF 또는 GitHub에 평문으로 남기지 않는다.
- Google Sheet 내보내기는 대상 전용 탭 범위를 지우고 다시 쓰므로 사용자가 별도로 관리하는 탭을 동기화 탭 이름으로 지정하지 않도록 한다.

## 10. 다른 Codex가 바로 이어서 작업할 수 있는 시작 프롬프트

```text
이 저장소는 Streamlit + Firestore 기반 연금저축 리밸런서입니다.

먼저 HANDOFF.md와 README.md를 모두 읽고 app.py를 확인해 주세요. 실제 Secrets 값이나 서비스 계정 JSON 내용은 출력하거나 커밋하지 마세요. Google OIDC 로그인과 사용자 고유값 기반 Firestore 분리가 구현되어 있습니다. Google Cloud OAuth 설정 및 Streamlit `[auth]` Secrets가 실제 배포에 적용됐는지 확인하고, 서로 다른 두 Google 계정의 데이터가 분리되는지 테스트해 주세요.

그 다음 Google Sheet 양방향 동기화 흐름을 점검해 주세요. 앱에서 StreamlitSync 탭으로 내보내고, 시트에서 원금/예수금/보유수량/목표비중을 수정한 뒤 앱으로 다시 가져왔을 때 Firestore에도 저장되는지 확인해야 합니다. 기존 공개 CSV URL 입력 UI는 사용자 요청으로 제거했으므로 다시 만들지 마세요.

가격 정책은 수동현재가 > Yahoo Finance/Naver 조회가 > 시트가격이며, KRX 티커는 Yahoo의 .KS/.KQ를 먼저 시도하고 실패하면 Naver를 사용합니다. 무료 운영 요구 때문에 Cloud Functions는 추가하지 않습니다.

변경 전 현재 Git 상태를 확인하고 사용자 변경을 보존하세요. 수정 후 GitHub 저장소 eggT0928/magic-pension-rebalancer-streamlit의 main 브랜치에 반영하고 Streamlit 앱 https://magic-pension-rebalancer.streamlit.app/ 재배포 상태까지 검증해 주세요.
```

