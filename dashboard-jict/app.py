from datetime import datetime, timedelta
import os
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request, send_from_directory
import pandas as pd
import requests

app = Flask(__name__)

UPLOAD_FOLDER = os.path.dirname(os.path.abspath(__file__))
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

LOGIN_URL = 'https://dashboard.ujbgroup.com/login'
AUTH_URL = 'https://dashboard.ujbgroup.com/login/auth'
TARGET_URL = (
    'https://dashboard.ujbgroup.com/data/transaction_data/get_data_detail_transaction_jict'
)

UJB_USERNAME = os.environ.get('UJB_USERNAME', 'staffjict')
UJB_PASSWORD = os.environ.get('UJB_PASSWORD', 'staffjict')

MANUAL_COOKIE_NAME = 'ci_session'
MANUAL_COOKIE_VALUE = ''

session = requests.Session()


def initialize_session():
  print('\n--- INISIALISASI SESI & KONEKSI UJB ---')
  if MANUAL_COOKIE_VALUE.strip() != '':
    session.cookies.set(
        MANUAL_COOKIE_NAME, MANUAL_COOKIE_VALUE, domain='dashboard.ujbgroup.com'
    )
    return True
  try:
    get_res = session.get(LOGIN_URL, timeout=15)
    soup = BeautifulSoup(get_res.text, 'html.parser')
    payload = {}
    for input_tag in soup.find_all('input'):
      name = input_tag.get('name')
      value = input_tag.get('value', '')
      if name:
        payload[name] = value

    payload['username'] = UJB_USERNAME
    payload['password'] = UJB_PASSWORD

    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            ' (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ),
        'Referer': LOGIN_URL,
        'Origin': 'https://dashboard.ujbgroup.com',
    }
    res = session.post(
        AUTH_URL, data=payload, headers=headers, allow_redirects=True, timeout=15
    )
    return 'login' not in res.url
  except Exception as e:
    print('ERROR KONEKSI:', str(e))
    return False


def load_local_mapping():
  excel_files = [
      f
      for f in os.listdir(app.config['UPLOAD_FOLDER'])
      if f.endswith('.xls') or f.endswith('.xlsx')
  ]
  if not excel_files:
    return {}

  prod_map = {}
  for file in excel_files:
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file)
    try:
      try:
        df = pd.read_excel(filepath, engine='calamine')
      except Exception:
        df = pd.read_excel(filepath)

      df.columns = [str(c).strip().lower() for c in df.columns]

      if any('trc' in c or 'box' in c for c in df.columns):
        date_col = next(
            (c for c in df.columns if 'date' in c or 'tgl' in c), df.columns[0]
        )
        id_col = next(
            (c for c in df.columns if 'id' in c or 'trc' in c), df.columns[1]
        )
        box_col = next((c for c in df.columns if 'box' in c), df.columns[-1])

        df['tgl_str'] = pd.to_datetime(
            df[date_col], errors='coerce'
        ).dt.strftime('%Y-%m-%d')
        df['asset_code'] = 'HT ' + df[id_col].astype(str).str.strip()

        for _, row in df.iterrows():
          tgl = str(row['tgl_str'])
          asset = str(row['asset_code'])
          box_val = float(row[box_col]) if pd.notnull(row[box_col]) else 0.0
          prod_map[(tgl, asset)] = box_val
    except Exception as e:
      print(f'Gagal memproses file lokal {file}: {e}')

  return prod_map


@app.route('/')
def index():
  return render_template('index.html')


@app.route('/api/files', methods=['GET'])
def list_files():
  excel_files = [
      f
      for f in os.listdir(app.config['UPLOAD_FOLDER'])
      if f.endswith('.xls') or f.endswith('.xlsx')
  ]
  return jsonify({'status': True, 'files': excel_files})


@app.route('/api/file/delete', methods=['POST'])
def delete_file():
  data = request.get_json()
  filename = data.get('filename')
  if filename:
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(filepath):
      os.remove(filepath)
      return jsonify({
          'status': True,
          'message': f'File {filename} berhasil dihapus.',
      })
  return jsonify({'status': False, 'error': 'File tidak ditemukan.'})


@app.route('/api/upload', methods=['POST'])
def upload_file():
  uploaded_files = request.files.getlist('file')
  saved_count = 0
  for file in uploaded_files:
    if file.filename != '' and (
        file.filename.endswith('.xls') or file.filename.endswith('.xlsx')
    ):
      filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
      file.save(filepath)
      saved_count += 1
  return jsonify({
      'status': True,
      'message': (
          f'Berhasil mengunggah {saved_count} file pengaya ke folder lokal!'
      ),
  })


@app.route('/api/data')
def get_data():
  try:
    today = datetime.now()
    today_str = today.strftime('%Y-%m-%d')
    start_date = (today - timedelta(days=180)).strftime('%Y-%m-%d')

    payload = {
        'startDate': start_date,
        'endDate': today_str,
        'site': 'site_tj_priuk',
        'idLoc': '14',
        'unit': '',
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest',
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            ' (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ),
        'Referer': 'https://dashboard.ujbgroup.com/',
    }

    response = session.post(
        TARGET_URL, data=payload, headers=headers, timeout=20
    )
    try:
      json_res = response.json()
    except Exception:
      return jsonify({
          'status': False,
          'error': 'Sesi belum terautentikasi penuh.',
          'data': [],
      })

    if isinstance(json_res, dict) and 'data' in json_res:
      prod_map = load_local_mapping()
      if prod_map and isinstance(json_res['data'], list):
        for item in json_res['data']:
          tgl = item.get('tgl', '')
          asset = (item.get('asset_no') or '').strip()
          item['total_box'] = prod_map.get((tgl, asset), 0)

    return jsonify(json_res)
  except Exception as e:
    return jsonify({'status': False, 'error': str(e), 'data': []})


if __name__ == '__main__':
  print('Menjalankan Inisialisasi Dashboard UJB...')
  initialize_session()
  app.run(debug=True, port=5000)