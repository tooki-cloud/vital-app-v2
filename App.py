import os
import io
import re
from flask import Flask, render_template, request, jsonify
from supabase import create_client, Client
from PIL import Image

# Google Cloud Vision API のインポート
try:
    from google.cloud import vision
    HAS_VISION_API = True
    print("Google Cloud Vision APIライブラリの読み込みが完了しました。")
except ImportError:
    HAS_VISION_API = False
    print("Warning: google-cloud-vision がインストールされていません。")

app = Flask(__name__)

SUPABASE_URL = "https://mjlqzhjuarsqivnieakj.supabase.co"
SUPABASE_KEY = "sb_publishable_nd2Ma1EqO5H8kTzreLhLcg_2FDup1Zt"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PASS_FILE = "password.txt"

def get_saved_password():
    if os.path.exists(PASS_FILE):
        with open(PASS_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None

# --- 画面表示用ルーティング ---

@app.route('/')
@app.route('/dash')
def dashboard():
    participant_id = request.args.get('participant_id')
    try:
        query = supabase.table("vitals").select("*")
        if participant_id:
            query = query.eq("participant_id", participant_id)
        
        res = query.order("created_at", asc=True).execute()
        vitals_data = res.data if res.data else []
    except Exception:
        vitals_data = []

    return render_template('Dash.html', vitals=vitals_data)

@app.route('/measure')
def measure():
    return render_template('measure.html')

@app.route('/chat')
def chat():
    return render_template('chat.html')

@app.route('/pharmacist')
def pharmacist_dashboard():
    try:
        res = supabase.table("vitals").select("*").order("created_at", desc=True).execute()
        patients_data = res.data if res.data else []
    except Exception:
        patients_data = []
    
    current_pass = get_saved_password()
    is_password_set = bool(current_pass)
    
    return render_template('pharmacist.html', patients=patients_data, is_password_set=is_password_set)


# --- API エンドポイント ---

# 画像解析・数値検出 API（Google Cloud Vision API 連携）
@app.route('/api/predict-vital', methods=['POST'])
def predict_and_save_vital():
    if 'image' not in request.files:
        return jsonify({'success': False, 'error': '画像ファイルが送信されていません'}), 400

    file = request.files['image']
    participant_id = request.form.get('participant_id', '0001')
    mode = request.form.get('mode', 'bp')

    try:
        image_bytes = file.read()
        image = Image.open(io.BytesIO(image_bytes))

        sys_val, dia_val, pulse_val, weight_val = None, None, None, None
        extracted_text = ""

        # 画像をバイト列に変換してVision APIへ渡す準備
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format=image.format if image.format else 'JPEG')
        image_bytes_data = img_byte_arr.getvalue()

        # Google Cloud Vision API による高精度な文字検出
        if HAS_VISION_API:
            try:
                # ※ "あなたのAPIキーをここに貼り付け" の部分にご自身のGoogle Cloud Vision APIキーを設定してください
                client = vision.ImageAnnotatorClient(client_options={"api_key":"AIzaSyBpHLQIsovNC_8OeddAkyi9A9B9hlZvrZE"})
                
                image_obj = vision.Image(content=image_bytes_data)
                response = client.text_detection(image=image_obj)
                texts = response.text_annotations

                if texts:
                    extracted_text = texts[0].description
                    print(f"Vision API 抽出テキスト:\n{extracted_text}")
                
                if response.error.message:
                    print(f"Vision API エラー: {response.error.message}")

            except Exception as vision_err:
                print(f"Vision API 処理エラー: {vision_err}")

        if mode == 'bp':
            measured_numbers = []
            if HAS_VISION_API and response and response.text_annotations and len(response.text_annotations) > 1:
                for text in response.text_annotations[1:]:
                    desc = text.description
                    
                    # 【対策】「数値:数値」形式（時刻など）が含まれるブロックはスキップする
                    if re.search(r'\d+[:/]\d+', desc):
                        continue
                        
                    match = re.search(r'\d+', desc)
                    if match:
                        val = int(match.group())
                        vertices = text.bounding_poly.vertices
                        if vertices:
                            height = max(v.y for v in vertices) - min(v.y for v in vertices)
                            cy = sum(v.y for v in vertices) / len(vertices)
                            measured_numbers.append({'val': val, 'height': height, 'cy': cy})
            
            # 文字サイズが大きく、かつ血圧・脈拍として妥当な範囲（40〜250）のものを候補にする
            valid_items = [item for item in measured_numbers if 40 <= item['val'] <= 250]
            
            # 文字サイズの大きい順にソート
            valid_items.sort(key=lambda x: x['height'], reverse=True)
            
            # 上位の大きな数字の中から、画面の上から順（cyが小さい順）に3つ並び替えて取得する
            if len(valid_items) >= 3:
                top_three = sorted(valid_items[:6], key=lambda x: x['cy'])
                if len(top_three) >= 3:
                    sys_val = top_three[0]['val']
                    dia_val = top_three[1]['val']
                    pulse_val = top_three[2]['val']
                else:
                    sys_val = valid_items[0]['val']
                    dia_val = valid_items[1]['val']
                    pulse_val = valid_items[2]['val']
            else:
                # フォールバック処理（時刻「数値:数値」の表現を除外してから抽出）
                cleaned_text = re.sub(r'\d+[:/]\d+', '', extracted_text)
                fallback_numbers = [int(num) for num in re.findall(r'\d+', cleaned_text)]
                valid_nums = [n for n in fallback_numbers if 40 <= n <= 250]
                if len(valid_nums) >= 3:
                    sys_val = valid_nums[0]
                    dia_val = valid_nums[1]
                    pulse_val = valid_nums[2]
                else:
                    sys_val, dia_val, pulse_val = 120, 80, 72
        else:
            float_numbers = [float(num) for num in re.findall(r'\d+\.\d+|\d+', extracted_text)]
            if float_numbers:
                valid_weights = [w for w in float_numbers if 20 <= w <= 200]
                weight_val = valid_weights[0] if valid_weights else float_numbers[0]
            else:
                weight_val = 65.5

        record = {
            "participant_id": participant_id,
            "sys": sys_val,
            "dia": dia_val,
            "pulse": pulse_val,
            "weight": weight_val
        }

        return jsonify({
            'success': True,
            'data': record
        })

    except Exception as e:
        print("解析エラー:", e)
        return jsonify({'success': False, 'error': str(e)}), 500


