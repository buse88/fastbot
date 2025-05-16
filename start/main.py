import asyncio
import html
import json
import re
import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional, Set
from urllib.parse import quote

import aiohttp
from fastapi import FastAPI, WebSocket
from fastbot.bot import FastBot
from fastbot.plugin import PluginManager
from starlette.websockets import WebSocketState

from config import (
    DEBUG_MODE,
    WATCHED_GROUP_IDS,
    APP_KEY,
    SID,
    PID,
    RELATION_ID,
    JD_APPKEY,
    JD_UNION_ID,
    JD_POSITION_ID,
    COMMANDS,
)

# SQLite database initialization
DB_FILE = "messages.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            user_id INTEGER,
            message_id INTEGER,
            raw_message TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Updated Taobao regex
TAOBAO_REGEX = re.compile(
    r'(https?://[^\s<>]*(?:taobao\.|tb\.)[^\s<>]+)|'  # Taobao URL
    r'(?:￥|\$)([0-9A-Za-z()]*[A-Za-z][0-9A-Za-z()]{10})(?:￥|\$)?(?![0-9A-Za-z])|'  # 11-digit Taobao code
    r'([0-9]\$[0-9a-zA-Z]+\$:// [A-Z0-9]+)|'  # Obfuscated Taobao code
    r'tk=([0-9A-Za-z]{11,12})|'  # tk= followed by code
    r'\(([0-9A-Za-z]{11})\)|'  # Parenthesized 11-digit code
    r'₤([0-9A-Za-z]{13})₤|'  # 13-digit code enclosed in ₤
    r'[0-9]{2}₤([0-9A-Za-z]{11})£'  # 48₤...£ format
)

JD_REGEX = re.compile(
    r'https?:\/\/[^\s<>]*(3\.cn|jd\.|jingxi)[^\s<>]+|[^一-龥0-9a-zA-Z=;&?-_.<>:\'",{}][0-9a-zA-Z()]{16}[^一-龥0-9a-zA-Z=;&?-_.<>:\'",{}\s]'
)

# Dictionary to manage timers per group: {group_id: {'enabled': bool, 'interval': int, 'task': asyncio.Task}}
group_timers = {}
pending_recall_messages = set()  # Track messages pending auto-recall
has_printed_watched_groups = False  # Flag to print watched groups only once

def debug_log(message: str):
    if DEBUG_MODE:
        print(f"[DEBUG] {message}")

def remove_cq_codes(text: str) -> str:
    return re.sub(r'\[CQ:.*?\]', '', text)

def extract_from_cq_json(text: str) -> str:
    pattern = re.compile(r'\[CQ:json,data=(\{.*?\})\]')
    matches = pattern.findall(text)
    extracted = ""
    for m in matches:
        m_unescaped = html.unescape(m)
        debug_log("Extracted CQ:json data: " + m_unescaped)
        try:
            data_obj = json.loads(m_unescaped)
            if "data" in data_obj and isinstance(data_obj["data"], str):
                try:
                    inner = json.loads(data_obj["data"])
                    news = inner.get("meta", {}).get("news", {})
                    jump_url = news.get("jumpUrl", "")
                    if jump_url:
                        extracted += " " + jump_url
                except Exception as e:
                    debug_log("Inner CQ:json data parsing error: " + str(e))
            else:
                news = data_obj.get("meta", {}).get("news", {})
                jump_url = news.get("jumpUrl", "")
                if jump_url:
                    extracted += " " + jump_url
        except Exception as e:
            debug_log(f"CQ:json parsing error: {str(e)}")
    return extracted.strip()

