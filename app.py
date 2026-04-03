import os
import logging
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    ButtonsTemplate, PostbackTemplateAction, PostbackAction, MessageAction,
    TextSendMessage, TemplateSendMessage, PostbackEvent, MessageEvent
)
from linebot.exceptions import InvalidSignatureError
import requests
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_ACCESS_TOKEN', '')
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

scheduler = BackgroundScheduler()
scheduler.start()

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
            response = requests.patch(url, headers=headers, data=json.dumps(data))
        
        if response.status_code in [200, 201, 206]:
            return response.json()
        else:
            logger.error(f"Supabase error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Supabase request error: {e}")
        return None

def get_or_create_user(line_id: str):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    
    result = supabase_request('users', filters={'line_id': f'eq.{line_id}'})
    
    if result and len(result) > 0:
        return result[0]
    
    user_data = {
        'line_id': line_id,
        'work_hours': 8.5,
        'remind_enabled': True,
        'remind_minutes': 10
    }
    
    new_user = supabase_request('users', method='POST', data=user_data)
    return new_user[0] if new_user else None

def update_user_settings(line_id: str, **kwargs):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    
    for key, value in kwargs.items():
        supabase_request('users', method='PATCH', 
                       data={key: value},
                       filters={'line_id': f'eq.{line_id}'})
    return True

def record_check_in(line_id: str):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    
    user = get_or_create_user(line_id)
    if not user:
        return None
    
    check_in = datetime.utcnow() + timedelta(hours=8)
    work_hours = user.get('work_hours', 8.5)
    scheduled_check_out = check_in + timedelta(hours=work_hours)
    
    record_data = {
        'user_id': user['id'],
        'check_in': check_in.isoformat(),
        'scheduled_check_out': scheduled_check_out.isoformat()
    }
    
    record = supabase_request('work_records', method='POST', data=record_data)
    
    schedule_reminders(user, check_in, scheduled_check_out)
    
    return {
        'record': record[0] if record else None,
        'scheduled_check_out': scheduled_check_out,
        'work_hours': work_hours
    }

def schedule_reminders(user, check_in, scheduled_check_out):
    from apscheduler.triggers.date import DateTrigger
    
    line_id = user['line_id']
    work_hours = user.get('work_hours', 8.5)
    remind_enabled = user.get('remind_enabled', True)
    remind_minutes = user.get('remind_minutes', 10)
    
    job_id_prefix = f"{line_id}_{check_in.strftime('%Y%m%d%H%M%S')}"
    
    if remind_enabled:
        early_remind_time = scheduled_check_out - timedelta(minutes=remind_minutes)
        if early_remind_time > get_taiwan_time():
            scheduler.add_job(
                send_reminder,
                trigger=DateTrigger(run_date=early_remind_time),
                args=[line_id, early_remind_time, "提前提醒", remind_minutes],
                id=f"{job_id_prefix}_early",
                replace_existing=True
            )
    
    scheduler.add_job(
        send_reminder,
        trigger=DateTrigger(run_date=scheduled_check_out),
        args=[line_id, scheduled_check_out, "下班時間", 0],
        id=f"{job_id_prefix}_main",
        replace_existing=True
    )

def send_reminder(line_id, check_out_time, reminder_type, minutes):
    try:
        if minutes > 0:
            message = f"提前 {minutes} 分鐘提醒：\n您的下班時間快到了！\n預定下班時間：{check_out_time.strftime('%H:%M')}"
        else:
            message = f"下班時間到了！\n辛苦您了，可以下班了！\n下班時間：{check_out_time.strftime('%H:%M')}"
        
        line_bot_api.push_message(line_id, TextSendMessage(text=message))
        logger.info(f"已發送提醒給 {line_id}: {reminder_type}")
    except Exception as e:
        logger.error(f"發送提醒失敗: {e}")

def schedule_line_test_reminder(line_id, delay_seconds):
    import threading
    def send_later():
        import time
        time.sleep(delay_seconds)
        test_time = get_taiwan_time()
        message = f"測試提醒！\n\n時間：{test_time.strftime('%H:%M:%S')}"
        line_bot_api.push_message(line_id, TextSendMessage(text=message))
        logger.info(f"測試提醒已發送給 {line_id}")
    
    timer = threading.Timer(delay_seconds, send_later)
    timer.start()
    logger.info(f"測試提醒已排程，{delay_seconds}秒後發送")

def get_taiwan_time():
    return datetime.utcnow() + timedelta(hours=8)

def get_user_history(line_id: str, limit: int = 10):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    
    user = get_or_create_user(line_id)
    if not user:
        return []
    
    records = supabase_request('work_records', 
                             filters={'user_id': f"eq.{user['id']}", 'select': '*'})
    if records:
        records = sorted(records, key=lambda x: x.get('check_in', ''), reverse=True)[:limit]
    return records or []

