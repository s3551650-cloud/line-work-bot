# LineWorkBot

Python + Flask + LINE Bot 上班打卡機器人

## 安裝依賴

```bash
pip install -r requirements.txt
```

## 本地測試

1. 設定環境變數：
```bash
export LINE_CHANNEL_SECRET="your_channel_secret"
export LINE_CHANNEL_ACCESS_TOKEN="your_channel_access_token"
export SUPABASE_URL="your_supabase_url"
export SUPABASE_KEY="your_supabase_anon_key"
export RENDER_EXTERNAL_URL="your_ngrok_url"  # 本地測試用
```

2. 啟動伺服器：
```bash
python app.py
```

3. 使用 ngrok 設定 Webhook：
```bash
ngrok http 5000
```

## 部署到 Render

1. 將程式碼推送到 GitHub
2. 在 Render 建立新的 Web Service
3. 設定環境變數
4. 部署完成後設定 LINE Webhook URL

## 功能

- 📝 上班打卡：記錄上班時間，計算下班時間
- ⏰ 下班提醒：自動提醒下班
- 📊 歷史查詢：查看打卡記錄
- ⚙️ 個人設定：調整工作時長、提醒設定