async def convert_tkl(tkl: str, processed_titles: Set[str]) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            base_url = "https://api.zhetaoke.com:10001/api/open_gaoyongzhuanlian_tkl.ashx"
            params = {
                "appkey": APP_KEY,
                "sid": SID,
                "pid": PID,
                "relation_id": RELATION_ID,
                "tkl": quote(tkl),
                "signurl": 5
            }
            debug_log_full_api(base_url, params)
            async with session.get(base_url, params=params, timeout=10) as response:
                response.raise_for_status()
                result = await response.json(content_type=None)
                if result.get("status") == 200 and "content" in result:
                    content = result["content"][0]
                    title = content.get('tao_title', content.get('title', '未知'))
                    if title in processed_titles:
                        debug_log(f"Skipping duplicate Taobao title: {title}")
                        return None
                    processed_titles.add(title)
                    pict_url = content.get('pict_url', content.get('pic_url', ''))
                    image_cq = f"[CQ:image,file={pict_url}]" if pict_url else ""
                    return (
                        f"商品：{title}\n\n"
                        f"券后: {content.get('quanhou_jiage', '未知')}\n"
                        f"佣金: {content.get('tkfee3', '未知')}\n"
                        f"链接: {content.get('shorturl2', '未知')}\n"
                        f"淘口令: {content.get('tkl', '未知')}\n"
                        f"{image_cq}"
                    )
                else:
                    content = result.get("content", "未知错误")
                    if isinstance(content, list) and content:
                        content = content[0]
                        title = content.get('tao_title', content.get('title', '未知'))
                        if title in processed_titles:
                            debug_log(f"Skipping duplicate Taobao title (error case): {title}")
                            return None
                        processed_titles.add(title)
                        return f"TB错误: {json.dumps(content, ensure_ascii=False)}"
                    elif isinstance(content, dict):
                        title = content.get('tao_title', content.get('title', '未知'))
                        if title in processed_titles:
                            debug_log(f"Skipping duplicate Taobao title (error case): {title}")
                            return None
                        processed_titles.add(title)
                        return f"TB错误: {json.dumps(content, ensure_ascii=False)}"
                    else:
                        return f"TB错误: {content}"
    except Exception as e:
        debug_log(f"TB error: {str(e)}")
        return "淘宝转链失败: 请求异常"

async def convert_jd_link(material_url: str, processed_titles: Set[str]) -> Optional[str]:
    base_url = "http://api.zhetaoke.com:20000/api/open_jing_union_open_promotion_byunionid_get.ashx"
    params = {
        "appkey": JD_APPKEY,
        "materialId": material_url,
        "unionId": JD_UNION_ID,
        "positionId": JD_POSITION_ID,
        "chainType": 3,
        "signurl": 5
    }
    debug_log_full_api(base_url, params)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(base_url, params=params, timeout=10) as response:
                response.raise_for_status()
                result = await response.json(content_type=None)
                if result.get("status") == 200 and "content" in result:
                    content = result["content"][0]
                    jianjie = content.get('jianjie', '未知')
                    if jianjie in processed_titles:
                        debug_log(f"Skipping duplicate JD title: {jianjie}")
                        return None
                    processed_titles.add(jianjie)
                    pict_url = content.get('pict_url', content.get('picUrl', ''))
                    image_cq = f"[CQ:image,file={pict_url}]" if pict_url else ""
                    return (
                        f"商品: {jianjie}\n\n"
                        f"券后: {content.get('quanhou_jiage', '未知')}\n"
                        f"佣金: {content.get('tkfee3', '未知')}\n"
                        f"购买链接: {content.get('shorturl', '未知')}\n"
                        f"{image_cq}"
                    )
                if "jd_union_open_promotion_byunionid_get_response" in result:
                    jd_response = result["jd_union_open_promotion_byunionid_get_response"]
                    if "result" in jd_response:
                        try:
                            jd_result = json.loads(jd_response["result"])
                            error_message = jd_result.get("message", "未知错误")
                            if jd_result.get("data") and jd_result["data"].get("shortURL"):
                                short_url = jd_result["data"]["shortURL"]
                                return f"优惠: {short_url}"
                            return f"JD转换失败: {error_message}"
                        except json.JSONDecodeError:
                            return "JD转换失败: 返回数据解析错误"
                return "JD转换失败: 未知错误"
    except Exception as e:
        debug_log(f"JD error: {str(e)}")
        return "JD请求失败: 请求异常"

