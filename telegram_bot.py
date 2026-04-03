import os
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_ACCESS_TOKEN', '')
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

scheduler = BackgroundScheduler()
scheduler.start()

def get_taiwan_time():
    return datetime.utcnow() + timedelta(hours=8)

def send_telegram_message(chat_id, text, buttons=None):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    data = {'chat_id': chat_id, 'text': text}
    if buttons:
        data['reply_markup'] = {'keyboard': buttons, 'resize_keyboard': True}
    requests.post(url, json=data)

def send_inline_buttons(chat_id, text, buttons):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    keyboard = [[{'text': btn['text'], 'callback_data': btn['data']}] for btn in buttons]
    data = {
        'chat_id': chat_id, 
        'text': text,
        'reply_markup': {'inline_keyboard': keyboard}
    }
    requests.post(url, json=data)

def get_main_buttons():
    return [
        [{'text': '上班', 'callback_data': 'check_in'}],
        [{'text': '測試', 'callback_data': 'test'}],
        [{'text': '歷史記錄', 'callback_data': 'history'}],
        [{'text': '設定', 'callback_data': 'settings'}],
    ]

def supabase_request(table, method='GET', data=None, filters=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation'
    }
    
    if filters:
        query = '&'.join([f"{k}={v}" for k, v in filters.items()])
        url = f"{url}?{query}"
    
    try:
        if method == 'GET':
            response = requests.get(url, headers=headers)
        elif method == 'POST':
            response = requests.post(url, headers=headers, json=data)
        elif method == 'PATCH':
            response = requests.patch(url, headers=headers, json=data)
        
        if response.status_code in [200, 201, 206]:
            return response.json()
        else:
            logger.error(f"Supabase error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Supabase request error: {e}")
        return None

def get_or_create_user(telegram_id: str):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    
    result = supabase_request('telegram_users', filters={'telegram_id': f'eq.{telegram_id}'})
    
    if result and len(result) > 0:
        return result[0]
    
    user_data = {
        'telegram_id': telegram_id,
        'work_hours': 8.5,
        'remind_enabled': True,
        'remind_minutes': 10
    }
    
    new_user = supabase_request('telegram_users', method='POST', data=user_data)
    return new_user[0] if new_user else None

def update_user_settings(telegram_id: str, **kwargs):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    
    for key, value in kwargs.items():
        supabase_request('telegram_users', method='PATCH', 
                       data={key: value},
                       filters={'telegram_id': f'eq.{telegram_id}'})
    return True

def record_check_in(telegram_id: str, chat_id: str):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    
    user = get_or_create_user(telegram_id)
    if not user:
        return None
    
    check_in = get_taiwan_time()
    work_hours = user.get('work_hours', 8.5)
    scheduled_check_out = check_in + timedelta(hours=work_hours)
    
    record_data = {
        'user_id': user['id'],
        'check_in': check_in.isoformat(),
        'scheduled_check_out': scheduled_check_out.isoformat(),
        'chat_id': str(chat_id)
    }
    
    record = supabase_request('telegram_work_records', method='POST', data=record_data)
    
    schedule_reminders(user, check_in, scheduled_check_out, chat_id)
    
    return {
        'record': record[0] if record else None,
        'scheduled_check_out': scheduled_check_out,
        'work_hours': work_hours
    }

def schedule_reminders(user, check_in, scheduled_check_out, chat_id):
    from apscheduler.triggers.date import DateTrigger
    
    telegram_id = user['telegram_id']
    remind_enabled = user.get('remind_enabled', True)
    remind_minutes = user.get('remind_minutes', 10)
    
    job_id_prefix = f"{telegram_id}_{check_in.strftime('%Y%m%d%H%M%S')}"
    
    if remind_enabled:
        early_remind_time = scheduled_check_out - timedelta(minutes=remind_minutes)
        if early_remind_time > get_taiwan_time():
            scheduler.add_job(
                send_telegram_reminder,
                trigger=DateTrigger(run_date=early_remind_time),
                args=[chat_id, early_remind_time, remind_minutes],
                id=f"{job_id_prefix}_early",
                replace_existing=True
            )
    
    scheduler.add_job(
        send_telegram_reminder,
        trigger=DateTrigger(run_date=scheduled_check_out),
        args=[chat_id, scheduled_check_out, 0],
        id=f"{job_id_prefix}_main",
        replace_existing=True
    )

