import asyncio
import html
import json
import re
import sqlite3
import datetime
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional, Set, Dict
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
    AUTO_CLEANUP_ENABLED,  # 新增
    CLEANUP_DAYS,          # 新增
    CLEANUP_HOUR           # 新增
)

# 调试日志函数
def debug_log(message: str):
    if DEBUG_MODE:
        print(f"[DEBUG] {message}")

# 数据库清理功能

async def cleanup_old_messages(days_to_keep: int = 7) -> str:
    """
    清理指定天数前的已撤回消息
    
    Args:
        days_to_keep: 保留多少天的数据，默认7天
    
    Returns:
        清理结果字符串
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # 计算清理的时间点
        cutoff_date = datetime.datetime.now() - datetime.timedelta(days=days_to_keep)
        cutoff_str = cutoff_date.isoformat()
        
        # 查询要删除的消息数量
        cursor.execute("""
            SELECT COUNT(*) FROM messages 
            WHERE recalled = 1 AND created_at < ?
        """, (cutoff_str,))
        count_to_delete = cursor.fetchone()[0]
        
        if count_to_delete == 0:
            return f"没有需要清理的已撤回消息（{days_to_keep}天前）"
        
        # 删除旧的已撤回消息
        cursor.execute("""
            DELETE FROM messages 
            WHERE recalled = 1 AND created_at < ?
        """, (cutoff_str,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        
        # 优化数据库
        cursor.execute("VACUUM")
        
        debug_log(f"数据库清理完成：删除了 {deleted_count} 条已撤回消息")
        return f"数据库清理完成：删除了 {deleted_count} 条已撤回消息（{days_to_keep}天前）"
        
    except Exception as e:
        conn.rollback()
        debug_log(f"数据库清理失败: {str(e)}")
        return f"数据库清理失败: {str(e)}"
    finally:
        conn.close()

async def cleanup_all_recalled_messages() -> str:
    """
    清理所有已撤回的消息
    
    Returns:
        清理结果字符串
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # 查询要删除的消息数量
        cursor.execute("SELECT COUNT(*) FROM messages WHERE recalled = 1")
        count_to_delete = cursor.fetchone()[0]
        
        if count_to_delete == 0:
            return "没有需要清理的已撤回消息"
        
        # 删除所有已撤回消息
        cursor.execute("DELETE FROM messages WHERE recalled = 1")
        deleted_count = cursor.rowcount
        conn.commit()
        
        # 优化数据库
        cursor.execute("VACUUM")
        
        debug_log(f"数据库清理完成：删除了 {deleted_count} 条已撤回消息")
        return f"数据库清理完成：删除了 {deleted_count} 条已撤回消息"
        
    except Exception as e:
        conn.rollback()
        debug_log(f"数据库清理失败: {str(e)}")
        return f"数据库清理失败: {str(e)}"
    finally:
        conn.close()