async def recall_messages(ws: WebSocket, group_id: Optional[int], count: int) -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if group_id is None:
        debug_log("Private messages do not support recall")
        conn.close()
        return ""
    query = "SELECT message_id, user_id, raw_message FROM messages WHERE group_id = ? ORDER BY id DESC LIMIT ?"
    cursor.execute(query, (group_id, count))
    messages_to_recall = cursor.fetchall()

    if not messages_to_recall:
        debug_log(f"No messages found in group {group_id} to recall")
        conn.close()
        return ""

    for message_id, user_id, raw_message in reversed(messages_to_recall):
        if not isinstance(message_id, int):
            debug_log(f"Invalid message ID {message_id} (user {user_id}), skipping recall")
            continue
        payload = {
            "action": "delete_msg",
            "params": {"message_id": message_id},
            "echo": f"recall_{message_id}"
        }
        debug_log(f"Sending recall command (group {group_id}, user {user_id}, message {raw_message}): {json.dumps(payload)}")
        try:
            if ws.client_state != WebSocketState.CONNECTED:
                debug_log("WebSocket disconnected, cannot send recall command")
                conn.close()
                return ""
            await ws.send_text(json.dumps(payload))
            cursor.execute("DELETE FROM messages WHERE message_id = ?", (message_id,))
            conn.commit()
            if message_id in pending_recall_messages:
                pending_recall_messages.remove(message_id)
            debug_log(f"Deleted recalled message {message_id} from database")
            await asyncio.sleep(2)
        except Exception as e:
            debug_log(f"Error recalling message {message_id} (user {user_id}): {str(e)}")
    conn.close()
    return ""