def send_telegram_reminder(chat_id, check_out_time, minutes):
    try:
        if minutes > 0:
            message = f"⏰ 提前 {minutes} 分鐘提醒：\n您的下班時間快到了！\n預定下班時間：{check_out_time.strftime('%H:%M')}"
        else:
            message = f"🎉 下班時間到了！\n辛苦您了，可以下班了！\n下班時間：{check_out_time.strftime('%H:%M')}"
        
        send_telegram_message(chat_id, message)
        logger.info(f"已發送提醒給 {chat_id}")
    except Exception as e:
        logger.error(f"發送提醒失敗: {e}")

def get_user_history(telegram_id: str, limit: int = 10):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    
    user = get_or_create_user(telegram_id)
    if not user:
        return []
    
    records = supabase_request('telegram_work_records', 
                             filters={'user_id': f"eq.{user['id']}"})
    if records:
        records = sorted(records, key=lambda x: x.get('check_in', ''), reverse=True)[:limit]
    return records or []

def format_history_message(records):
    if not records:
        return "尚無打卡記錄"
    
    message = "📊 最近打卡記錄：\n\n"
    
    for i, record in enumerate(records, 1):
        check_in = record.get('check_in', '')
        scheduled = record.get('scheduled_check_out')
        
        if isinstance(check_in, str):
            try:
                check_in = datetime.fromisoformat(check_in.replace('Z', '+00:00'))
            except:
                pass
        
        if isinstance(scheduled, str):
            try:
                scheduled = datetime.fromisoformat(scheduled.replace('Z', '+00:00'))
            except:
                pass
        
        date_str = check_in.strftime('%Y/%m/%d') if isinstance(check_in, datetime) else 'N/A'
        time_str = check_in.strftime('%H:%M') if isinstance(check_in, datetime) else 'N/A'
        
        if scheduled:
            scheduled_str = scheduled.strftime('%H:%M') if isinstance(scheduled, datetime) else 'N/A'
            message += f"{i}. {date_str}\n   上班：{time_str} → 下班：{scheduled_str}\n"
        else:
            message += f"{i}. {date_str} 上班 {time_str}\n"
    
    return message

def get_user_settings_message(user):
    work_hours = user.get('work_hours', 8.5)
    remind_enabled = user.get('remind_enabled', True)
    remind_minutes = user.get('remind_minutes', 10)
    
    message = f"⚙️ 目前設定：\n\n"
    message += f"• 工作時長：{work_hours} 小時\n"
    message += f"• 提前提醒：{'開啟' if remind_enabled else '關閉'}\n"
    if remind_enabled:
        message += f"• 提前分鐘：{remind_minutes} 分鐘\n"
    
    return message

@app.route("/")
def index():
    return "Telegram Bot 運作中"