def format_history_message(records):
    if not records:
        return "尚無打卡記錄"
    
    message = "最近打卡記錄：\n\n"
    
    for i, record in enumerate(records, 1):
        check_in = record.get('check_in', '')
        scheduled = record.get('scheduled_check_out')
        actual = record.get('actual_check_out')
        
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
        
        if isinstance(actual, str):
            try:
                actual = datetime.fromisoformat(actual.replace('Z', '+00:00'))
            except:
                pass
        
        date_str = check_in.strftime('%Y/%m/%d') if isinstance(check_in, datetime) else 'N/A'
        time_str = check_in.strftime('%H:%M') if isinstance(check_in, datetime) else 'N/A'
        
        if scheduled:
            scheduled_str = scheduled.strftime('%H:%M') if isinstance(scheduled, datetime) else 'N/A'
            message += f"{i}. {date_str} 上班 {time_str} -> 預定下班 {scheduled_str}"
            if actual:
                actual_str = actual.strftime('%H:%M') if isinstance(actual, datetime) else 'N/A'
                message += f" -> 實際下班 {actual_str}"
        else:
            message += f"{i}. {date_str} 上班 {time_str}"
        
        message += "\n"
    
    return message

@app.route("/health")
def health():
    return jsonify({'status': 'ok', 'time': get_taiwan_time().isoformat()})

@app.route("/")
def index():
    return "LINE Bot 運作中"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return jsonify({'status': 'error', 'message': 'Invalid signature'}), 400
    
    return jsonify({'status': 'ok'})

@handler.add(PostbackEvent)
def handle_postback(event):
    line_id = event.source.user_id
    data = event.postback.data
    
    if 'action=check_in' in data:
        result = record_check_in(line_id)
        if result:
            user = get_or_create_user(line_id)
            work_hours = result['work_hours']
            scheduled = result['scheduled_check_out']
            
            message = f"上班打卡成功！\n\n上班時間：{get_taiwan_time().strftime('%H:%M')}\n預定下班時間：{scheduled.strftime('%H:%M')}\n工作時長：{work_hours} 小時\n\n系統會在下班時間提醒您！"
            
            if user and user.get('remind_enabled', True):
                remind_min = user.get('remind_minutes', 10)
                early_time = scheduled - timedelta(minutes=remind_min)
                message += f"\n提前 {remind_min} 分鐘（{early_time.strftime('%H:%M')}）也會提醒您"
        else:
            message = "打卡失敗，請稍後再試"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
    elif 'action=test_check_in' in data:
        test_check_in = get_taiwan_time()
        test_check_out = test_check_in + timedelta(seconds=10)
        
        message = f"測試打卡成功！\n\n上班時間：{test_check_in.strftime('%H:%M:%S')}\n預定下班時間：{test_check_out.strftime('%H:%M:%S')}\n（10秒後收到提醒）"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
        
        schedule_line_test_reminder(line_id, 10)
    
    elif 'action=history' in data:
        records = get_user_history(line_id, 10)
        message = format_history_message(records)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
    elif 'action=settings' in data:
        user = get_or_create_user(line_id)
        if user:
            work_hours = user.get('work_hours', 8.5)
            remind_enabled = user.get('remind_enabled', True)
            remind_minutes = user.get('remind_minutes', 10)
            
            message = f"設定選項：\n\n目前設定：\n• 工作時長：{work_hours} 小時\n• 提前提醒：{'開啟' if remind_enabled else '關閉'}"
            
            if remind_enabled:
                message += f"\n• 提前分鐘：{remind_minutes} 分鐘"
            
            message += "\n\n請選擇要修改的項目："
            
            buttons = ButtonsTemplate(
                actions=[
                    PostbackTemplateAction(
                        label=f"設定時長 (目前 {work_hours}h)",
                        data="action=set_hours"
                    ),
                    PostbackTemplateAction(
                        label=f"提前提醒 (目前 {'開' if remind_enabled else '關'})",
                        data="action=toggle_remind"
                    ),
                    PostbackTemplateAction(
                        label=f"提前分鐘 (目前 {remind_minutes}分)",
                        data="action=set_remind_min"
                    )
                ]
            )
            
            line_bot_api.reply_message(event.reply_token, [TextSendMessage(text=message), TemplateSendMessage(alt_text="設定選項", template=buttons)])
    
    elif 'action=set_hours' in data:
        buttons = ButtonsTemplate(
            actions=[
                PostbackTemplateAction(label="8 小時", data="hours=8"),
                PostbackTemplateAction(label="8.5 小時", data="hours=8.5"),
                PostbackTemplateAction(label="9 小時", data="hours=9"),
                PostbackTemplateAction(label="自訂", data="hours=custom")
            ]
        )
        line_bot_api.reply_message(event.reply_token, TemplateSendMessage(alt_text="選擇時長", template=buttons))
    
    elif 'action=toggle_remind' in data:
        user = get_or_create_user(line_id)
        if user:
            current = user.get('remind_enabled', True)
            update_user_settings(line_id, remind_enabled=not current)
            status = "開啟" if not current else "關閉"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"提前提醒已{status}"))
    
    elif 'action=set_remind_min' in data:
        buttons = ButtonsTemplate(
            actions=[
                PostbackTemplateAction(label="5 分鐘", data="remind_min=5"),
                PostbackTemplateAction(label="10 分鐘", data="remind_min=10"),
                PostbackTemplateAction(label="15 分鐘", data="remind_min=15"),
                PostbackTemplateAction(label="20 分鐘", data="remind_min=20")
            ]
        )
        line_bot_api.reply_message(event.reply_token, TemplateSendMessage(alt_text="選擇分鐘", template=buttons))
    
    elif data.startswith('hours='):
        hours_str = data.split('=')[1]
        if hours_str == 'custom':
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入您的工作時長（例如：8.5）"))
        else:
            hours = float(hours_str)
            update_user_settings(line_id, work_hours=hours)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"工作時長已設定為 {hours} 小時"))
    
    elif data.startswith('remind_min='):
        minutes = int(data.split('=')[1])
        update_user_settings(line_id, remind_minutes=minutes)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"提前提醒分鐘已設定為 {minutes} 分鐘"))

