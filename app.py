import os
import logging
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

def get_taiwan_time():
    return datetime.utcnow() + timedelta(hours=8)

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
            logger.error(f"Supabase error: {response.status_code}")
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
    
    check_in = get_taiwan_time()
    work_hours = user.get('work_hours', 8.5)
    scheduled_check_out = check_in + timedelta(hours=work_hours)
    remind_enabled = user.get('remind_enabled', True)
    remind_minutes = user.get('remind_minutes', 10)
    
    if remind_enabled:
        early_remind_time = scheduled_check_out - timedelta(minutes=remind_minutes)
    else:
        early_remind_time = None
    
    record_data = {
        'user_id': user['id'],
        'line_id': line_id,
        'check_in': check_in.isoformat(),
        'scheduled_check_out': scheduled_check_out.isoformat(),
        'early_remind_time': early_remind_time.isoformat() if early_remind_time else None,
        'early_remind_sent': False,
        'main_remind_sent': False
    }
    
    record = supabase_request('work_records', method='POST', data=record_data)
    
    return {
        'record': record[0] if record else None,
        'scheduled_check_out': scheduled_check_out,
        'work_hours': work_hours,
        'remind_enabled': remind_enabled,
        'remind_minutes': remind_minutes
    }

def check_and_send_reminders():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    
    now = get_taiwan_time()
    today = now.strftime('%Y-%m-%d')
    
    records = supabase_request('work_records', 
                              filters={
                                  'early_remind_sent': 'eq.false',
                                  'check_in': f'gte.{today}T00:00:00',
                                  'order': 'created_at.desc',
                                  'limit': 1
                              })
    
    if not records:
        return
    
    for record in records:
        line_id = record.get('line_id')
        early_remind_time_str = record.get('early_remind_time')
        scheduled_check_out_str = record.get('scheduled_check_out')
        early_remind_sent = record.get('early_remind_sent', False)
        main_remind_sent = record.get('main_remind_sent', False)
        is_test = record.get('is_test', False)
        
        if not line_id:
            continue
        
        if early_remind_time_str and not early_remind_sent:
            try:
                early_remind_time = datetime.fromisoformat(early_remind_time_str.replace('Z', '+00:00').replace('+00:00', ''))
            except:
                early_remind_time = None
            
            if early_remind_time and now >= early_remind_time:
                user = get_or_create_user(line_id)
                remind_min = 10
                if user:
                    remind_min = user.get('remind_minutes', 10)
                
                if is_test:
                    message = f"🧪 測試提醒\n\n這是測試訊息，您的下班時間快到了！"
                else:
                    message = f"提前 {remind_min} 分鐘提醒：\n您的下班時間快到了！"
                try:
                    line_bot_api.push_message(line_id, TextSendMessage(text=message))
                    
                    supabase_request('work_records', method='PATCH',
                                  data={'early_remind_sent': True},
                                  filters={'id': f"eq.{record.get('id')}"})
                    
                    logger.info(f"已發送提前提醒給 {line_id}")
                except Exception as e:
                    logger.error(f"發送提前提醒失敗: {e}")
        
        if scheduled_check_out_str and not main_remind_sent:
            try:
                scheduled_check_out = datetime.fromisoformat(scheduled_check_out_str.replace('Z', '+00:00').replace('+00:00', ''))
            except:
                scheduled_check_out = None
            
            if scheduled_check_out and now >= scheduled_check_out:
                if is_test:
                    message = f"🧪 測試提醒\n\n這是測試訊息，下班時間到了！"
                else:
                    message = f"下班時間到了！\n辛苦您了，可以下班了！"
                try:
                    line_bot_api.push_message(line_id, TextSendMessage(text=message))
                    
                    supabase_request('work_records', method='PATCH',
                                  data={'main_remind_sent': True},
                                  filters={'id': f"eq.{record.get('id')}"})
                    
                    logger.info(f"已發送下班提醒給 {line_id}")
                except Exception as e:
                    logger.error(f"發送下班提醒失敗: {e}")