async def get_database_stats() -> str:
    """
    获取数据库统计信息
    
    Returns:
        数据库统计信息字符串
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # 总消息数
        cursor.execute("SELECT COUNT(*) FROM messages")
        total_messages = cursor.fetchone()[0]
        
        # 已撤回消息数
        cursor.execute("SELECT COUNT(*) FROM messages WHERE recalled = 1")
        recalled_messages = cursor.fetchone()[0]
        
        # 未撤回消息数
        cursor.execute("SELECT COUNT(*) FROM messages WHERE recalled = 0")
        active_messages = cursor.fetchone()[0]
        
        # 最老的消息时间
        cursor.execute("SELECT MIN(created_at) FROM messages")
        oldest_message = cursor.fetchone()[0]
        
        # 数据库文件大小（需要os模块）
        import os
        db_size = os.path.getsize(DB_FILE) / (1024 * 1024)  # MB
        
        return (
            f"数据库统计信息:\n"
            f"总消息数: {total_messages}\n"
            f"已撤回消息: {recalled_messages}\n"
            f"未撤回消息: {active_messages}\n"
            f"最早消息时间: {oldest_message or '无'}\n"
            f"数据库大小: {db_size:.2f} MB"
        )
        
    except Exception as e:
        debug_log(f"获取数据库统计失败: {str(e)}")
        return f"获取数据库统计失败: {str(e)}"
    finally:
        conn.close()

# 定时清理任务
async def scheduled_cleanup_task():
    """
    定时清理任务，每天指定小时清理指定天数前的已撤回消息
    """
    while True:
        now = datetime.datetime.now()
        # 计算到下一个CLEANUP_HOUR点的时间
        next_run = now.replace(hour=CLEANUP_HOUR, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)
        # 等待到指定时间
        wait_seconds = (next_run - now).total_seconds()
        debug_log(f"定时清理任务将在 {next_run} 执行")
        await asyncio.sleep(wait_seconds)
        # 执行清理
        result = await cleanup_old_messages(days_to_keep=CLEANUP_DAYS)
        debug_log(f"定时清理结果: {result}")

# 在process_message函数中添加清理相关指令
async def add_cleanup_commands_to_process_message(msg: str):
    """
    处理各种清理数据库的指令
    """
    # 清理指令
    if msg == "清理数据库" or (msg.startswith("@") and "清理数据库" in msg):
        return await cleanup_old_messages(days_to_keep=7)
    
    if msg == "清理全部已撤回" or (msg.startswith("@") and "清理全部已撤回" in msg):
        return await cleanup_all_recalled_messages()
    
    if msg == "数据库统计" or (msg.startswith("@") and "数据库统计" in msg):
        return await get_database_stats()
    
    if msg.startswith("清理") and "天" in msg:
        try:
            import re
            match = re.search(r'清理(\d+)天', msg)
            if match:
                days = int(match.group(1))
                return await cleanup_old_messages(days_to_keep=days)
        except:
            return "清理指令格式错误，正确格式：清理3天"


# 在应用启动时添加定时任务
async def start_cleanup_scheduler():
    """
    启动定时清理任务（根据AUTO_CLEANUP_ENABLED）
    """
    if AUTO_CLEANUP_ENABLED:
        asyncio.create_task(scheduled_cleanup_task())
        debug_log("定时清理任务已启动（已启用自动清理）")
    else:
        debug_log("未启用自动清理，定时清理任务未启动")

    
# SQLite数据库初始化
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
            raw_message TEXT,
            recalled BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute("PRAGMA table_info(messages)")
    columns = {col[1] for col in cursor.fetchall()}
    
    if 'recalled' not in columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN recalled BOOLEAN DEFAULT 0")
        debug_log("已添加 recalled 列到 messages 表")
    
    if 'created_at' not in columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        debug_log("已添加 created_at 列到 messages 表")
    
    conn.commit()
    conn.close()

init_db()

# 正则表达式
TAOBAO_REGEX = re.compile(
    r'(https?://[^\s<>]*(?:taobao\.|tb\.)[^\s<>]+)|'
    r'(?:￥|\$)([0-9A-Za-z()]*[A-Za-z][0-9A-Za-z()]{10})(?:￥|\$)?(?![0-9A-Za-z])|'
    r'([0-9]\$[0-9a-zA-Z]+\$:// [A-Z0-9]+)|'
    r'tk=([0-9A-Za-z]{11,12})|'
    r'\(([0-9A-Za-z]{11})\)|'
    r'₤([0-9A-Za-z]{13})₤|'
    r'[0-9]{2}₤([0-9A-Za-z]{11})£'
)

JD_REGEX = re.compile(
    r'https?:\/\/[^\s<>]*(3\.cn|jd\.|jingxi)[^\s<>]+|[^一-龥0-9a-zA-Z=;&?-_.<>:\'",{}][0-9a-zA-Z()]{16}[^一-龥0-9a-zA-Z=;&?-_.<>:\'",{}\s]'
)

# 全局变量
group_timers: Dict[int, Dict] = {}  # {group_id: {'enabled': bool, 'interval': int, 'task': asyncio.Task}}
pending_recall_messages: Set[int] = set()  # 待重试撤回的消息
pending_requests: Dict[str, int] = {}  # {echo: message_id} 用于匹配响应
has_printed_watched_groups = False

def remove_cq_codes(text: str) -> str:
    return re.sub(r'\[CQ:.*?\]', '', text)

def extract_from_cq_json(text: str) -> str:
    pattern = re.compile(r'\[CQ:json,data=(\{.*?\})\]')
    matches = pattern.findall(text)
    extracted = ""
    for m in matches:
        m_unescaped = html.unescape(m)
        debug_log(f"提取的CQ:json数据: {m_unescaped}")
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
                    debug_log(f"内部CQ:json数据解析错误: {str(e)}")
            else:
                news = data_obj.get("meta", {}).get("news", {})
                jump_url = news.get("jumpUrl", "")
                if jump_url:
                    extracted += " " + jump_url
        except Exception as e:
            debug_log(f"CQ:json解析错误: {str(e)}")
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
                        debug_log(f"跳过重复的淘宝标题: {title}")
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
                            debug_log(f"跳过重复的淘宝标题（错误情况）: {title}")
                            return None
                        processed_titles.add(title)
                        return f"TB错误: {json.dumps(content, ensure_ascii=False)}"
                    elif isinstance(content, dict):
                        title = content.get('tao_title', content.get('title', '未知'))
                        if title in processed_titles:
                            debug_log(f"跳过重复的淘宝标题（错误情况）: {title}")
                            return None
                        processed_titles.add(title)
                        return f"TB错误: {json.dumps(content, ensure_ascii=False)}"
                    else:
                        return f"TB错误: {content}"
    except Exception as e:
        debug_log(f"TB错误: {str(e)}")
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
                        debug_log(f"跳过重复的京东标题: {jianjie}")
                        return None
                    processed_titles.add(jianjie)
                    pict_url = content.get('pict_url', content.get('pic_url', ''))
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
        debug_log(f"JD错误: {str(e)}")
        return "JD请求失败: 请求异常"

async def recall_messages(ws: WebSocket, group_id: Optional[int], count: int) -> str:
    if group_id is None:
        debug_log("私聊不支持撤回")
        return "私聊不支持撤回功能"
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    query = "SELECT message_id, user_id, raw_message, created_at FROM messages WHERE group_id = ? AND recalled = 0 ORDER BY id DESC LIMIT ?"
    cursor.execute(query, (group_id, count))
    messages_to_recall = cursor.fetchall()

    if not messages_to_recall:
        debug_log(f"群 {group_id} 中没有未撤回的消息")
        conn.close()
        return "群内没有未撤回的消息"

    results = []
    for message_id, user_id, raw_message, created_at in reversed(messages_to_recall):
        if not isinstance(message_id, int):
            debug_log(f"无效的消息ID {message_id} (用户 {user_id})，跳过撤回")
            results.append(f"消息ID {message_id} 无效，跳过")
            continue
        message_time = datetime.datetime.fromisoformat(created_at) if created_at else datetime.datetime.now()
        if (datetime.datetime.now() - message_time).total_seconds() > 120:
            debug_log(f"消息 {message_id} 已过期，无法撤回")
            cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,))
            conn.commit()
            results.append(f"消息ID {message_id} 已过期，标记为已撤回")
            continue
        pending_echo = f"recall_{message_id}"
        payload = {
            "action": "delete_msg",
            "params": {"message_id": message_id},
            "echo": pending_echo
        }
        pending_requests[pending_echo] = message_id
        debug_log(f"发送撤回命令 (群 {group_id}, 用户 {user_id}, 消息 {raw_message}): {json.dumps(payload)}")
        try:
            if ws.client_state != WebSocketState.CONNECTED:
                debug_log("WebSocket已断开，无法发送撤回命令")
                results.append("WebSocket连接断开，无法撤回消息")
                conn.close()
                return "\n".join(results) if results else "WebSocket连接断开，无法撤回消息"
            await ws.send_text(json.dumps(payload))
            timeout = 10
            start_time = asyncio.get_event_loop().time()
            while True:
                if asyncio.get_event_loop().time() - start_time > timeout:
                    debug_log(f"撤回消息 {message_id} 超时未收到响应")
                    pending_recall_messages.add(message_id)
                    results.append(f"消息ID {message_id} 撤回失败: 响应超时")
                    break
                response_data = await ws.receive_text()
                response = json.loads(response_data)
                if response.get("echo") == pending_echo:
                    if response.get("status") == "ok":
                        cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,))
                        conn.commit()
                        if message_id in pending_recall_messages:
                            pending_recall_messages.remove(message_id)
                        debug_log(f"成功撤回并标记消息 {message_id} 在数据库中")
                        results.append(f"消息ID {message_id} 撤回成功")
                    else:
                        debug_log(f"撤回消息 {message_id} 失败: {response.get('message', '未知错误')}，完整响应: {json.dumps(response)}")
                        pending_recall_messages.add(message_id)
                        results.append(f"消息ID {message_id} 撤回失败: {response.get('message', '未知错误')}")
                    del pending_requests[pending_echo]
                    break
        except Exception as e:
            debug_log(f"撤回消息 {message_id} (用户 {user_id}) 错误: {str(e)}")
            pending_recall_messages.add(message_id)
            results.append(f"消息ID {message_id} 撤回错误: {str(e)}")
        await asyncio.sleep(2)
    conn.close()
    return "\n".join(results) if results else "撤回完成"

async def recall_group_messages(ws: WebSocket, group_id: int) -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    query = "SELECT message_id, user_id, raw_message, created_at FROM messages WHERE group_id = ? AND recalled = 0 ORDER BY id DESC"
    cursor.execute(query, (group_id,))
    messages_to_recall = cursor.fetchall()

    if not messages_to_recall:
        debug_log(f"群 {group_id} 中没有未撤回的消息")
        conn.close()
        return "群内没有未撤回的消息"

    results = []
    for message_id, user_id, raw_message, created_at in reversed(messages_to_recall):
        if not isinstance(message_id, int):
            debug_log(f"无效的消息ID {message_id} (用户 {user_id})，跳过撤回")
            results.append(f"消息ID {message_id} 无效，跳过")
            continue
        message_time = datetime.datetime.fromisoformat(created_at) if created_at else datetime.datetime.now()
        if (datetime.datetime.now() - message_time).total_seconds() > 120:
            debug_log(f"消息 {message_id} 已过期，无法撤回")
            cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,))
            conn.commit()
            results.append(f"消息ID {message_id} 已过期，标记为已撤回")
            continue
        pending_echo = f"recall_group_{group_id}_{message_id}"
        payload = {
            "action": "delete_msg",
            "params": {"message_id": message_id},
            "echo": pending_echo
        }
        pending_requests[pending_echo] = message_id
        debug_log(f"发送撤回命令 (群 {group_id}, 用户 {user_id}, 消息 {raw_message}): {json.dumps(payload)}")
        try:
            if ws.client_state != WebSocketState.CONNECTED:
                debug_log("WebSocket已断开，无法发送撤回命令")
                results.append("WebSocket连接断开，无法撤回消息")
                conn.close()
                return "\n".join(results) if results else "WebSocket连接断开，无法撤回消息"
            await ws.send_text(json.dumps(payload))
            timeout = 10
            start_time = asyncio.get_event_loop().time()
            while True:
                if asyncio.get_event_loop().time() - start_time > timeout:
                    debug_log(f"撤回消息 {message_id} 超时未收到响应")
                    pending_recall_messages.add(message_id)
                    results.append(f"消息ID {message_id} 撤回失败: 响应超时")
                    break
                response_data = await ws.receive_text()
                response = json.loads(response_data)
                if response.get("echo") == pending_echo:
                    if response.get("status") == "ok":
                        cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,))
                        conn.commit()
                        if message_id in pending_recall_messages:
                            pending_recall_messages.remove(message_id)
                        debug_log(f"成功撤回并标记消息 {message_id} 在数据库中")
                        results.append(f"消息ID {message_id} 撤回成功")
                    else:
                        debug_log(f"撤回消息 {message_id} 失败: {response.get('message', '未知错误')}，完整响应: {json.dumps(response)}")
                        pending_recall_messages.add(message_id)
                        results.append(f"消息ID {message_id} 撤回失败: {response.get('message', '未知错误')}")
                    del pending_requests[pending_echo]
                    break
        except Exception as e:
            debug_log(f"撤回消息 {message_id} (用户 {user_id}) 错误: {str(e)}")
            pending_recall_messages.add(message_id)
            results.append(f"消息ID {message_id} 撤回错误: {str(e)}")
        await asyncio.sleep(2)
    conn.close()
    return "\n".join(results) if results else "撤回完成"

async def retry_failed_recalls(ws: WebSocket):
    while True:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT message_id, group_id, user_id, raw_message, created_at FROM messages WHERE message_id IN ({}) AND recalled = 0".format(
            ','.join('?' for _ in pending_recall_messages)), list(pending_recall_messages))
        failed_messages = cursor.fetchall()
        conn.close()

        for message_id, group_id, user_id, raw_message, created_at in failed_messages:
            message_time = datetime.datetime.fromisoformat(created_at) if created_at else datetime.datetime.now()
            if (datetime.datetime.now() - message_time).total_seconds() > 120:
                debug_log(f"消息 {message_id} 已过期，无法重试撤回")
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,))
                conn.commit()
                conn.close()
                if message_id in pending_recall_messages:
                    pending_recall_messages.remove(message_id)
                continue
            pending_echo = f"retry_recall_{message_id}"
            payload = {
                "action": "delete_msg",
                "params": {"message_id": message_id},
                "echo": pending_echo
            }
            pending_requests[pending_echo] = message_id
            debug_log(f"重试撤回消息 {message_id} (群 {group_id}, 用户 {user_id}): {json.dumps(payload)}")
            try:
                if ws.client_state != WebSocketState.CONNECTED:
                    debug_log("WebSocket已断开，无法重试撤回")
                    continue
                await ws.send_text(json.dumps(payload))
                timeout = 10
                start_time = asyncio.get_event_loop().time()
                while True:
                    if asyncio.get_event_loop().time() - start_time > timeout:
                        debug_log(f"重试撤回消息 {message_id} 超时未收到响应")
                        break
                    response_data = await ws.receive_text()
                    response = json.loads(response_data)
                    if response.get("echo") == pending_echo:
                        if response.get("status") == "ok":
                            conn = sqlite3.connect(DB_FILE)
                            cursor = conn.cursor()
                            cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,))
                            conn.commit()
                            conn.close()
                            if message_id in pending_recall_messages:
                                pending_recall_messages.remove(message_id)
                            debug_log(f"重试成功撤回并标记消息 {message_id}")
                        else:
                            debug_log(f"重试失败，消息 {message_id}: {response.get('message', '未知错误')}，完整响应: {json.dumps(response)}")
                        del pending_requests[pending_echo]
                        break
            except Exception as e:
                debug_log(f"重试消息 {message_id} 错误: {str(e)}")
        await asyncio.sleep(60)

async def timer_recall_group(ws: WebSocket, group_id: int):
    while group_timers.get(group_id, {}).get('enabled', False):
        debug_log(f"群 {group_id} 定时任务执行：每 {group_timers[group_id]['interval']} 分钟撤回消息")
        result = await recall_group_messages(ws, group_id)
        if result:
            debug_log(f"定时撤回结果: {result}")
        await asyncio.sleep(group_timers[group_id]['interval'] * 60)

async def query_database() -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        cursor.execute("PRAGMA table_info(messages)")
        columns = {col[1] for col in cursor.fetchall()}
        
        select_columns = ["group_id", "user_id", "message_id", "raw_message"]
        if "recalled" in columns:
            select_columns.append("recalled")
        if "created_at" in columns:
            select_columns.append("created_at")
        
        query = f"SELECT {', '.join(select_columns)} FROM messages ORDER BY id DESC"
        cursor.execute(query)
        messages = cursor.fetchall()
        
        if not messages:
            debug_log("数据库中无消息记录")
            return "数据库中没有消息记录"

        total_count = len(messages)
        result = [f"数据库消息总数: {total_count}"]
        
        for i, message in enumerate(messages, 1):
            group_id = message[0]
            user_id = message[1]
            message_id = message[2]
            raw_message = message[3]
            recalled = message[4] if "recalled" in columns else 0
            created_at = message[5] if "created_at" in columns else "未知"
            
            group_str = f"群号: {group_id}" if group_id else "私聊"
            status = "已撤回" if recalled else "未撤回"
            result.append(
                f"消息 {i}:\n"
                f"{group_str}\n"
                f"用户ID: {user_id}\n"
                f"消息ID: {message_id}\n"
                f"状态: {status}\n"
                f"创建时间: {created_at}\n"
                f"内容: {raw_message[:50]}{'...' if len(raw_message) > 50 else ''}"
            )
        
        return "\n\n".join(result)
    
    except Exception as e:
        debug_log(f"查询数据库错误: {str(e)}")
        return f"查询数据库失败: {str(e)}"
    
    finally:
        conn.close()

async def query_commands() -> str:
    if not COMMANDS:
        debug_log("config.py中未定义指令")
        return "暂无定义的指令"

    result = ["支持的指令："]
    for cmd, desc in COMMANDS.items():
        result.append(f"{cmd}: {desc}")
    return "\n".join(result)

async def auto_recall_message(ws: WebSocket, message_id: int, delay: int = 30):
    global pending_recall_messages
    pending_recall_messages.add(message_id)
    debug_log(f"消息 {message_id} 已添加到待自动撤回集合（延迟 {delay}秒）")
    await asyncio.sleep(delay)
    if ws.client_state == WebSocketState.CONNECTED:
        pending_echo = f"auto_recall_{message_id}"
        payload = {
            "action": "delete_msg",
            "params": {"message_id": message_id},
            "echo": pending_echo
        }
        pending_requests[pending_echo] = message_id
        try:
            await ws.send_text(json.dumps(payload))
            timeout = 10
            start_time = asyncio.get_event_loop().time()
            while True:
                if asyncio.get_event_loop().time() - start_time > timeout:
                    debug_log(f"自动撤回消息 {message_id} 超时未收到响应")
                    break
                response_data = await ws.receive_text()
                response = json.loads(response_data)
                if response.get("echo") == pending_echo:
                    if response.get("status") == "ok":
                        conn = sqlite3.connect(DB_FILE)
                        cursor = conn.cursor()
                        cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,))
                        affected_rows = cursor.rowcount
                        if affected_rows == 0:
                            debug_log(f"消息 {message_id} 未在数据库中找到，插入已撤回记录")
                            cursor.execute(
                                "INSERT INTO messages (message_id, raw_message, recalled) VALUES (?, ?, ?)",
                                (message_id, "[自动撤回]", 1)
                            )
                        conn.commit()
                        conn.close()
                        if message_id in pending_recall_messages:
                            pending_recall_messages.remove(message_id)
                        debug_log(f"自动撤回并标记消息 {message_id} 在数据库中")
                    else:
                        debug_log(f"自动撤回消息 {message_id} 失败: {response.get('message', '未知错误')}，完整响应: {json.dumps(response)}")
                        pending_recall_messages.add(message_id)
                    del pending_requests[pending_echo]
                    break
        except Exception as e:
            debug_log(f"自动撤回消息 {message_id} 错误: {str(e)}")
            pending_recall_messages.add(message_id)
    else:
        debug_log(f"WebSocket已断开，无法自动撤回消息 {message_id}")
        pending_recall_messages.add(message_id)

async def process_message(message: str, group_id: Optional[int], user_id: int, message_id: Optional[int], ws: WebSocket, self_id: int) -> str:
    global group_timers

    msg = message.strip()
    if user_id == self_id and (
        msg in (
            "定时指令格式错误，正确格式：定时 5 或 定时关",
            "定时撤回功能已关闭",
            "定时撤回功能已是关闭状态",
            "定时间隔必须大于 0",
            "数据库中没有消息可撤回",
            "数据库中没有消息记录",
            "私聊不支持撤回功能",
            "群内没有未撤回的消息",
            "WebSocket连接断开，无法撤回消息"
        ) or msg.startswith(("定时撤回功能已开启", "查询数据库失败", "撤回完成", "消息ID"))
    ):
        debug_log(f"跳过机器人自身消息: {msg}")
        return ""

    is_command = (
        msg == "查数据库" or 
        msg.startswith("撤回") or 
        msg.startswith("定时") or 
        msg == "指令" or 
        msg == "撤回全部" or 
        (msg.startswith("@") and ("撤回" in msg or "查数据库" in msg or "指令" in msg))
    )

    if group_id is None:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (group_id, user_id, message_id, raw_message) VALUES (?, ?, ?, ?)",
            (group_id, user_id, message_id, message)
        )
        conn.commit()
        conn.close()
        debug_log(f"【私聊】保存消息到数据库: user_id={user_id}, message_id={message_id}, raw_message={message}")
    elif group_id in WATCHED_GROUP_IDS:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (group_id, user_id, message_id, raw_message) VALUES (?, ?, ?, ?)",
            (group_id, user_id, message_id, message)
        )
        conn.commit()
        conn.close()
        debug_log(f"【群聊】保存消息到数据库: group_id={group_id}, user_id={user_id}, message_id={message_id}, raw_message={message}")
        if is_command and message_id is not None:
            asyncio.create_task(auto_recall_message(ws, message_id, delay=10))
            debug_log(f"指令消息 {message_id} 已安排10秒后自动撤回")
    else:
        debug_log(f"群 {group_id} 不在 WATCHED_GROUP_IDS 中，跳过处理")
        return ""

    # 先处理清理相关指令
    cleanup_result = await add_cleanup_commands_to_process_message(msg)
    if cleanup_result:
        return cleanup_result

    if is_command and user_id != self_id:
        if msg == "查数据库" or (msg.startswith("@") and "查数据库" in msg):
            debug_log(f"处理查数据库指令: group_id={group_id}, user_id={user_id}, message_id={message_id}")
            return await query_database()

        if msg.startswith("定时"):
            if group_id is None:
                return "私聊不支持定时撤回功能"
            if msg.endswith("关"):
                if group_id in group_timers and group_timers[group_id]['enabled']:
                    group_timers[group_id]['enabled'] = False
                    if group_timers[group_id]['task']:
                        group_timers[group_id]['task'].cancel()
                        group_timers[group_id]['task'] = None
                    debug_log(f"群 {group_id} 定时撤回已禁用")
                    return f"群 {group_id} 定时撤回功能已关闭"
                else:
                    debug_log(f"群 {group_id} 定时撤回已是关闭状态")
                    return f"群 {group_id} 定时撤回功能已是关闭状态"
            else:
                parts = msg.split()
                if len(parts) != 2:
                    debug_log("定时命令格式错误，正确格式：定时 5 或 定时关")
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
                    debug_log(f"群 {group_id} 定时撤回已启用，每 {interval} 分钟撤回")
                    return f"群 {group_id} 定时撤回功能已开启，每 {interval} 分钟撤回群内消息"
                except ValueError:
                    debug_log("定时命令格式错误，正确格式：定时 5 或 定时关")
                    return "定时指令格式错误，正确格式：定时 5 或 定时关"

        if msg == "指令" or (msg.startswith("@") and "指令" in msg):
            debug_log(f"处理指令查询: group_id={group_id}, user_id={user_id}, message_id={message_id}")
            return await query_commands()

        if msg == "撤回全部" or (msg.startswith("@") and "撤回全部" in msg):
            debug_log(f"处理撤回全部指令: group_id={group_id}, user_id={user_id}, message_id={message_id}")
            return await recall_group_messages(ws, group_id)

        if msg.startswith("撤回") or (msg.startswith("@") and "撤回" in msg and "撤回全部" not in msg):
            debug_log(f"处理撤回指令: group_id={group_id}, user_id={user_id}, message_id={message_id}, raw_message={msg}")
            parts = msg.split()
            count = 0
            if msg.startswith("@"):
                try:
                    count = int(parts[2])
                except (IndexError, ValueError):
                    debug_log("撤回命令格式错误，正确格式：撤回 5 或 @anyID 撤回 5")
                    return "撤回指令格式错误，正确格式：撤回 5 或 @某ID 撤回 5"
            else:
                try:
                    count = int(parts[1])
                except (IndexError, ValueError):
                    debug_log("撤回命令格式错误，正确格式：撤回 5 或 @anyID 撤回 5")
                    return "撤回指令格式错误，正确格式：撤回 5 或 @某ID 撤回 5"
            return await recall_messages(ws, group_id, count)
    
    elif user_id != self_id:
        text = remove_cq_codes(message)
        cq_extracted = extract_from_cq_json(message)
        combined_text = text + " " + cq_extracted
        results = []
        processed_titles = set()
        for match in TAOBAO_REGEX.finditer(combined_text):
            token = match.group(0)
            debug_log(f"检测到淘宝链接/口令 (完整匹配): {token}")
            if match.group(1):
                debug_log(f"匹配到淘宝URL: {match.group(1)}")
                token = match.group(1)
            elif match.group(2):
                debug_log(f"匹配到11位淘宝口令: {match.group(2)}")
                token = match.group(2)
            elif match.group(3):
                debug_log(f"匹配到混淆淘宝口令: {match.group(3)}")
                token = match.group(3)
            elif match.group(4):
                debug_log(f"匹配到tk=淘宝口令: {match.group(4)}")
                token = match.group(4)
            elif match.group(5):
                debug_log(f"匹配到括号淘宝口令: {match.group(5)}")
                token = match.group(5)
            elif match.group(6):
                debug_log(f"匹配到₤13位淘宝口令: {match.group(6)}")
                token = match.group(6)
            elif match.group(7):
                debug_log(f"匹配到48₤...£淘宝口令: {match.group(7)}")
                token = match.group(7)
            result = await convert_tkl(token, processed_titles)
            if result:
                results.append(result)
        for match in JD_REGEX.finditer(combined_text):
            token = match.group(0)
            debug_log(f"检测到京东链接: {token}")
            result = await convert_jd_link(token, processed_titles)
            if result:
                results.append(result)
        return "\n".join(results) if results else ""
    return ""

async def custom_ws_adapter(websocket: WebSocket):
    global has_printed_watched_groups
    await websocket.accept()
    debug_log("WebSocket连接已创建")
    
    if not has_printed_watched_groups:
        print(f"当前监控群聊: {', '.join(map(str, WATCHED_GROUP_IDS))}")
        has_printed_watched_groups = True

    retry_task = asyncio.create_task(retry_failed_recalls(websocket))

    try:
        while True:
            data = await websocket.receive_text()
            debug_log(f"收到消息: {data}")
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                debug_log("消息解析失败: 非JSON格式")
                continue

            if event.get("post_type") == "notice":
                notice_type = event.get("notice_type")
                if notice_type in ["group_recall", "friend_recall"]:
                    message_id_to_recall = event.get("message_id")
                    group_id = event.get("group_id")
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id_to_recall,))
                    affected_rows = cursor.rowcount
                    if affected_rows > 0:
                        debug_log(f"处理撤回通知: 标记消息 {message_id_to_recall} 为已撤回 " + (f"来自群 {group_id}" if group_id else "【私聊】"))
                        if message_id_to_recall in pending_recall_messages:
                            pending_recall_messages.remove(message_id_to_recall)
                    else:
                        debug_log(f"撤回通知: 消息 {message_id_to_recall} 未在数据库中找到，插入已撤回记录")
                        cursor.execute(
                            "INSERT OR IGNORE INTO messages (group_id, user_id, message_id, raw_message, recalled) VALUES (?, ?, ?, ?, ?)",
                            (group_id, event.get("user_id", 0), message_id_to_recall, "[已撤回]", 1)
                        )
                        conn.commit()
                    conn.close()
                continue

            if event.get("post_type") == "meta_event":
                continue

            if event.get("post_type") == "message" and event.get("message_type") in ["private", "group"]:
                self_id = event.get("self_id")
                user_id = event.get("user_id")
                group_id = event.get("group_id")
                raw_message = event.get("raw_message", "")
                message_id = event.get("message_id")
                is_command = (
                    raw_message == "查数据库" or
                    raw_message.startswith("撤回") or 
                    raw_message.startswith("定时") or 
                    raw_message in ("指令", "撤回全部") or 
                    (raw_message.startswith("@") and ("撤回" in raw_message or "查数据库" in raw_message or "指令" in raw_message))
                )
                try:
                    reply_content = await process_message(raw_message, group_id, user_id, message_id, websocket, self_id)
                except Exception as e:
                    debug_log(f"处理消息 {message_id} 错误: {str(e)}")
                    reply_content = f"处理消息失败: {str(e)}"
                
                if user_id == self_id and not is_command:
                    debug_log("机器人自身的非命令消息，不发送回复")
                else:
                    if reply_content:
                        reply_action = {
                            "action": "send_private_msg" if event.get("message_type") == "private" else "send_group_msg",
                            "params": {"user_id": user_id, "message": reply_content} if event.get("message_type") == "private" else {"group_id": group_id, "message": reply_content},
                            "echo": f"reply_{message_id}"
                        }
                        reply_json = json.dumps(reply_action)
                        if websocket.client_state == WebSocketState.CONNECTED:
                            await websocket.send_text(reply_json)
                            debug_log(f"发送回复: {reply_json}")
                            try:
                                timeout = 10
                                start_time = asyncio.get_event_loop().time()
                                while True:
                                    if asyncio.get_event_loop().time() - start_time > timeout:
                                        debug_log(f"回复消息 {message_id} 超时未收到响应")
                                        break
                                    response_data = await websocket.receive_text()
                                    response = json.loads(response_data)
                                    if response.get("echo") == f"reply_{message_id}":
                                        if response.get("status") == "ok" and response.get("data"):
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
                                                debug_log(f"保存机器人回复到数据库: message_id={sent_message_id}, raw_message={reply_content}")
                                                if is_command:
                                                    asyncio.create_task(auto_recall_message(websocket, sent_message_id, delay=10))
                                                    debug_log(f"指令回复消息 {sent_message_id} 已安排10秒后自动撤回")
                                                else:
                                                    asyncio.create_task(auto_recall_message(websocket, sent_message_id, delay=30))
                                                    debug_log(f"非指令回复消息 {sent_message_id} 已安排30秒后自动撤回")
                                        break
                            except Exception as e:
                                debug_log(f"处理回复错误: {str(e)}")
                        else:
                            debug_log("WebSocket已断开，无法发送回复")
            else:
                if "echo" in event and event["echo"] in pending_requests:
                    message_id = pending_requests[event["echo"]]
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    if event.get("status") == "ok":
                        cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,))
                        affected_rows = cursor.rowcount
                        if affected_rows == 0:
                            debug_log(f"消息 {message_id} 未在数据库中找到，插入已撤回记录")
                            cursor.execute(
                                "INSERT INTO messages (message_id, raw_message, recalled) VALUES (?, ?, ?)",
                                (message_id, "[已撤回]", 1)
                            )
                        conn.commit()
                        if message_id in pending_recall_messages:
                            pending_recall_messages.remove(message_id)
                        debug_log(f"处理延迟响应: 成功撤回并标记消息 {message_id}")
                    else:
                        debug_log(f"处理延迟响应: 撤回消息 {message_id} 失败: {event.get('message', '未知错误')}，完整响应: {json.dumps(event)}")
                        pending_recall_messages.add(message_id)
                    conn.close()
                    del pending_requests[event["echo"]]
                else:
                    debug_log("收到非消息类型事件")

    except Exception as e:
        debug_log(f"WebSocket错误: {str(e)}")
    finally:
        retry_task.cancel()
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close()
            debug_log("WebSocket连接已关闭")

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    async def run_websocket():
        while True:
            try:
                app.add_api_websocket_route("/onebot/v11/ws", custom_ws_adapter)
                debug_log("WebSocket路由已添加")
                break
            except Exception as e:
                debug_log(f"WebSocket初始化失败: {str(e)}，2秒后重试")
                await asyncio.sleep(2)

    await asyncio.gather(
        run_websocket(),
        *(
            init() if asyncio.iscoroutinefunction(init) else asyncio.to_thread(init)
            for plugin in PluginManager.plugins.values()
            if (init := getattr(plugin, "init", None))
        )
    )
    debug_log("应用生命周期开始")
    yield
    debug_log("应用生命周期结束")

def debug_log_full_api(base_url: str, params: dict):
    full_url = f"{base_url}?{'&'.join(f'{k}={quote(str(v))}' for k, v in params.items())}"
    debug_log(f"[API MATCHED] 完整API URL: {full_url}")

if __name__ == "__main__":
    debug_log("启动 FastBot 应用")
    # 启动自动清理任务
    import asyncio
    asyncio.get_event_loop().run_until_complete(start_cleanup_scheduler())
    FastBot.build(plugins=["./plugins"], lifespan=lifespan).run(host="0.0.0.0", port=5670)
