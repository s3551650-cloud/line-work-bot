import os
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    RichMenu, RichMenuArea, RichMenuBounds, PostbackAction,
    MessageAction, TemplateMessage, ButtonsTemplate,
    PostbackTemplateAction, TextSendMessage, PostbackEvent, MessageEvent
)
from linebot.exceptions import InvalidSignatureError
from supabase import create_client, Client
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

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

scheduler = BackgroundScheduler()
scheduler.start()

def init_db():
    if not supabase:
        return
    
    supabase.table('users').upsert({
        'line_id': 'temp',
        'work_hours': 8.5,
        'remind_enabled': True,
        'remind_minutes': 10
    }, on_conflict='line_id').execute()

def get_or_create_user(line_id: str):
    if not supabase:
        return None
    
    result = supabase.table('users').select('*').eq('line_id', line_id).execute()
    
    if result.data and len(result.data) > 0:
        return result.data[0]
    
    user_data = {
        'line_id': line_id,
        'work_hours': 8.5,
        'remind_enabled': True,
        'remind_minutes': 10
    }
    result = supabase.table('users').insert(user_data).execute()
    return result.data[0] if result.data else None

def update_user_settings(line_id: str, **kwargs):
    if not supabase:
        return False
    
    supabase.table('users').update(kwargs).eq('line_id', line_id).execute()
    return True

def record_check_in(line_id: str):
    if not supabase:
        return None
    
    user = get_or_create_user(line_id)
    if not user:
        return None
    
    check_in = datetime.now()
    work_hours = user.get('work_hours', 8.5)
    scheduled_check_out = check_in + timedelta(hours=work_hours)
    
    record_data = {
        'user_id': user['id'],
        'check_in': check_in.isoformat(),
        'scheduled_check_out': scheduled_check_out.isoformat()
    }
    
    result = supabase.table('work_records').insert(record_data).execute()
    
    schedule_reminders(user, check_in, scheduled_check_out)
    
    return {
        'record': result.data[0] if result.data else None,
        'scheduled_check_out': scheduled_check_out,
        'work_hours': work_hours
    }

def schedule_reminders(user, check_in, scheduled_check_out):
    from apscheduler.triggers.date import DateTrigger
    
    line_id = user['line_id']
    user_id = user['id']
    work_hours = user.get('work_hours', 8.5)
    remind_enabled = user.get('remind_enabled', True)
    remind_minutes = user.get('remind_minutes', 10)
    
    job_id_prefix = f"{line_id}_{check_in.strftime('%Y%m%d%H%M%S')}"
    
    if remind_enabled:
        early_remind_time = scheduled_check_out - timedelta(minutes=remind_minutes)
        if early_remind_time > datetime.now():
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
            message = f"⏰ 提前 {minutes} 分鐘提醒：\n您的下班時間快到了！\n預定下班時間：{check_out_time.strftime('%H:%M')}"
        else:
            message = f"🎉 下班時間到了！\n辛苦您了，可以下班了！\n下班時間：{check_out_time.strftime('%H:%M')}"
        
        line_bot_api.push_message(line_id, TextSendMessage(text=message))
        logger.info(f"已發送提醒給 {line_id}: {reminder_type}")
    except Exception as e:
        logger.error(f"發送提醒失敗: {e}")

def get_user_history(line_id: str, limit: int = 10):
    if not supabase:
        return []
    
    user = get_or_create_user(line_id)
    if not user:
        return []
    
    result = supabase.table('work_records').select('*').eq('user_id', user['id']).order('check_in', desc=True).limit(limit).execute()
    return result.data if result.data else []

def get_latest_record(line_id: str):
    if not supabase:
        return None
    
    user = get_or_create_user(line_id)
    if not user:
        return None
    
    result = supabase.table('work_records').select('*').eq('user_id', user['id']).order('check_in', desc=True).limit(1).execute()
    return result.data[0] if result.data else None

