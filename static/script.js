async function sendLog(actionDetail) {
    const participantId = localStorage.getItem('participant_id');
    if (!participantId) return; // IDがない場合は送信しない

    try {
        await fetch('/api/log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                participant_id: participantId,
                log: actionDetail
            })
        });
    } catch (e) {
        console.error('ログ送信エラー:', e);
    }
}

// --- ボタンタップ・画面遷移時のログ記録付き関数（ログ送信完了を待って遷移） ---
async function navigateWithLog(url, actionName) {
    await sendLog(`ボタンタップ: ${actionName}`);
    window.location.href = url;
}

// --- DOM読み込み完了時の処理 ---
document.addEventListener('DOMContentLoaded', () => {
    const savedId = localStorage.getItem('participant_id');

    if (savedId) {
        // すでにIDが保存されている場合（2回目以降）
        updateIdDisplay(savedId);
        
        // 画面表示ログをDBへ自動送信
        sendLog('ダッシュボード画面を表示');
    } else {
        // IDが未保存の場合（初回アクセス）
        const overlay = document.getElementById('start-overlay');
        if (overlay) {
            overlay.style.display = 'flex';
        }
    }
});

// --- 「使用を開始する」ボタン押下時の処理 ---
async function issueParticipantId() {
    const btn = document.getElementById('start-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = "IDを発行中...";
    }

    try {
        // バックエンド（Flask）から連番IDを取得
        const response = await fetch('/api/generate-id', { method: 'POST' });
        const data = await response.json();

        if (data.success || data.participant_id) {
            const newId = data.participant_id; // バックエンドから受け取ったID (例: "0001")
            
            // localStorage にIDを保存
            localStorage.setItem('participant_id', newId);
            
            // 初回登録ログを送信
            await sendLog('新規利用開始（ID発行完了）');

            // IDをクエリパラメータに載せてリロード
            window.location.href = `/?participant_id=${newId}`;
        } else {
            alert("IDの発行に失敗しました: " + (data.error || "不明なエラー"));
            if (btn) {
                btn.disabled = false;
                btn.textContent = "使用を開始する";
            }
        }
    } catch (error) {
        console.error("Error generating ID:", error);
        alert("通信エラーが発生しました。ネットワーク状況を確認してください。");
        if (btn) {
            btn.disabled = false;
            btn.textContent = "使用を開始する";
        }
    }
}

// --- 研究者ID表示の更新関数 ---
function updateIdDisplay(id) {
    const idElement = document.getElementById('researcher-id');
    if (idElement) {
        idElement.textContent = id;
    }
}

// --- カメラの起動処理 ---
function startCamera() {
    sendLog('カメラを起動');

    const cameraInput = document.createElement('input');
    cameraInput.type = 'file';
    cameraInput.accept = 'image/*';
    
    // スマホの背面カメラを優先起動
    cameraInput.setAttribute('capture', 'environment');

    cameraInput.onchange = function(event) {
        const file = event.target.files[0];
        if (file) {
            uploadImage(file);
        }
    };

    cameraInput.click();
}

// --- 画像送信処理（軽量化リサイズ対応版） ---
function uploadImage(file) {
    const currentUserId = localStorage.getItem('participant_id') || '0001';

    console.log(`画像をリサイズ中... (研究者ID: ${currentUserId})`);
    sendLog('OCR画像解析をリクエスト送信準備');

    // Canvasを使って画像をリサイズ・圧縮してから送信する
    const reader = new FileReader();
    reader.onload = function(e) {
        const img = new Image();
        img.onload = function() {
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');

            // 最大幅を1200pxに制限してアスペクト比を維持
            const maxWidth = 1200;
            let width = img.width;
            let height = img.height;

            if (width > maxWidth) {
                height = Math.round((height * maxWidth) / width);
                width = maxWidth;
            }

            canvas.width = width;
            canvas.height = height;
            ctx.drawImage(img, 0, 0, width, height);

            // JPEG形式、品質 0.8 で圧縮して Blob に変換
            canvas.toBlob((blob) => {
                const resizedFile = new File([blob], file.name || 'photo.jpg', {
                    type: 'image/jpeg',
                    lastModified: Date.now()
                });

                const formData = new FormData();
                formData.append('image', resizedFile);
                formData.append('participant_id', currentUserId);
                formData.append('user_id', currentUserId); // 互換性のため両方送信

                console.log(`リサイズ後の画像を送信中...`);

                // タイムアウト監視付きの fetch (約30秒)
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 30000);

                fetch('/api/ocr-upload', {
                    method: 'POST',
                    body: formData,
                    signal: controller.signal
                })
                .then(response => {
                    clearTimeout(timeoutId);
                    if (!response.ok) {
                        throw new Error('サーバーエラーが発生しました');
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.success) {
                        const ext = data.extracted_data;
                        const msg = `画像の送信・保存に成功しました！\n\n【読み取り結果】\n最高血圧: ${ext.sys || '未検出'}\n最低血圧: ${ext.dia || '未検出'}\n脈拍: ${ext.pulse || '未検出'}\n体重: ${ext.weight || '未検出'}`;
                        
                        sendLog('OCR画像解析およびDB保存成功');
                        alert(msg);
                        console.log("Supabase保存完了データ:", data);
                    } else {
                        sendLog('OCR画像解析エラー発生');
                        alert("送信エラー: " + (data.message || data.error));
                    }
                })
                .catch(error => {
                    clearTimeout(timeoutId);
                    console.error("通信エラー:", error);
                    sendLog('OCR画像解析の通信失敗');
                    if (error.name === 'AbortError') {
                        alert("通信がタイムアウトしました。サーバーがスリープ状態から復帰中の場合は、もう一度お試しください。");
                    } else {
                        alert("通信に失敗しました。サーバーの動作を確認してください。");
                    }
                });

            }, 'image/jpeg', 0.8);
        };
        img.src = e.target.result;
    };
    reader.readAsDataURL(file);
}
