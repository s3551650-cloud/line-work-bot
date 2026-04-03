# LINE 上班打卡機器人 - 部署教學

## 第一步：LINE Developers 設定

### 1. 建立 Provider
1. 前往 [LINE Developers](https://developers.line.biz/)
2. 登入後點擊「建立 Provider」
3. 輸入名稱（如：WorkBot）

### 2. 建立 Messaging API Channel
1. 點擊「建立 Channel」
2. 選擇「Messaging API」
3. 填寫資料：
   - Channel 名稱：WorkBot
   - Channel 描述：上班打卡機器人
   - 類別：工具
   - 子類別：排程工具
4. 同意使用條款並建立

### 3. 取得 Channel 憑證
1. 進入 Channel 設定
2. 記下：
   - **Channel Secret**（在 Basic Settings 頁面）
   - **Channel Access Token**（在 Messaging API 頁面）

### 4. 設定 Webhook
1. 在 Messaging API 頁面
2. Webhook URL 填入：`https://你的render網址/callback`
3. 啟用「使用 Webhook」

### 5. 關閉自動回覆（可選）
- 若要自訂回覆訊息，在「回覆訊息」設定處關閉

---

## 第二步：Supabase 資料庫設定

### 1. 建立帳號
1. 前往 [Supabase](https://supabase.com/)
2. 註冊並驗證 Email

### 2. 建立專案
1. 點擊「New Project」
2. 填寫：
   - Name：workbot
   - Database Password：設定密碼（記下來）
   - Region：選擇東京或新加坡
3. 等待建立完成（約 1 分鐘）

### 3. 建立資料表
1. 進入「SQL Editor」
2. 執行以下 SQL：

```sql
-- 建立 users 資料表
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    line_id TEXT UNIQUE NOT NULL,
    work_hours FLOAT DEFAULT 8.5,
    remind_enabled BOOLEAN DEFAULT TRUE,
    remind_minutes INTEGER DEFAULT 10,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 建立 work_records 資料表
CREATE TABLE work_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    check_in TIMESTAMP NOT NULL,
    scheduled_check_out TIMESTAMP,
    actual_check_out TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 啟用 RLS（資料列安全性）
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE work_records ENABLE ROW LEVEL SECURITY;

-- 設定 users 資料表權限
CREATE POLICY "允許所有人讀取 users" ON users FOR SELECT USING (true);
CREATE POLICY "允許所有人寫入 users" ON users FOR INSERT WITH CHECK (true);
CREATE POLICY "允許所有人更新 users" ON users FOR UPDATE USING (true);

-- 設定 work_records 資料表權限
CREATE POLICY "允許所有人讀取 work_records" ON work_records FOR SELECT USING (true);
CREATE POLICY "允許所有人寫入 work_records" ON work_records FOR INSERT WITH CHECK (true);
```

### 4. 取得連線資訊
1. 進入「Settings」→「API」
2. 記下：
   - **Project URL**（就是 SUPABASE_URL）
   - **anon public** 金鑰（就是 SUPABASE_KEY）

---

## 第三步：Render 部署

### 1. 準備程式碼
1. 將 `app.py`、`requirements.txt`、`README.md` 推送到 GitHub
2. 或直接複製檔案內容

### 2. 建立 Render 帳號
1. 前往 [Render](https://render.com/)
2. 使用 GitHub 帳號登入

### 3. 建立 Web Service
1. 點擊「New」→「Web Service」
2. 連接 GitHub 儲存庫
3. 設定：
   - Name：workbot
   - Environment：Python
   - Build Command：`pip install -r requirements.txt`
   - Start Command：`gunicorn app:app`
4. 點擊「Create Web Service」

### 4. 設定環境變數
在 Render 儀表板中設定：
| 變數名稱 | 值 |
|---------|-----|
| `LINE_CHANNEL_SECRET` | 你的 Channel Secret |
| `LINE_ACCESS_TOKEN` | 你的 Channel Access Token |
| `SUPABASE_URL` | 你的 Supabase URL |
| `SUPABASE_KEY` | 你的 Supabase anon 金鑰 |

### 5. 部署完成
1. 等待部署完成（約 2-3 分鐘）
2. 記下自動分配的網址（如：`https://workbot-xxx.onrender.com`）

---

## 第四步：設定 LINE Webhook

1. 回到 LINE Developers
2. 在 Messaging API 頁面
3. Webhook URL 改為：`https://你的render網址/callback`
4. 點擊「Verify」確認連線成功

---

## 第五步：設定 Rich Menu（圖文選單）

1. 前往：`https://你的render網址/setup-richmenu`
2. 瀏覽器會自動建立三個按鈕的選單
3. 回到 LINE App 查看，應該能看到選單

---

## 環境變數總覽

```
LINE_CHANNEL_SECRET=xxx
LINE_ACCESS_TOKEN=xxx
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=xxx
```

---

## 常見問題

### Q: 打卡後沒有回應
A: 檢查 Webhook 是否正確設定，確認 Render 日誌沒有錯誤

### Q: 提醒沒有發送
A: Render 免費方案會進入睡眠，請確保每個月有訪問過網站

### Q: 如何修改時長？
A: 輸入數字（如 8.5）或使用設定選單

---

## 費用

| 服務 | 費用 |
|------|------|
| LINE 官方帳號 | 免費 |
| Supabase | 免費（500MB 資料庫） |
| Render | 免費（750 小時/月） |

100 人使用完全免費！