@handler.add(MessageEvent)
def handle_message(event):
    line_id = event.source.user_id
    text = event.message.text.strip()
    
    try:
        hours = float(text)
        if 1 <= hours <= 24:
            update_user_settings(line_id, work_hours=hours)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"工作時長已設定為 {hours} 小時"))
            return
    except ValueError:
        pass
    
    if text in ['上班', '打卡', '開始上班']:
        result = record_check_in(line_id)
        if result:
            user = get_or_create_user(line_id)
            work_hours = result['work_hours']
            scheduled = result['scheduled_check_out']
            
            message = f"上班打卡成功！\n\n上班時間：{get_taiwan_time().strftime('%H:%M')}\n預定下班時間：{scheduled.strftime('%H:%M')}\n工作時長：{work_hours} 小時\n\n系統會在下班時間提醒您！"
            
            if user and user.get('remind_enabled', True):
                remind_min = user.get('remind_minutes', 10)
                early_time = scheduled - timedelta(minutes=remind_min)
                message += f"\n提前 {remind_min} 分鐘（{early_time.strftime('%H:%M')}）也會提醒您"
        else:
            message = "打卡失敗，請稍後再試"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
    elif text in ['歷史', '記錄', '查詢']:
        records = get_user_history(line_id, 10)
        message = format_history_message(records)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
    elif text in ['設定', '設定功能']:
        user = get_or_create_user(line_id)
        if user:
            work_hours = user.get('work_hours', 8.5)
            remind_enabled = user.get('remind_enabled', True)
            remind_minutes = user.get('remind_minutes', 10)
            
            message = f"設定選項：\n\n目前設定：\n• 工作時長：{work_hours} 小時\n• 提前提醒：{'開啟' if remind_enabled else '關閉'}"
            
            if remind_enabled:
                message += f"\n• 提前分鐘：{remind_minutes} 分鐘"
            
            message += "\n\n請選擇：\n1. 輸入數字設定工作時長\n2. 輸入「提醒開」或「提醒關」切換提醒\n3. 輸入「5分」「10分」設定提前分鐘"
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
    elif text in ['提醒開', '開提醒', '開啟提醒']:
        update_user_settings(line_id, remind_enabled=True)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="提前提醒已開啟"))
    
    elif text in ['提醒關', '關提醒', '關閉提醒']:
        update_user_settings(line_id, remind_enabled=False)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="提前提醒已關閉"))
    
    elif text.endswith('分') and text[:-1].isdigit():
        minutes = int(text[:-1])
        if 1 <= minutes <= 60:
            update_user_settings(line_id, remind_minutes=minutes)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"提前提醒分鐘已設定為 {minutes} 分鐘"))
    
    elif text in ['功能', '幫助', 'help', '選單', 'menu']:
        message = "使用說明：\n\n• 輸入「上班」或點擊上班按鈕打卡\n• 輸入「歷史」查看打卡記錄\n• 輸入「設定」調整選項\n• 點擊下方選單快速操作"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
    else:
        message = "您好！請使用以下指令：\n\n• 「上班」- 打卡\n• 「歷史」- 查看記錄\n• 「設定」- 調整選項\n• 「幫助」- 查看說明\n\n或點擊下方選單按鈕"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