def format_history_message(records):
    if not records:
        return "尚無打卡記錄"
    
    message = "📊 最近打卡記錄：\n\n"
    
    for i, record in enumerate(records, 1):
        check_in = datetime.fromisoformat(record['check_in'].replace('+00:00', ''))
        scheduled = datetime.fromisoformat(record['scheduled_check_out'].replace('+00:00', '')) if record.get('scheduled_check_out') else None
        actual = datetime.fromisoformat(record['actual_check_out'].replace('+00:00', '')) if record.get('actual_check_out') else None
        
        date_str = check_in.strftime('%Y/%m/%d')
        time_str = check_in.strftime('%H:%M')
        
        if scheduled:
            scheduled_str = scheduled.strftime('%H:%M')
            message += f"{i}. {date_str} 上班 {time_str} → 預定下班 {scheduled_str}"
            if actual:
                actual_str = actual.strftime('%H:%M')
                message += f" → 實際下班 {actual_str}"
        else:
            message += f"{i}. {date_str} 上班 {time_str}"
        
        message += "\n"
    
    return message

def create_rich_menu():
    rich_menu = RichMenu(
        size=RichMenuBounds(width=2500, height=843),
        selected=True,
        name="上班打卡選單",
        chat_bar_text="選單",
        areas=[
            RichMenuArea(
                bounds=RichMenuBounds(x=0, y=0, width=833, height=843),
                action=PostbackAction(
                    label="上班",
                    data="action=check_in",
                    display_text="上班"
                )
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=833, y=0, width=833, height=843),
                action=PostbackAction(
                    label="歷史記錄",
                    data="action=history",
                    display_text="查詢歷史"
                )
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=1666, y=0, width=834, height=843),
                action=PostbackAction(
                    label="設定",
                    data="action=settings",
                    display_text="設定"
                )
            )
        ]
    )
    return rich_menu

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
            
            message = f"✅ 上班打卡成功！\n\n上班時間：{datetime.now().strftime('%H:%M')}\n預定下班時間：{scheduled.strftime('%H:%M')}\n工作時長：{work_hours} 小時\n\n⏰ 系統會在下班時間提醒您！"
            
            if user.get('remind_enabled', True):
                remind_min = user.get('remind_minutes', 10)
                early_time = scheduled - timedelta(minutes=remind_min)
                message += f"\n⚡ 提前 {remind_min} 分鐘（{early_time.strftime('%H:%M')}）也會提醒您"
        else:
            message = "❌ 打卡失敗，請稍後再試"
        
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
            
            message = f"⚙️ 設定選項：\n\n目前設定：\n• 工作時長：{work_hours} 小時\n• 提前提醒：{'開啟' if remind_enabled else '關閉'}"
            
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
            
            line_bot_api.reply_message(event.reply_token, [TextSendMessage(text=message), TemplateMessage(alt_text="設定選項", template=buttons)])
    
    elif 'action=set_hours' in data:
        buttons = ButtonsTemplate(
            actions=[
                PostbackTemplateAction(label="8 小時", data="hours=8"),
                PostbackTemplateAction(label="8.5 小時", data="hours=8.5"),
                PostbackTemplateAction(label="9 小時", data="hours=9"),
                PostbackTemplateAction(label="自訂", data="hours=custom")
            ]
        )
        line_bot_api.reply_message(event.reply_token, TemplateMessage(alt_text="選擇時長", template=buttons))
    
    elif 'action=toggle_remind' in data:
        user = get_or_create_user(line_id)
        if user:
            current = user.get('remind_enabled', True)
            update_user_settings(line_id, remind_enabled=not current)
            status = "開啟" if not current else "關閉"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 提前提醒已{status}"))
    
    elif 'action=set_remind_min' in data:
        buttons = ButtonsTemplate(
            actions=[
                PostbackTemplateAction(label="5 分鐘", data="remind_min=5"),
                PostbackTemplateAction(label="10 分鐘", data="remind_min=10"),
                PostbackTemplateAction(label="15 分鐘", data="remind_min=15"),
                PostbackTemplateAction(label="20 分鐘", data="remind_min=20")
            ]
        )
        line_bot_api.reply_message(event.reply_token, TemplateMessage(alt_text="選擇分鐘", template=buttons))
    
    elif data.startswith('hours='):
        hours_str = data.split('=')[1]
        if hours_str == 'custom':
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入您的工作時長（例如：8.5）"))
        else:
            hours = float(hours_str)
            update_user_settings(line_id, work_hours=hours)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 工作時長已設定為 {hours} 小時"))
    
    elif data.startswith('remind_min='):
        minutes = int(data.split('=')[1])
        update_user_settings(line_id, remind_minutes=minutes)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 提前提醒分鐘已設定為 {minutes} 分鐘"))