def start_scheduler():
    if not scheduler.running:
        scheduler.start()
    
    scheduler.add_job(
        check_and_send_reminders,
        'interval',
        minutes=1,
        id='check_reminders',
        replace_existing=True
    )
    logger.info("LINE Bot 排程檢查提醒已啟動")

def get_user_history(line_id: str, limit: int = 10):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    
    user = get_or_create_user(line_id)
    if not user:
        return []
    
    records = supabase_request('work_records', 
                             filters={'user_id': f"eq.{user['id']}"})
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
            message += f"{i}. {date_str} 上班 {time_str} -> 下班 {scheduled_str}\n"
        else:
            message += f"{i}. {date_str} 上班 {time_str}\n"
    
    return message

@app.route("/health")
def health():
    check_and_send_reminders()
    return jsonify({'status': 'ok', 'time': get_taiwan_time().isoformat()})

@app.route("/check")
def check_reminders():
    check_and_send_reminders()
    return jsonify({'status': 'ok', 'checked': True})

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
            work_hours = result['work_hours']
            scheduled = result['scheduled_check_out']
            
            message = f"上班打卡成功！\n\n上班時間：{get_taiwan_time().strftime('%H:%M')}\n預定下班時間：{scheduled.strftime('%H:%M')}\n工作時長：{work_hours} 小時\n\n系統會在下班時間提醒您！"
            
            if result.get('remind_enabled'):
                remind_min = result.get('remind_minutes', 10)
                early_time = scheduled - timedelta(minutes=remind_min)
                message += f"\n提前 {remind_min} 分鐘也會提醒您"
        else:
            message = "打卡失敗，請稍後再試"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
    elif 'action=test_check_in' in data:
        result = record_check_in(line_id)
        if result:
            message = f"測試打卡成功！\n\n上班時間：{get_taiwan_time().strftime('%H:%M:%S')}\n預定下班時間：{result['scheduled_check_out'].strftime('%H:%M:%S')}\n（10秒後會收到提醒！）"
        else:
            message = "測試打卡失敗"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
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
                PostbackTemplateAction(label="7 小時", data="hours=7"),
                PostbackTemplateAction(label="8 小時", data="hours=8"),
                PostbackTemplateAction(label="8.5 小時", data="hours=8.5"),
                PostbackTemplateAction(label="9 小時", data="hours=9")
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
        hours = float(data.split('=')[1])
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
            work_hours = result['work_hours']
            scheduled = result['scheduled_check_out']
            
            message = f"上班打卡成功！\n\n上班時間：{get_taiwan_time().strftime('%H:%M')}\n預定下班時間：{scheduled.strftime('%H:%M')}\n工作時長：{work_hours} 小時\n\n系統會在下班時間提醒您！"
            
            if result.get('remind_enabled'):
                remind_min = result.get('remind_minutes', 10)
                early_time = scheduled - timedelta(minutes=remind_min)
                message += f"\n提前 {remind_min} 分鐘也會提醒您"
        else:
            message = "打卡失敗，請稍後再試"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
    elif text == '測試':
        user = get_or_create_user(line_id)
        if user:
            check_in = get_taiwan_time()
            scheduled_check_out = check_in + timedelta(seconds=10)
            
            record_data = {
                'user_id': user['id'],
                'line_id': line_id,
                'check_in': check_in.isoformat(),
                'scheduled_check_out': scheduled_check_out.isoformat(),
                'early_remind_time': None,
                'early_remind_sent': True,
                'main_remind_sent': False,
                'is_test': True
            }
            
            supabase_request('work_records', method='POST', data=record_data)
            
            message = f"🧪 測試打卡成功！\n\n測試時間：{check_in.strftime('%H:%M:%S')}\n⏰ 10秒後會收到下班提醒！"
        else:
            message = "❌ 測試打卡失敗"
        
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
        message = "使用說明：\n\n• 輸入「上班」打卡\n• 輸入「歷史」查看記錄\n• 輸入「設定」調整選項"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
    else:
        message = "您好！請使用以下指令：\n\n• 「上班」- 打卡\n• 「歷史」- 查看記錄\n• 「設定」- 調整選項"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))

start_scheduler()

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
