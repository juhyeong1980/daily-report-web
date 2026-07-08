# -*- coding: utf-8 -*-
"""일일보고 웹 서버 (AWS 배포용).

병원 PC가 push한 일일보고 스냅샷을 저장하고, 카카오톡으로 전송된 URL로
친구가 열람할 수 있게 프론트엔드와 조회 API를 제공한다.

환경변수:
    API_KEY     업로드 인증 키 (병원 PC만 보유 — 필수)
    VIEW_TOKEN  열람 토큰 (카톡 URL에 포함 — 필수)
    DATA_DIR    SQLite 저장 경로 (기본: ./data)
"""
import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, request, send_from_directory, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, '..', 'frontend')
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(BASE_DIR, 'data'))
DB_PATH = os.path.join(DATA_DIR, 'reports.db')

API_KEY = os.environ.get('API_KEY', '')
VIEW_TOKEN = os.environ.get('VIEW_TOKEN', '')

KST = timezone(timedelta(hours=9))

app = Flask(__name__)


def _get_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            report_date TEXT PRIMARY KEY,
            payload     TEXT NOT NULL,
            received_at TEXT NOT NULL
        )
    """)
    return conn


def _check_view_token():
    if not VIEW_TOKEN or request.args.get('k') != VIEW_TOKEN:
        abort(403)


# ─────────────────────────────────────────────── 업로드 (병원 PC 전용)
@app.route('/api/reports', methods=['POST'])
def upsert_report():
    if not API_KEY or request.headers.get('X-Api-Key') != API_KEY:
        abort(403)
    body = request.get_json(silent=True) or {}
    report_date = body.get('report_date')
    if not report_date:
        return jsonify({'success': False, 'error': 'report_date 필수'}), 400

    conn = _get_conn()
    conn.execute(
        """INSERT INTO reports (report_date, payload, received_at) VALUES (?, ?, ?)
           ON CONFLICT(report_date) DO UPDATE SET payload=excluded.payload, received_at=excluded.received_at""",
        (report_date, json.dumps(body, ensure_ascii=False), datetime.now(KST).isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'report_date': report_date})


# ─────────────────────────────────────────────── 조회 (열람 토큰 필요)
@app.route('/api/reports/latest', methods=['GET'])
def get_latest():
    _check_view_token()
    conn = _get_conn()
    row = conn.execute('SELECT * FROM reports ORDER BY report_date DESC LIMIT 1').fetchone()
    conn.close()
    if not row:
        return jsonify({'success': False, 'error': '저장된 보고서 없음'}), 404
    return jsonify({'success': True, 'report': json.loads(row['payload']), 'received_at': row['received_at']})


@app.route('/api/reports/<report_date>', methods=['GET'])
def get_report(report_date):
    _check_view_token()
    conn = _get_conn()
    row = conn.execute('SELECT * FROM reports WHERE report_date=?', (report_date,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'success': False, 'error': f'{report_date} 보고서 없음'}), 404
    return jsonify({'success': True, 'report': json.loads(row['payload']), 'received_at': row['received_at']})


@app.route('/api/reports', methods=['GET'])
def list_reports():
    _check_view_token()
    conn = _get_conn()
    rows = conn.execute('SELECT report_date FROM reports ORDER BY report_date DESC LIMIT 60').fetchall()
    conn.close()
    return jsonify({'success': True, 'dates': [r['report_date'] for r in rows]})


# ─────────────────────────────────────────────── 프론트엔드 서빙
@app.route('/')
def index():
    return send_from_directory(os.path.abspath(FRONTEND_DIR), 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(os.path.abspath(FRONTEND_DIR), filename)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), debug=False)