@handler.add(MessageEvent)
def handle_message(event):
    line_id = event.source.user_id
    text = event.message.text.strip()
    
    try:
        hours = float(text)
        if 1 <= hours <= 24:
            update_user_settings(line_id, work_hours=hours)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 工作時長已設定為 {hours} 小時"))
            return
    except ValueError:
        pass
    
    if text in ['上班', '打卡', '開始上班']:
        result = record_check_in(line_id)
        if result:
            user = get_or_create_user(line_id)
            work_hours = result['work_hours']
            scheduled = result['scheduled_check_out']
            
            message = f"✅ 上班打卡成功！\n\n上班時間：{datetime.now().strftime('%H:%M')}\n預定下班時間：{scheduled.strftime('%H:%M')}\n工作時長：{work_hours} 小時\n\n⏰ 系統會在下班時間提醒您！"
            
            if user.get('remind_enabled', True):
                remind_min = user.get('remind_minutes', 10)
                early_time = scheduled - timedelta(minutes=remind_min)
                message += f"\n⚡ 提前 {remind_min} 分鐘（{early_time.strftime('%H:%M')}）也會提醒您"
        else:
            message = "❌ 打卡失敗，請稍後再試"
        
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
            
            message = f"⚙️ 設定選項：\n\n目前設定：\n• 工作時長：{work_hours} 小時\n• 提前提醒：{'開啟' if remind_enabled else '關閉'}"
            
            if remind_enabled:
                message += f"\n• 提前分鐘：{remind_minutes} 分鐘"
            
            message += "\n\n請選擇：\n1. 輸入數字設定工作時長\n2. 輸入「提醒開」或「提醒關」切換提醒\n3. 輸入「5分」「10分」設定提前分鐘"
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
    elif text in ['提醒開', '開提醒', '開啟提醒']:
        update_user_settings(line_id, remind_enabled=True)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 提前提醒已開啟"))
    
    elif text in ['提醒關', '關提醒', '關閉提醒']:
        update_user_settings(line_id, remind_enabled=False)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 提前提醒已關閉"))
    
    elif text.endswith('分') and text[:-1].isdigit():
        minutes = int(text[:-1])
        if 1 <= minutes <= 60:
            update_user_settings(line_id, remind_minutes=minutes)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 提前提醒分鐘已設定為 {minutes} 分鐘"))
    
    elif text in ['功能', '幫助', 'help', '選單', 'menu']:
        message = "📱 使用說明：\n\n• 輸入「上班」或點擊上班按鈕打卡\n• 輸入「歷史」查看打卡記錄\n• 輸入「設定」調整選項\n• 點擊下方選單快速操作"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
    
    else:
        message = "您好！請使用以下指令：\n\n• 「上班」- 打卡\n• 「歷史」- 查看記錄\n• 「設定」- 調整選項\n• 「幫助」- 查看說明\n\n或點擊下方選單按鈕"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))

@app.route("/setup-richmenu", methods=['GET'])
def setup_richmenu():
    try:
        rich_menu = create_rich_menu()
        rich_menu_id = line_bot_api.create_rich_menu(rich_menu)
        
        return jsonify({'status': 'ok', 'rich_menu_id': rich_menu_id})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)