import time
from datetime import datetime, timezone, timedelta
from linebot import LineBotApi, WebhookHandler
from linebot.models import TextSendMessage
import json
import os
import firebase_admin
from firebase_admin import db
from openai import OpenAI
import logging

# 配置日志记录
logging.basicConfig(level=logging.INFO)

# 使用环境变量读取凭证
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
token = os.getenv('LINE_BOT_TOKEN')
secret = os.getenv('LINE_BOT_SECRET')
firebase_url = os.getenv('FIREBASE_URL')
asst_id = os.getenv('ASST_ID')

# 初始化 Firebase Admin SDK，使用内建凭证
firebase_admin.initialize_app(options={
    'databaseURL': firebase_url
})

def linebot(request):
    body = request.get_data(as_text=True)
    logging.info(f"Request body: {body}")
    
    try:
        json_data = json.loads(body)
        logging.info(f"JSON data: {json_data}")

        line_bot_api = LineBotApi(token)
        handler = WebhookHandler(secret)
        
        # 检查请求头中是否存在 'X-Line-Signature'
        if 'X-Line-Signature' not in request.headers:
            logging.error("Missing X-Line-Signature header")
            return 'Missing X-Line-Signature header', 400

        signature = request.headers['X-Line-Signature']
        handler.handle(body, signature)

        # 检查事件列表是否存在且非空
        if 'events' not in json_data or not json_data['events']:
            logging.error("No events in request")
            return 'No events in request', 400
        
        event = json_data['events'][0]
        logging.info(f"Event: {event}")

        tk = event['replyToken']
        user_id = event['source']['userId']
        msg_type = event['message']['type']

        user_chat_path = f'it_chat/{user_id}'
        chat_state_path = f'it_state/{user_id}'
        thread_path = f'it_thread/{user_id}'
        thread_ref = db.reference(thread_path)
        threadID = thread_ref.get()
        logging.info(f"Thread ID: {threadID}")

        if msg_type == 'text':
            msg = event['message']['text']
            logging.info(f"Received message: {msg}")

            if threadID is None:
                chat = client.beta.threads.create()
                thread_ref.set(chat.id)
                logging.info(f"Created new thread with ID: {chat.id}")
            else:
                chat = client.beta.threads.retrieve(thread_id=threadID)
                logging.info(f"Retrieved existing thread with ID: {threadID}")

            if msg == '!清空':
                reply_msg = TextSendMessage(text='對話歷史紀錄已經清空！我不會記得我們先前的對話內容。')
                thread_ref.delete()
                db.reference(user_chat_path).delete()
                logging.info("Cleared chat history and thread")
                chat = client.beta.threads.create()
                thread_ref.set(chat.id)
                logging.info(f"Created new thread with ID: {chat.id}")
            elif msg == '!重啟':
                reply_msg = TextSendMessage(text='已為您重啟對話，您可以繼續進行對話。')
                thread_ref.delete()
                chat = client.beta.threads.create()
                thread_ref.set(chat.id)
                logging.info(f"Created new thread with ID: {chat.id}")
            else:
                # 更新firebase中的对话记录
                timestamp = datetime.now(timezone(timedelta(hours=+8))).strftime("%Y-%m-%d %H:%M:%S %a")
                db.reference(user_chat_path).child(timestamp).set({"role": "user", "content": msg})
                logging.info(f"Saved user message to Firebase at {user_chat_path} with timestamp {timestamp}")
                
                thread_message = client.beta.threads.messages.create(thread_id=chat.id, role="user", content=msg)
                run = client.beta.threads.runs.create(thread_id=chat.id, assistant_id=asst_id)

                while run.status != "completed":
                    run = client.beta.threads.runs.retrieve(thread_id=chat.id, run_id=run.id)
                    time.sleep(5)

                message_response = client.beta.threads.messages.list(thread_id=chat.id)
                messages = message_response.data

                latest_message = messages[0]
                ai_msg = latest_message.content[0].text.value
                reply_msg = TextSendMessage(text=ai_msg)
                # 更新firebase中的对话记录
                timestamp = datetime.now(timezone(timedelta(hours=+8))).strftime("%Y-%m-%d %H:%M:%S %a")
                db.reference(user_chat_path).child(timestamp).set({"role": "assistant", "content": ai_msg})
                logging.info(f"Saved assistant message to Firebase at {user_chat_path} with timestamp {timestamp}")

            line_bot_api.reply_message(tk, reply_msg)
            logging.info("Sent reply message to user")

        else:
            reply_msg = TextSendMessage(text='你傳的不是文字訊息唷！我目前只能接受文字訊息。')
            line_bot_api.reply_message(tk, reply_msg)
            logging.info("Received non-text message, sent warning to user")

    except Exception as e:
        detail = str(e)
        logging.error(f"Exception: {detail}")
        timestamp = datetime.now(timezone(timedelta(hours=+8))).strftime("%Y-%m-%d %H:%M:%S %a")
        db.reference(user_chat_path).child(timestamp).set({"role": "error", "content": detail})
        print(detail)
    return 'OK'
