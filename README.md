# daily-report-web — 일일보고 웹 뷰어

병원 PC(관리 포털)가 push한 일일보고 스냅샷을 저장·열람하는 서버.
카카오톡으로 전송되는 "일일보고 보기" URL이 이 서버를 가리킨다.

```
[병원 PC 관리포털] --(완료 버튼 → POST /api/reports, X-Api-Key)--> [이 서버]
[친구 폰 브라우저] <--(카톡 URL: /?date=YYYY-MM-DD&k=VIEW_TOKEN)--- [이 서버]
```

## 구성
- `backend/app.py` — Flask API (업로드/조회) + 프론트엔드 서빙
- `frontend/index.html` — 열람 페이지 (월별 건수/금액 표 + 메모)
- 저장소: SQLite (`DATA_DIR/reports.db`)

## 환경변수
| 변수 | 용도 |
|---|---|
| `API_KEY` | 업로드 인증 키. 병원 PC의 `data/kakao_config.json`의 `publish_api_key`와 동일해야 함 |
| `VIEW_TOKEN` | 열람 토큰. 카톡 URL의 `?k=` 값 |
| `DATA_DIR` | SQLite 저장 경로 (Docker에서는 `/data`) |

키 생성 예: `python -c "import secrets; print(secrets.token_urlsafe(24))"` (두 번 실행해 각각 사용)

## AWS 배포 (Lightsail 권장 — 월 $5, 가장 단순)

1. **인스턴스 생성**: Lightsail → OS 전용 Ubuntu 22.04, 최저 플랜 → 고정 IP 연결
2. **방화벽**: Lightsail 네트워킹 탭에서 HTTPS(443), HTTP(80) 허용
3. **접속 후 설치**:
   ```bash
   sudo apt update && sudo apt install -y docker.io
   git clone <이 repo URL> && cd daily-report-web
   sudo docker build -t daily-report .
   sudo docker run -d --name daily-report --restart unless-stopped \
     -p 8000:8000 -v /srv/report-data:/data \
     -e API_KEY='<업로드키>' -e VIEW_TOKEN='<열람토큰>' daily-report
   ```
4. **HTTPS**: 도메인이 있으면 Caddy가 가장 간단 (자동 인증서):
   ```bash
   sudo apt install -y caddy
   echo 'report.example.com {
       reverse_proxy localhost:8000
   }' | sudo tee /etc/caddy/Caddyfile && sudo systemctl restart caddy
   ```
   도메인이 없으면 `http://<고정IP>:8000` 으로도 동작하지만, 카카오 링크 버튼용
   도메인 등록과 보안을 위해 저가 도메인 + HTTPS 권장.

## 카카오디벨로퍼스 연동
- [내 애플리케이션 > 플랫폼 > Web]에 이 서버의 도메인 등록
  (등록 안 하면 메시지의 "일일보고 보기" 버튼 링크가 거부됨)

## 병원 PC 쪽 설정
`DB/data/kakao_config.json`에 추가:
```json
{
  "publish_api_url": "https://report.example.com",
  "publish_api_key": "<업로드키>",
  "publish_view_token": "<열람토큰>"
}
```
이후 관리 포털에서 "💾 일일예약현황 DB에 저장" 성공 시
자동으로 이 서버에 업로드되고, 카톡 메시지의 링크가 이 서버 URL로 나간다.

## 로컬 테스트
```bash
cd backend
pip install -r requirements.txt
API_KEY=test VIEW_TOKEN=test python app.py
# 업로드: curl -X POST localhost:8000/api/reports -H "X-Api-Key: test" -H "Content-Type: application/json" -d '{"report_date":"2026-07-09","year":"2026","cnt":[1,2,3,0,0,0,0,0,0,0,0,0],"amt":[100,200,300,0,0,0,0,0,0,0,0,0],"memo":"테스트"}'
# 열람: http://localhost:8000/?k=test
```