# ID発行API
@app.route('/api/generate-id', methods=['POST'])
def generate_id():
    try:
        response = supabase.rpc('generate_next_participant_id', {}).execute()
        new_id = response.data
        return jsonify({'success': True, 'participant_id': new_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# バイタルデータ（手動・保存ボタン押下時）の保存API
@app.route('/api/vitals', methods=['POST'])
def save_vital():
    data = request.get_json() or {}
    participant_id = data.get('participant_id') or data.get('user_id', '0001')
    try:
        response = supabase.table("vitals").insert({
            "participant_id": participant_id,
            "sys": data.get('sys'),
            "dia": data.get('dia'),
            "pulse": data.get('pulse'),
            "weight": data.get('weight')
        }).execute()
        return jsonify({"success": True, "data": response.data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# チャット取得・送信 API
@app.route('/api/messages', methods=['GET'])
@app.route('/api/chat/history', methods=['GET'])
def get_chat_history():
    participant_id = request.args.get('participant_id') or '0001'

    try:
        response = supabase.table('messages').select('*').eq('participant_id', participant_id).order('created_at', desc=False).execute()
        if request.path == '/api/messages':
            return jsonify(response.data)
        return jsonify({'success': True, 'messages': response.data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/messages', methods=['POST'])
@app.route('/api/chat/send', methods=['POST'])
def send_chat_message():
    data = request.get_json() or {}
    participant_id = data.get('participant_id') or '0001'
    user_message = data.get('message') or data.get('content')
    sender = data.get('sender', 'user')

    if not user_message:
        return jsonify({'success': False, 'error': 'メッセージを入力してください'}), 400

    try:
        res = supabase.table('messages').insert({
            'participant_id': participant_id, 
            'sender': sender, 
            'content': user_message
        }).execute()

        return jsonify({'success': True, 'data': res.data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# 未読状況確認 API
@app.route('/api/messages/unread-status', methods=['GET'])
def get_unread_status():
    try:
        res = supabase.table('messages').select('participant_id, sender, created_at').order('created_at', desc=True).execute()
        messages = res.data or []
        
        latest_msgs = {}
        for msg in messages:
            pid = msg['participant_id']
            if pid not in latest_msgs:
                latest_msgs[pid] = msg
        unread_ids = [pid for pid, msg in latest_msgs.items() if msg.get('sender') == 'user']
        return jsonify(unread_ids)
    except Exception as e:
        return jsonify([]), 500

# 既読処理 API
@app.route('/api/messages/mark-as-read', methods=['POST'])
def mark_as_read():
    return jsonify({'success': True})

@app.route('/api/log', methods=['POST'])
@app.route('/api/logs', methods=['POST'])
def save_log():
    data = request.get_json() or {}
    participant_id = data.get('participant_id') or data.get('user_id', '0001')
    log_text = data.get('log') or data.get('log_action', '')

    if not log_text:
        return jsonify({'success': False, 'message': 'Missing parameters'}), 400

    try:
        response = supabase.table("user_logs").insert({"participant_id": participant_id, "log": log_text}).execute() # ← "user_logs" に変更する
        return jsonify({"success": True, "data": response.data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# 薬剤師認証 API（初回パスワード設定）
@app.route('/api/pharmacist/set-password', methods=['POST'])
def pharmacist_set_password():
    data = request.get_json() or {}
    new_pass = data.get('password')
    if not new_pass or len(new_pass.strip()) == 0:
        return jsonify({"success": False, "message": "パスワードを入力してください"}), 400
        
    with open(PASS_FILE, "w", encoding="utf-8") as f:
        f.write(new_pass.strip())
        
    return jsonify({"success": True})

# 薬剤師認証 API（ログイン）
@app.route('/api/pharmacist/login', methods=['POST'])
def pharmacist_login():
    data = request.get_json() or {}
    password = data.get('password')
    saved_pass = get_saved_password()
    
    if saved_pass and password == saved_pass:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "パスワードが正しくありません"}), 401

# 薬剤師認証 API（パスワード変更）
@app.route('/api/pharmacist/change-password', methods=['POST'])
def pharmacist_change_password():
    data = request.get_json() or {}
    current_pass_input = data.get('current_password')
    new_pass_input = data.get('new_password')
    
    saved_pass = get_saved_password()
    
    if saved_pass and current_pass_input != saved_pass:
        return jsonify({"success": False, "message": "現在のパスワードが正しくありません"}), 400
        
    if not new_pass_input or len(new_pass_input.strip()) == 0:
        return jsonify({"success": False, "message": "新しいパスワードを入力してください"}), 400
        
    with open(PASS_FILE, "w", encoding="utf-8") as f:
        f.write(new_pass_input.strip())
        
    return jsonify({"success": True, "message": "パスワードを変更しました"})
# 未読状況確認 API (患者さん側用) [追加]
@app.route('/api/messages/patient-unread', methods=['GET'])
def patient_unread():
    participant_id = request.args.get('participant_id', '0001')
    try:
        res = supabase.table('messages').select('*').eq('participant_id', participant_id).order('created_at', desc=True).execute()
        messages = res.data or []
        
        if not messages:
            return jsonify({'has_unread': False})
        
        latest_msg = messages[0]
        has_unread = (latest_msg.get('sender') == 'pharmacist')
        
        return jsonify({'has_unread': has_unread})
    except Exception as e:
        return jsonify({'has_unread': False, 'error': str(e)})

# 既読処理 API (患者さん側用) [追加]
@app.route('/api/messages/patient-read', methods=['POST'])
def patient_read():
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