@app.route(f"/telegram/webhook", methods=['POST'])
def telegram_webhook():
    data = request.get_json()
    
    if 'callback_query' in data:
        query = data['callback_query']
        chat_id = query['message']['chat']['id']
        user_id = str(query['from']['id'])
        data_cb = query['data']
        
        if data_cb == 'check_in':
            result = record_check_in(user_id, chat_id)
            if result:
                work_hours = result['work_hours']
                scheduled = result['scheduled_check_out']
                user = get_or_create_user(user_id)
                
                message_text = f"✅ 上班打卡成功！\n\n"
                message_text += f"上班時間：{get_taiwan_time().strftime('%H:%M')}\n"
                message_text += f"預定下班：{scheduled.strftime('%H:%M')}\n"
                message_text += f"工作時長：{work_hours} 小時\n\n"
                message_text += f"⏰ 系統會在下班時間提醒您！"
                
                if user and user.get('remind_enabled', True):
                    remind_min = user.get('remind_minutes', 10)
                    early_time = scheduled - timedelta(minutes=remind_min)
                    message_text += f"\n⚡ 提前 {remind_min} 分鐘也會提醒您"
            else:
                message_text = "❌ 打卡失敗，請稍後再試"
            
            send_telegram_message(chat_id, message_text, get_main_buttons())
        
        elif data_cb == 'test':
            test_check_in = get_taiwan_time()
            test_check_out = test_check_in + timedelta(seconds=10)
            
            from apscheduler.triggers.date import DateTrigger
            
            job_id = f"test_{user_id}"
            scheduler.add_job(
                send_telegram_reminder,
                trigger=DateTrigger(run_date=test_check_out),
                args=[chat_id, test_check_out, 0],
                id=job_id,
                replace_existing=True
            )
            
            message_text = f"✅ 測試打卡成功！\n\n"
            message_text += f"上班時間：{test_check_in.strftime('%H:%M:%S')}\n"
            message_text += f"預定下班：{test_check_out.strftime('%H:%M:%S')}\n"
            message_text += f"⏰ 10秒後收到提醒！"
            
            send_telegram_message(chat_id, message_text, get_main_buttons())
        
        elif data_cb == 'history':
            records = get_user_history(user_id, 10)
            message_text = format_history_message(records)
            send_telegram_message(chat_id, message_text, get_main_buttons())
        
        elif data_cb == 'settings':
            user = get_or_create_user(user_id)
            if user:
                work_hours = user.get('work_hours', 8.5)
                remind_enabled = user.get('remind_enabled', True)
                remind_minutes = user.get('remind_minutes', 10)
                
                message_text = f"⚙️ 設定選項：\n\n"
                message_text += f"• 工作時長：{work_hours} 小時\n"
                message_text += f"• 提前提醒：{'開啟' if remind_enabled else '關閉'}\n"
                if remind_enabled:
                    message_text += f"• 提前分鐘：{remind_minutes} 分鐘\n"
                
                send_inline_buttons(chat_id, message_text, [
                    {'text': f'8 小時', 'data': 'hours_8'},
                    {'text': f'8.5 小時', 'data': 'hours_8.5'},
                    {'text': f'9 小時', 'data': 'hours_9'},
                    {'text': f'提醒開', 'data': 'remind_on'},
                    {'text': f'提醒關', 'data': 'remind_off'},
                    {'text': f'5 分鐘', 'data': 'min_5'},
                    {'text': f'10 分鐘', 'data': 'min_10'},
                    {'text': f'返回主選單', 'data': 'back'},
                ])
        
        elif data_cb.startswith('hours_'):
            hours = float(data_cb.split('_')[1])
            update_user_settings(user_id, work_hours=hours)
            send_telegram_message(chat_id, f"✅ 工作時長已設定為 {hours} 小時", get_main_buttons())
        
        elif data_cb == 'remind_on':
            update_user_settings(user_id, remind_enabled=True)
            send_telegram_message(chat_id, "✅ 提前提醒已開啟", get_main_buttons())
        
        elif data_cb == 'remind_off':
            update_user_settings(user_id, remind_enabled=False)
            send_telegram_message(chat_id, "✅ 提前提醒已關閉", get_main_buttons())
        
        elif data_cb.startswith('min_'):
            minutes = int(data_cb.split('_')[1])
            update_user_settings(user_id, remind_minutes=minutes)
            send_telegram_message(chat_id, f"✅ 提前提醒分鐘已設定為 {minutes} 分鐘", get_main_buttons())
        
        elif data_cb == 'back':
            send_telegram_message(chat_id, "請選擇功能：", get_main_buttons())
        
        return jsonify({'status': 'ok'})
    
    if 'message' not in data:
        return jsonify({'status': 'ok'})
    
    message = data['message']
    chat_id = message['chat']['id']
    user_id = str(message['from']['id'])
    text = message.get('text', '')
    
    if text == '/start':
        send_telegram_message(chat_id, "👋 歡迎使用上班打卡機器人！\n\n點擊下方按鈕開始使用：", get_main_buttons())
    
    elif text == '上班':
        result = record_check_in(user_id, chat_id)
        if result:
            work_hours = result['work_hours']
            scheduled = result['scheduled_check_out']
            
            message_text = f"✅ 上班打卡成功！\n\n"
            message_text += f"上班時間：{get_taiwan_time().strftime('%H:%M')}\n"
            message_text += f"預定下班：{scheduled.strftime('%H:%M')}\n"
            message_text += f"工作時長：{work_hours} 小時\n\n"
            message_text += f"⏰ 系統會在下班時間提醒您！"
            
            user = get_or_create_user(user_id)
            if user and user.get('remind_enabled', True):
                remind_min = user.get('remind_minutes', 10)
                early_time = scheduled - timedelta(minutes=remind_min)
                message_text += f"\n⚡ 提前 {remind_min} 分鐘也會提醒您"
        else:
            message_text = "❌ 打卡失敗，請稍後再試"
        
        send_telegram_message(chat_id, message_text, get_main_buttons())
    
    elif text == '測試':
        test_check_in = get_taiwan_time()
        test_check_out = test_check_in + timedelta(seconds=10)
        
        from apscheduler.triggers.date import DateTrigger
        
        job_id = f"test_{user_id}"
        scheduler.add_job(
            send_telegram_reminder,
            trigger=DateTrigger(run_date=test_check_out),
            args=[chat_id, test_check_out, 0],
            id=job_id,
            replace_existing=True
        )
        
        message_text = f"✅ 測試打卡成功！\n\n"
        message_text += f"上班時間：{test_check_in.strftime('%H:%M:%S')}\n"
        message_text += f"預定下班：{test_check_out.strftime('%H:%M:%S')}\n"
        message_text += f"⏰ 10秒後收到提醒！"
        
        send_telegram_message(chat_id, message_text, get_main_buttons())
    
    elif text == '歷史':
        records = get_user_history(user_id, 10)
        message_text = format_history_message(records)
        send_telegram_message(chat_id, message_text, get_main_buttons())
    
    elif text == '設定':
        user = get_or_create_user(user_id)
        if user:
            work_hours = user.get('work_hours', 8.5)
            remind_enabled = user.get('remind_enabled', True)
            remind_minutes = user.get('remind_minutes', 10)
            
            message_text = f"⚙️ 設定選項：\n\n"
            message_text += f"• 工作時長：{work_hours} 小時\n"
            message_text += f"• 提前提醒：{'開啟' if remind_enabled else '關閉'}\n"
            if remind_enabled:
                message_text += f"• 提前分鐘：{remind_minutes} 分鐘\n"
            
            send_inline_buttons(chat_id, message_text, [
                {'text': f'7 小時', 'data': 'hours_7'},
                {'text': f'8 小時', 'data': 'hours_8'},
                {'text': f'8.5 小時', 'data': 'hours_8.5'},
                {'text': f'9 小時', 'data': 'hours_9'},
                {'text': f'提醒開', 'data': 'remind_on'},
                {'text': f'提醒關', 'data': 'remind_off'},
                {'text': f'5 分鐘', 'data': 'min_5'},
                {'text': f'10 分鐘', 'data': 'min_10'},
                {'text': f'返回主選單', 'data': 'back'},
            ])
    
    elif text in ['提醒開', '提醒開']:
        update_user_settings(user_id, remind_enabled=True)
        send_telegram_message(chat_id, "✅ 提前提醒已開啟", get_main_buttons())
    
    elif text in ['提醒關', '提醒關']:
        update_user_settings(user_id, remind_enabled=False)
        send_telegram_message(chat_id, "✅ 提前提醒已關閉", get_main_buttons())
    
    elif text.endswith('分') and text[:-1].isdigit():
        minutes = int(text[:-1])
        if 1 <= minutes <= 60:
            update_user_settings(user_id, remind_minutes=minutes)
            send_telegram_message(chat_id, f"✅ 提前提醒分鐘已設定為 {minutes} 分鐘", get_main_buttons())
    
    else:
        try:
            hours = float(text)
            if 1 <= hours <= 24:
                update_user_settings(user_id, work_hours=hours)
                send_telegram_message(chat_id, f"✅ 工作時長已設定為 {hours} 小時", get_main_buttons())
                return jsonify({'status': 'ok'})
        except ValueError:
            pass
        
        send_telegram_message(chat_id, "👋 請選擇功能：", get_main_buttons())
    
    return jsonify({'status': 'ok'})

@app.route(f"/telegram/setwebhook", methods=['GET'])
def set_webhook():
    webhook_url = os.environ.get('TELEGRAM_WEBHOOK_URL', '')
    if not webhook_url:
        return jsonify({'error': 'TELEGRAM_WEBHOOK_URL not set'}), 400
    
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook'
    data = {'url': webhook_url}
    response = requests.post(url, json=data)
    return jsonify(response.json())

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