async def recall_group_messages(ws: WebSocket, group_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    query = "SELECT message_id, user_id, raw_message FROM messages WHERE group_id = ? ORDER BY id DESC"
    cursor.execute(query, (group_id,))
    messages_to_recall = cursor.fetchall()

    if not messages_to_recall:
        debug_log(f"No messages found in group {group_id} to recall")
        conn.close()
        return

    for message_id, user_id, raw_message in reversed(messages_to_recall):
        if not isinstance(message_id, int):
            debug_log(f"Invalid message ID {message_id} (user {user_id}), skipping recall")
            continue
        payload = {
            "action": "delete_msg",
            "params": {"message_id": message_id},
            "echo": f"recall_group_{group_id}_{message_id}"
        }
        debug_log(f"Sending recall command (group {group_id}, user {user_id}, message {raw_message}): {json.dumps(payload)}")
        try:
            if ws.client_state != WebSocketState.CONNECTED:
                debug_log("WebSocket disconnected, cannot send recall command")
                conn.close()
                return
            await ws.send_text(json.dumps(payload))
            cursor.execute("DELETE FROM messages WHERE message_id = ?", (message_id,))
            conn.commit()
            if message_id in pending_recall_messages:
                pending_recall_messages.remove(message_id)
            debug_log(f"Deleted recalled message {message_id} from database")
            await asyncio.sleep(2)
        except Exception as e:
            debug_log(f"Error recalling message {message_id} (user {user_id}): {str(e)}")
    conn.close()

async def timer_recall_group(ws: WebSocket, group_id: int):
    while group_timers[group_id]['enabled']:
        debug_log(f"Group {group_id} timer task executing: recalling messages every {group_timers[group_id]['interval']} minutes")
        await recall_group_messages(ws, group_id)
        await asyncio.sleep(group_timers[group_id]['interval'] * 60)

async def query_database() -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    query = "SELECT group_id, user_id, message_id, raw_message FROM messages ORDER BY id DESC"
    cursor.execute(query)
    messages = cursor.fetchall()
    conn.close()

    if not messages:
        debug_log("No message records in database")
        return "数据库中没有消息记录"

    total_count = len(messages)
    result = [f"数据库消息总数: {total_count}"]
    for i, (group_id, user_id, message_id, raw_message) in enumerate(messages, 1):
        group_str = f"群号: {group_id}" if group_id else "私聊"
        result.append(
            f"消息 {i}:\n"
            f"{group_str}\n"
            f"用户ID: {user_id}\n"
            f"消息ID: {message_id}\n"
            f"内容: {raw_message[:50]}{'...' if len(raw_message) > 50 else ''}"
        )
    
    return "\n\n".join(result)

async def query_commands() -> str:
    if not COMMANDS:
        debug_log("No commands defined in config.py")
        return "暂无定义的指令"

    result = ["支持的指令："]
    for cmd, desc in COMMANDS.items():
        result.append(f"{cmd}: {desc}")
    return "\n".join(result)

async def auto_recall_message(ws: WebSocket, message_id: int):
    global pending_recall_messages
    pending_recall_messages.add(message_id)
    debug_log(f"Message {message_id} added to pending auto-recall set")
    await asyncio.sleep(30)
    if ws.client_state == WebSocketState.CONNECTED:
        payload = {
            "action": "delete_msg",
            "params": {"message_id": message_id},
            "echo": f"auto_recall_{message_id}"
        }
        try:
            await ws.send_text(json.dumps(payload))
            debug_log(f"Auto-recall command sent for message {message_id}")
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE message_id = ?", (message_id,))
            conn.commit()
            conn.close()
            if message_id in pending_recall_messages:
                pending_recall_messages.remove(message_id)
            debug_log(f"Deleted auto-recalled message {message_id} from database")
        except Exception as e:
            debug_log(f"Error auto-recalling message {message_id}: {str(e)}")
    else:
        debug_log(f"WebSocket disconnected, cannot auto-recall message {message_id}")
    if message_id in pending_recall_messages:
        pending_recall_messages.remove(message_id)

async def process_message(message: str, group_id: Optional[int], user_id: int, message_id: Optional[int], ws: WebSocket, self_id: int) -> str:
    global group_timers

    msg = message.strip()
    # Skip bot's own response messages to avoid reprocessing
    if user_id == self_id and (
        msg in (
            "定时指令格式错误，正确格式：定时 5 或 定时关",
            "定时撤回功能已关闭",
            "定时撤回功能已是关闭状态",
            "定时间隔必须大于 0",
            "数据库中没有消息可撤回"
        ) or msg.startswith("定时撤回功能已开启")
    ):
        return ""

    is_command = (msg.startswith("撤回") or 
                  msg.startswith("定时") or 
                  msg in ("指令", "查数据库", "撤回全部") or 
                  (msg.startswith("@") and ("撤回" in msg or "查询" in msg)))

    # Save message to database (both user and bot messages)
    if group_id is None:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (group_id, user_id, message_id, raw_message) VALUES (?, ?, ?, ?)",
            (group_id, user_id, message_id, message)
        )
        conn.commit()
        conn.close()
        debug_log(f"【Private】Saved message to database: user_id={user_id}, message_id={message_id}, raw_message={message}")
    elif group_id in WATCHED_GROUP_IDS:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (group_id, user_id, message_id, raw_message) VALUES (?, ?, ?, ?)",
            (group_id, user_id, message_id, message)
        )
        conn.commit()
        conn.close()
        debug_log(f"【Group】Saved message to database: group_id={group_id}, user_id={user_id}, message_id={message_id}, raw_message={message}")
    else:
        debug_log(f"Group {group_id} not in WATCHED_GROUP_IDS, skipping processing")
        return ""

    # Process user commands only
    if is_command and user_id != self_id:
        if msg.startswith("定时"):
            if group_id is None:
                return "私聊不支持定时撤回功能"
            if msg.endswith("关"):
                if group_id in group_timers and group_timers[group_id]['enabled']:
                    group_timers[group_id]['enabled'] = False
                    if group_timers[group_id]['task']:
                        group_timers[group_id]['task'].cancel()
                        group_timers[group_id]['task'] = None
                    debug_log(f"Group {group_id} timed recall disabled")
                    return f"群 {group_id} 定时撤回功能已关闭"
                else:
                    debug_log(f"Group {group_id} timed recall already disabled")
                    return f"群 {group_id} 定时撤回功能已是关闭状态"
            else:
                parts = msg.split()
                if len(parts) != 2:
                    debug_log("Timer command format error, correct format: 定时 5 or 定时关")
                    return "定时指令格式错误，正确格式：定时 5 或 定时关"
                try:
                    interval = int(parts[1])
                    if interval <= 0:
                        return "定时间隔必须大于 0"
                    if group_id not in group_timers:
                        group_timers[group_id] = {'enabled': True, 'interval': interval, 'task': None}
                    else:
                        group_timers[group_id]['enabled'] = True
                        group_timers[group_id]['interval'] = interval
                        if group_timers[group_id]['task']:
                            group_timers[group_id]['task'].cancel()
                    group_timers[group_id]['task'] = asyncio.create_task(timer_recall_group(ws, group_id))
                    debug_log(f"Group {group_id} timed recall enabled, recalling every {interval} minutes")
                    return f"群 {group_id} 定时撤回功能已开启，每 {interval} 分钟撤回群内消息"
                except ValueError:
                    debug_log("Timer command format error, correct format: 定时 5 or 定时关")
                    return "定时指令格式错误，正确格式：定时 5 或 定时关"

        if msg == "指令" or (msg.startswith("@") and "指令" in msg):
            return await query_commands()

        if msg == "查数据库" or (msg.startswith("@") and "查数据库" in msg):
            return await query_database()

        if msg == "撤回全部" or (msg.startswith("@") and "撤回全部" in msg):
            await recall_group_messages(ws, group_id)
            # return "撤回全部消息完成"

        if msg.startswith("撤回") or (msg.startswith("@") and "撤回" in msg and "撤回全部" not in msg):
            parts = msg.split()
            count = 0
            if msg.startswith("@"):
                try:
                    count = int(parts[2])
                except (IndexError, ValueError):
                    debug_log("Recall command format error, correct format: 撤回 5 or @anyID 撤回 5")
                    return ""
            else:
                try:
                    count = int(parts[1])
                except (IndexError, ValueError):
                    debug_log("Recall command format error, correct format: 撤回 5 or @anyID 撤回 5")
                    return ""
            await recall_messages(ws, group_id, count)
            return ""
    elif user_id != self_id:  # Non-command user messages
        text = remove_cq_codes(message)
        cq_extracted = extract_from_cq_json(message)
        combined_text = text + " " + cq_extracted
        results = []
        processed_titles = set()
        for match in TAOBAO_REGEX.finditer(combined_text):
            token = match.group(0)
            debug_log(f"Detected Taobao link/code (full match): {token}")
            if match.group(1):
                debug_log(f"Matched Taobao URL: {match.group(1)}")
                token = match.group(1)
            elif match.group(2):
                debug_log(f"Matched 11-digit Taobao code: {match.group(2)}")
                token = match.group(2)
            elif match.group(3):
                debug_log(f"Matched obfuscated Taobao code: {match.group(3)}")
                token = match.group(3)
            elif match.group(4):
                debug_log(f"Matched tk= Taobao code: {match.group(4)}")
                token = match.group(4)
            elif match.group(5):
                debug_log(f"Matched parenthesized Taobao code: {match.group(5)}")
                token = match.group(5)
            elif match.group(6):
                debug_log(f"Matched ₤13-digit Taobao code: {match.group(6)}")
                token = match.group(6)
            elif match.group(7):
                debug_log(f"Matched 48₤...£ Taobao code: {match.group(7)}")
                token = match.group(7)
            result = await convert_tkl(token, processed_titles)
            if result:
                results.append(result)
        for match in JD_REGEX.finditer(combined_text):
            token = match.group(0)
            debug_log(f"Detected JD link: {token}")
            result = await convert_jd_link(token, processed_titles)
            if result:
                results.append(result)
        return "\n".join(results) if results else ""
    return ""  # Bot's non-command messages or irrelevant messages

