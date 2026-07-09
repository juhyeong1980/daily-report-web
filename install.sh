#!/bin/bash
# ============================================================
# daily-report 서버 설치 (Lightsail Ubuntu 24.04)
# 실행:
#   curl -fsSL https://raw.githubusercontent.com/juhyeong1980/daily-report-web/main/install.sh \
#     | API_KEY=업로드키 VIEW_TOKEN=열람토큰 bash
# 이 스크립트에는 키가 없다. 키는 위 환경변수로만 주입된다.
# ============================================================
set -e

REPO_RAW="https://raw.githubusercontent.com/juhyeong1980/daily-report-web/main"
DOMAIN="report.ugensai.com"

if [ -z "$API_KEY" ] || [ -z "$VIEW_TOKEN" ]; then
  echo "!!! API_KEY / VIEW_TOKEN 환경변수가 필요합니다."
  exit 1
fi

echo "[1/6] 시스템 패키지 설치..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip debian-keyring debian-archive-keyring apt-transport-https curl

echo "[2/6] Caddy 설치 (HTTPS 자동)..."
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
sudo apt-get update -qq && sudo apt-get install -y -qq caddy

echo "[3/6] 앱 파일 다운로드..."
sudo mkdir -p /opt/daily-report/backend /opt/daily-report/frontend /srv/report-data
sudo chown -R ubuntu:ubuntu /opt/daily-report /srv/report-data
curl -fsSL "$REPO_RAW/backend/app.py" -o /opt/daily-report/backend/app.py
curl -fsSL "$REPO_RAW/frontend/index.html" -o /opt/daily-report/frontend/index.html
python3 -c "import ast; ast.parse(open('/opt/daily-report/backend/app.py').read()); print('app.py 무결성 OK')"

echo "[4/6] Python 환경..."
python3 -m venv /opt/daily-report/venv
/opt/daily-report/venv/bin/pip install -q flask gunicorn

echo "[5/6] systemd 서비스..."
sudo tee /etc/systemd/system/daily-report.service >/dev/null <<SVC
[Unit]
Description=daily-report web
After=network.target
[Service]
User=ubuntu
WorkingDirectory=/opt/daily-report/backend
Environment=API_KEY=$API_KEY
Environment=VIEW_TOKEN=$VIEW_TOKEN
Environment=DATA_DIR=/srv/report-data
ExecStart=/opt/daily-report/venv/bin/gunicorn -b 127.0.0.1:8000 -w 2 app:app
Restart=always
[Install]
WantedBy=multi-user.target
SVC
sudo systemctl daemon-reload
sudo systemctl enable --now daily-report

echo "[6/6] Caddy 리버스 프록시..."
sudo tee /etc/caddy/Caddyfile >/dev/null <<CAD
$DOMAIN {
    reverse_proxy 127.0.0.1:8000
}
CAD
sudo systemctl restart caddy

sleep 2
echo ""
echo "=== 상태(둘 다 active여야 정상) ==="
systemctl is-active daily-report
systemctl is-active caddy
echo "=== 업로드 테스트 ==="
curl -s -X POST localhost:8000/api/reports -H "X-Api-Key: $API_KEY" -H "Content-Type: application/json" -d '{"report_date":"2026-07-09","year":"2026","cnt":[10,20,30,0,0,0,0,0,0,0,0,0],"amt":[1000000,2000000,3000000,0,0,0,0,0,0,0,0,0],"memo":"설치 테스트"}'
echo ""
echo ">>> 완료. 브라우저에서 확인: https://$DOMAIN/?k=$VIEW_TOKEN"
