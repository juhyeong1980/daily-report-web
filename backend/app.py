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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_pending (
            state      TEXT PRIMARY KEY,
            code       TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS approvals (
            report_date TEXT PRIMARY KEY,
            steps_json  TEXT NOT NULL,
            status      TEXT NOT NULL,
            current_seq INTEGER NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
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


# ─────────────────────────────────────────────── 카카오 OAuth 중계 (원격 직원 등록)
# 병원 PC가 등록 링크의 state를 사전 등록 → 직원 폰에서 카카오 로그인 →
# 카카오가 인가코드를 /oauth로 전달 → 병원 PC가 폴링으로 코드를 회수해 토큰 교환.
# 이 서버는 코드를 잠깐 보관만 하며 REST 키·토큰은 전혀 다루지 않는다.

@app.route('/api/oauth-states', methods=['POST'])
def register_oauth_state():
    """병원 PC 전용 — 등록 링크 발급 시 state 사전 등록."""
    if not API_KEY or request.headers.get('X-Api-Key') != API_KEY:
        abort(403)
    body = request.get_json(silent=True) or {}
    state = (body.get('state') or '').strip()
    if not (20 <= len(state) <= 128):
        return jsonify({'success': False, 'error': 'state 형식 오류'}), 400
    conn = _get_conn()
    # 30분 지난 대기 항목 청소
    cutoff = (datetime.now(KST) - timedelta(minutes=30)).isoformat()
    conn.execute('DELETE FROM oauth_pending WHERE created_at < ?', (cutoff,))
    conn.execute(
        'INSERT OR REPLACE INTO oauth_pending (state, code, created_at) VALUES (?, NULL, ?)',
        (state, datetime.now(KST).isoformat()))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/oauth')
def oauth_callback():
    """카카오 로그인 후 직원 폰이 도착하는 곳 — 인가코드를 보관만 한다."""
    code = request.args.get('code', '')
    state = request.args.get('state', '')
    page = ('<!doctype html><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<body style="font-family:-apple-system,\'Malgun Gothic\',sans-serif;'
            'text-align:center;padding:60px 20px;color:#1c1c1e;">{msg}</body>')
    if not code or not state:
        return page.format(msg='<h3>인증 정보가 없습니다</h3><p>등록 링크를 다시 요청하세요.</p>'), 400
    conn = _get_conn()
    row = conn.execute('SELECT state FROM oauth_pending WHERE state=? AND code IS NULL',
                       (state,)).fetchone()
    if row is None:
        conn.close()
        return page.format(msg='<h3>만료되었거나 잘못된 링크입니다</h3>'
                               '<p>담당자에게 새 등록 링크를 요청하세요.</p>'), 400
    conn.execute('UPDATE oauth_pending SET code=? WHERE state=?', (code, state))
    conn.commit()
    conn.close()
    return page.format(msg='<h3>✅ 카카오 인증 완료</h3>'
                           '<p>잠시 후 자동으로 등록되고, 카카오톡 \'나와의 채팅\'으로<br>'
                           '테스트 메시지가 도착합니다. 이 창은 닫으셔도 됩니다.</p>')


@app.route('/api/oauth-states/<state>', methods=['GET'])
def fetch_oauth_code(state):
    """병원 PC 전용 — 코드 도착 확인. 도착했으면 1회 반환 후 삭제."""
    if not API_KEY or request.headers.get('X-Api-Key') != API_KEY:
        abort(403)
    conn = _get_conn()
    row = conn.execute('SELECT code FROM oauth_pending WHERE state=?', (state,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({'success': True, 'status': 'unknown'})
    if not row['code']:
        conn.close()
        return jsonify({'success': True, 'status': 'waiting'})
    conn.execute('DELETE FROM oauth_pending WHERE state=?', (state,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'status': 'ready', 'code': row['code']})


# ─────────────────────────────────────────────── 결재선(승인 체인)
# 병원 PC가 결재 세션을 시작하며 순서대로 결재자 목록(seq/name/token)을 등록한다.
# 각 결재자는 자기 카톡 링크(?approve=token)로 뷰어를 열어 승인/반려한다.
# 병원 PC는 상태를 폴링해 다음 결재자에게 카톡을 보내거나(승인) 담당자에게 알린다(반려).
# 이 서버는 상태만 보관하며 카카오 토큰은 다루지 않는다(push 모델 유지).

def _now():
    return datetime.now(KST).isoformat()


def _acted_msg(state):
    if state == 'approved':
        return '이미 확인하신 보고입니다'
    if state == 'rejected':
        return '이미 수정 요청하신 보고입니다'
    return '이미 종료된 보고입니다'


@app.route('/api/approvals', methods=['POST'])
def start_approval():
    """병원 PC 전용 — 결재 세션 시작(또는 재시작). 순서대로 steps 등록."""
    if not API_KEY or request.headers.get('X-Api-Key') != API_KEY:
        abort(403)
    body = request.get_json(silent=True) or {}
    report_date = body.get('report_date')
    steps = body.get('steps')  # [{seq, name, token}, ...] seq 오름차순
    if not report_date or not isinstance(steps, list) or not steps:
        return jsonify({'success': False, 'error': 'report_date/steps 필수'}), 400
    norm = []
    for i, s in enumerate(steps):
        tok = (s.get('token') or '').strip()
        if len(tok) < 16:
            return jsonify({'success': False, 'error': f'{i}번 token 형식 오류'}), 400
        norm.append({'seq': i, 'name': s.get('name', f'{i+1}번'),
                     'token': tok, 'state': 'pending', 'acted_at': None, 'reason': None})
    conn = _get_conn()
    conn.execute(
        """INSERT INTO approvals (report_date, steps_json, status, current_seq, created_at, updated_at)
           VALUES (?, ?, 'in_progress', 0, ?, ?)
           ON CONFLICT(report_date) DO UPDATE SET
             steps_json=excluded.steps_json, status='in_progress',
             current_seq=0, updated_at=excluded.updated_at""",
        (report_date, json.dumps(norm, ensure_ascii=False), _now(), _now()))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'report_date': report_date, 'steps': len(norm)})


@app.route('/api/approvals/<report_date>/action', methods=['POST'])
def approval_action(report_date):
    """결재자 뷰어에서 호출 — 자기 token으로 승인/반려. 현재 차례만 유효."""
    body = request.get_json(silent=True) or {}
    token = (body.get('token') or '').strip()
    action = body.get('action')
    reason = (body.get('reason') or '')[:200]
    if action not in ('approve', 'reject') or not token:
        return jsonify({'success': False, 'error': '잘못된 요청'}), 400

    conn = _get_conn()
    row = conn.execute('SELECT * FROM approvals WHERE report_date=?', (report_date,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({'success': False, 'error': '결재 세션이 없습니다'}), 404
    steps = json.loads(row['steps_json'])
    status = row['status']
    cur = row['current_seq']
    # 이 token이 몇 번 step인지
    idx = next((i for i, s in enumerate(steps) if s['token'] == token), None)
    if idx is None:
        conn.close()
        return jsonify({'success': False, 'error': '유효하지 않은 결재 링크'}), 403
    me = steps[idx]
    if status != 'in_progress':
        conn.close()
        return jsonify({'success': False, 'error': _acted_msg(me['state']), 'status': status,
                        'my_state': me['state']}), 409
    if idx != cur:
        conn.close()
        if idx < cur:
            return jsonify({'success': False, 'error': _acted_msg(me['state']),
                            'my_state': me['state']}), 409
        return jsonify({'success': False, 'error': '아직 앞 단계 확인이 끝나지 않았습니다'}), 409

    me['acted_at'] = _now()
    me['reason'] = reason or None
    if action == 'approve':
        me['state'] = 'approved'
        is_last = (idx == len(steps) - 1)
        new_status = 'completed' if is_last else 'in_progress'
        new_cur = cur if is_last else cur + 1
    else:
        me['state'] = 'rejected'
        new_status = 'rejected'
        new_cur = cur
    conn.execute(
        'UPDATE approvals SET steps_json=?, status=?, current_seq=?, updated_at=? WHERE report_date=?',
        (json.dumps(steps, ensure_ascii=False), new_status, new_cur, _now(), report_date))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'action': action, 'status': new_status,
                    'name': me['name'],
                    'completed': new_status == 'completed',
                    'rejected': new_status == 'rejected'})


@app.route('/api/approvals/<report_date>/mystate', methods=['GET'])
def approval_mystate(report_date):
    """결재자 뷰어용(공개) — 자기 token의 처리 상태 조회(행위 없음).

    링크를 다시 열었을 때 '이미 확인하신 보고입니다' 등을 먼저 판단하는 용도.
    """
    token = (request.args.get('token') or '').strip()
    if not token:
        return jsonify({'valid': False})
    conn = _get_conn()
    row = conn.execute('SELECT * FROM approvals WHERE report_date=?', (report_date,)).fetchone()
    conn.close()
    if row is None:
        return jsonify({'valid': False})
    steps = json.loads(row['steps_json'])
    idx = next((i for i, s in enumerate(steps) if s['token'] == token), None)
    if idx is None:
        return jsonify({'valid': False})
    me = steps[idx]
    return jsonify({'valid': True, 'my_state': me['state'], 'status': row['status'],
                    'is_current': row['status'] == 'in_progress' and idx == row['current_seq'],
                    'is_waiting': row['status'] == 'in_progress' and idx > row['current_seq'],
                    'acted_msg': _acted_msg(me['state']) if me['state'] != 'pending' else None})


@app.route('/api/approvals/<report_date>', methods=['GET'])
def get_approval(report_date):
    """병원 PC 전용 — 결재 진행 상태 폴링(토큰 제외)."""
    if not API_KEY or request.headers.get('X-Api-Key') != API_KEY:
        abort(403)
    conn = _get_conn()
    row = conn.execute('SELECT * FROM approvals WHERE report_date=?', (report_date,)).fetchone()
    conn.close()
    if row is None:
        return jsonify({'success': True, 'exists': False})
    steps = json.loads(row['steps_json'])
    return jsonify({'success': True, 'exists': True, 'status': row['status'],
                    'current_seq': row['current_seq'],
                    'steps': [{'seq': s['seq'], 'name': s['name'], 'state': s['state'],
                               'acted_at': s['acted_at'], 'reason': s['reason']} for s in steps]})


# ─────────────────────────────────────────────── 프론트엔드 서빙
@app.route('/')
def index():
    return send_from_directory(os.path.abspath(FRONTEND_DIR), 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(os.path.abspath(FRONTEND_DIR), filename)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), debug=False)