async def custom_ws_adapter(websocket: WebSocket):
    global has_printed_watched_groups
    await websocket.accept()
    debug_log("WebSocket connection established")
    
    if not has_printed_watched_groups:
        print(f"当前监控群聊: {', '.join(map(str, WATCHED_GROUP_IDS))}")
        has_printed_watched_groups = True

    try:
        while True:
            data = await websocket.receive_text()
            debug_log(f"Received message: {data}")
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                debug_log("Message parsing failed: not JSON format")
                continue

            if event.get("post_type") == "notice":
                notice_type = event.get("notice_type")
                if notice_type in ["group_recall", "friend_recall"]:
                    message_id_to_recall = event.get("message_id")
                    group_id = event.get("group_id")
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM messages WHERE message_id = ?", (message_id_to_recall,))
                    conn.commit()
                    conn.close()
                    if message_id_to_recall in pending_recall_messages:
                        pending_recall_messages.remove(message_id_to_recall)
                    debug_log(f"Recall notice handled: deleted message {message_id_to_recall} " + (f"from group {group_id}" if group_id else "【private】"))
                continue

            if event.get("post_type") == "message" and event.get("message_type") in ["private", "group"]:
                self_id = event.get("self_id")
                user_id = event.get("user_id")
                group_id = event.get("group_id")
                raw_message = event.get("raw_message", "")
                message_id = event.get("message_id")
                try:
                    reply_content = await process_message(raw_message, group_id, user_id, message_id, websocket, self_id)
                except Exception as e:
                    debug_log(f"Error processing message {message_id}: {str(e)}")
                    continue

                is_command = (raw_message.startswith("撤回") or 
                              raw_message.startswith("定时") or 
                              raw_message in ("指令", "查数据库", "撤回全部") or 
                              (raw_message.startswith("@") and ("撤回" in raw_message or "查询" in raw_message)))
                if user_id == self_id and not is_command:
                    debug_log("Bot's own non-command message, no reply sent")
                else:
                    if reply_content:
                        reply_action = {
                            "action": "send_private_msg" if event.get("message_type") == "private" else "send_group_msg",
                            "params": {"user_id": user_id, "message": reply_content} if event.get("message_type") == "private" else {"group_id": group_id, "message": reply_content},
                            "echo": "private_reply" if event.get("message_type") == "private" else "group_reply"
                        }
                        reply_json = json.dumps(reply_action)
                        if websocket.client_state == WebSocketState.CONNECTED:
                            await websocket.send_text(reply_json)
                            debug_log(f"Sent response: {reply_json}")
                            try:
                                async with asyncio.timeout(5):
                                    response_data = await websocket.receive_text()
                                    if response_data is None:
                                        debug_log("No response received, connection may be closed")
                                        continue
                                    response = json.loads(response_data)
                                    if response.get("status") == "ok" and "data" in response:
                                        sent_message_id = response["data"].get("message_id")
                                        if sent_message_id:
                                            conn = sqlite3.connect(DB_FILE)
                                            cursor = conn.cursor()
                                            cursor.execute(
                                                "INSERT INTO messages (group_id, user_id, message_id, raw_message) VALUES (?, ?, ?, ?)",
                                                (group_id, self_id, sent_message_id, reply_content)
                                            )
                                            conn.commit()
                                            conn.close()
                                            debug_log(f"Saved bot response to database: message_id={sent_message_id}, raw_message={reply_content}")
                                            asyncio.create_task(auto_recall_message(websocket, sent_message_id))
                            except asyncio.TimeoutError:
                                debug_log("Response timeout, skipping auto-recall task")
                            except Exception as e:
                                debug_log(f"Error handling response: {str(e)}")
                        else:
                            debug_log("WebSocket disconnected, cannot send response")
            if event.get("post_type") == "meta_event" and event.get("meta_event_type") == "heartbeat":
                debug_log("Heartbeat detected")
    except Exception as e:
        debug_log(f"WebSocket error: {str(e)}")
    finally:
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close()
            debug_log("WebSocket connection closed")

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    async def run_websocket():
        while True:
            try:
                app.add_api_websocket_route("/onebot/v11/ws", custom_ws_adapter)
                debug_log("WebSocket route added")
                break
            except Exception as e:
                debug_log(f"WebSocket initialization failed: {str(e)}, retrying in 2 seconds")
                await asyncio.sleep(2)

    await asyncio.gather(
        run_websocket(),
        *(
            init() if asyncio.iscoroutinefunction(init) else asyncio.to_thread(init)
            for plugin in PluginManager.plugins.values()
            if (init := getattr(plugin, "init", None))
        )
    )
    yield

def debug_log_full_api(base_url: str, params: dict):
    full_url = f"{base_url}?{'&'.join(f'{k}={quote(str(v))}' for k, v in params.items())}"
    debug_log(f"[API MATCHED] Full API URL: {full_url}")

if __name__ == "__main__":
    FastBot.build(plugins=["plugins"], lifespan=lifespan).run(host="0.0.0.0", port=5670)
