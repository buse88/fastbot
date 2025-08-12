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
    AUTO_CLEANUP_ENABLED,
    CLEANUP_DAYS,
    CLEANUP_HOUR,
    JD_APPID,
    JDJPK_APPKEY
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
        # 计算清理的时间点 (使用UTC时间)
        cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_to_keep)
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
        debug_log(f"准备清理已撤回消息，当前已撤回消息数量: {count_to_delete}")
        
        if count_to_delete == 0:
            return "没有需要清理的已撤回消息"
        
        # 删除所有已撤回消息
        cursor.execute("DELETE FROM messages WHERE recalled = 1")
        deleted_count = cursor.rowcount
        conn.commit()
        
        # 验证删除结果
        cursor.execute("SELECT COUNT(*) FROM messages WHERE recalled = 1")
        remaining_count = cursor.fetchone()[0]
        debug_log(f"删除后剩余已撤回消息数量: {remaining_count}")
        
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
        
        # 转换最早消息时间到本地时区（CST = UTC+8）
        oldest_message_display = "无"
        if oldest_message:
            try:
                # 假设数据库存储的是ISO格式的UTC时间字符串
                dt_utc = datetime.datetime.fromisoformat(oldest_message).replace(tzinfo=datetime.timezone.utc)
                # 定义CST时区（UTC+8）
                cst_tz = datetime.timezone(datetime.timedelta(hours=8))
                oldest_message_cst = dt_utc.astimezone(cst_tz)
                oldest_message_display = oldest_message_cst.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                debug_log(f"获取数据库统计：无法解析最早消息时间字符串: {oldest_message}")
                oldest_message_display = oldest_message # 解析失败时显示原始字符串

        # 数据库文件大小（需要os模块）
        import os
        db_size = os.path.getsize(DB_FILE) / (1024 * 1024)  # MB
        
        return (
            f"数据库统计信息:\n"
            f"总消息数: {total_messages}\n"
            f"已撤回消息: {recalled_messages}\n"
            f"未撤回消息: {active_messages}\n"
            f"最早消息时间: {oldest_message_display}\n"
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
    处理各种清理数据库的指令.
    返回 (reply_content, needs_auto_recall_reply) 或 None.
    """
    # 清理指令
    if msg == "清理数据库" or (msg.startswith("@") and "清理数据库" in msg):
        return (await cleanup_old_messages(days_to_keep=7), True)
    
    if msg == "清理全部已撤回" or (msg.startswith("@") and "清理全部已撤回" in msg):
        return (await cleanup_all_recalled_messages(), True)
    
    if msg == "数据库统计" or (msg.startswith("@") and "数据库统计" in msg):
        return (await get_database_stats(), False) # 统计指令不应自动撤回自身
    
    if msg.startswith("清理") and "天" in msg:
        try:
            import re
            match = re.search(r'清理(\d+)天', msg)
            if match:
                days = int(match.group(1))
                return (await cleanup_old_messages(days_to_keep=days), True)
        except:
            return ("清理指令格式错误，正确格式：清理3天", False)
    return None # 如果没有匹配任何清理指令，返回 None

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
pending_recall_messages: Set[int] = set()  # 待重试撤回的消息ID集合
pending_requests: Dict[str, int] = {}  # {echo: message_id} 用于匹配 OneBot 响应，特别是为重试和自动撤回保留 (老的同步逻辑)
pending_futures: Dict[str, asyncio.Future] = {}  # {echo: Future} 用于 `force_recall_message` 非阻塞等待响应 (新的异步逻辑)
retry_counts: Dict[int, int] = {}  # 记录每个消息ID的重试次数
MAX_RETRY_ATTEMPTS = 3  # 最大重试3次
has_printed_watched_groups = False

def remove_cq_codes(text: str) -> str:
    # 稍微改进一下，确保所有CQ码都被移除
    return re.sub(r'\[CQ:[^\]]+\]', '', text)

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

# MODIFIED: convert_jd_link function to use short_url for command generation
async def convert_jd_link(material_url: str, processed_titles: Set[str]) -> Optional[str]:
    # --- Existing JD API call (to get product info and short URL) ---
    base_url = "http://api.zhetaoke.com:20000/api/open_jing_union_open_promotion_byunionid_get.ashx"
    params = {
        "appkey": JD_APPKEY,
        "materialId": material_url,
        "unionId": JD_UNION_ID,
        "positionId": JD_POSITION_ID,
        "chainType": 3,
        "signurl": 5
    }
    debug_log(f"调用主要京东转链API: {base_url} with materialId={material_url}")
    jd_command_text = "" # Initialize JD command
    
    try:
        async with aiohttp.ClientSession() as session:
            # First API call to get product details and short URL
            async with session.get(base_url, params=params, timeout=10) as response:
                response.raise_for_status()
                result = await response.json(content_type=None)
                
                # Check for success of the first API call and extract info
                if result.get("status") == 200 and "content" in result:
                    content = result["content"][0]
                    jianjie = content.get('jianjie', '未知')
                    if jianjie in processed_titles:
                        debug_log(f"跳过重复的京东标题: {jianjie}")
                        return None
                    processed_titles.add(jianjie)
                    pict_url = content.get('pict_url', content.get('pic_url', ''))
                    image_cq = f"[CQ:image,file={pict_url}]" if pict_url else ""
                    short_url_from_zhetaoke = content.get('shorturl', '') # Get the short URL from the first API
                    
                    # --- New JD Command API call using the obtained short_url ---
                    if short_url_from_zhetaoke: # Only call if short_url was successfully obtained
                        command_api_url = "http://api.jingpinku.com/get_goods_command/api"
                        command_params = {
                            "appkey": JDJPK_APPKEY,
                            "union_id": JD_UNION_ID,
                            "position_id": JD_POSITION_ID,
                            "material_url": short_url_from_zhetaoke, # MODIFIED: 使用从上一个API获取的 short_url
                            "appid": JD_APPID
                        }
                        debug_log(f"调用京东口令API: {command_api_url} with material_url={short_url_from_zhetaoke}")
                        
                        try:
                            async with session.get(command_api_url, params=command_params, timeout=5) as cmd_response:
                                cmd_response.raise_for_status()
                                cmd_result = await cmd_response.json(content_type=None)
                                if cmd_result.get("code") == 0 and "data" in cmd_result:
                                    jd_command_text_raw = cmd_result["data"].get("jShortCommand", "")
                                    if jd_command_text_raw:
                                        jd_command_text = f"【口令】{jd_command_text_raw}"
                                    debug_log(f"京东口令转换成功: {jd_command_text_raw}")
                                else:
                                    debug_log(f"京东口令转换失败: {cmd_result.get('msg', '未知错误')}")
                        except Exception as cmd_e:
                            debug_log(f"京东口令API请求异常: {str(cmd_e)}")
                    else:
                        debug_log("未获取到有效的short_url，跳过京东口令生成。")

                    # Combine results from both APIs
                    # MODIFIED: 调整返回字符串的换行符，确保精确格式
                    return_string = (
                        f"【商品】: {jianjie}\n\n"
                        f"【券后】: {content.get('quanhou_jiage', '未知')}\n"
                        f"【佣金】: {content.get('tkfee3', '未知')}\n"
                        f"【领券买】: {short_url_from_zhetaoke}\n"
                    )
                    if jd_command_text: # 如果口令存在，则添加口令及一个换行
                        return_string += f"{jd_command_text}\n"
                    
                    if image_cq: # 如果图片CQ码存在，则添加
                        return_string += f"{image_cq}"
                    
                    return return_string
                
                # Error handling for the first JD API call
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

# MODIFIED: force_recall_message function
async def force_recall_message(ws: WebSocket, message_id: int) -> str:
    """
    发送撤回请求并使用 Future 非阻塞地等待 OneBot 响应。
    """
    if not isinstance(message_id, int):
        return f"无效的消息ID: {message_id}"
    debug_log(f"尝试强制撤回消息ID: {message_id}")
    if ws.client_state != WebSocketState.CONNECTED:
        debug_log("WebSocket连接断开，无法撤回消息")
        return "撤回失败: WebSocket连接断开"

    # 使用时间戳确保echo的唯一性，防止和旧的pending_requests冲突
    unique_echo = f"force_recall_{message_id}_{datetime.datetime.now().timestamp()}"
    payload = {
        "action": "delete_msg",
        "params": {"message_id": message_id},
        "echo": unique_echo
    }
    
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_futures[unique_echo] = future # 将 Future 存储起来，等待 main loop 填充结果

    try:
        await ws.send_text(json.dumps(payload))
        debug_log(f"已发送撤回命令: {message_id}, echo: {unique_echo}")

        # 等待 OneBot 的响应，设置超时
        response = await asyncio.wait_for(future, timeout=5.0)
        
        if response.get("status") == "ok":
            # 成功，更新数据库
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,))
            # 如果是OneBot已经撤回但我们数据库没有记录的情况，也插入为已撤回
            if cursor.rowcount == 0:
                debug_log(f"消息 {message_id} 未在数据库中，插入已撤回记录")
                cursor.execute(
                    "INSERT INTO messages (message_id, raw_message, recalled) VALUES (?, ?, ?)",
                    (message_id, "[已撤回]", 1)
                )
            conn.commit()
            conn.close()
            # 从待重试列表中移除，因为已经成功了
            pending_recall_messages.discard(message_id)
            retry_counts.pop(message_id, None) # 成功后清除重试计数
            debug_log(f"撤回成功: 消息ID {message_id}")
            return f"消息ID {message_id} 撤回成功"
        else:
            # 撤回失败
            error_msg = response.get("wording") or response.get("message", "未知错误")
            debug_log(f"撤回失败: 消息ID {message_id}, 原因: {error_msg}, 完整响应: {json.dumps(response)}")
            pending_recall_messages.add(message_id) # 添加到待重试列表
            return f"消息ID {message_id} 撤回失败: {error_msg}"
    
    except asyncio.TimeoutError:
        debug_log(f"撤回超时: 消息ID {message_id}")
        pending_recall_messages.add(message_id) # 添加到待重试列表
        # 这里不移除 future，因为 OneBot 可能只是响应慢，稍后会发送 echo。
        # 让 main loop 接收到 echo 后去处理。
        return f"消息ID {message_id} 撤回超时"
    
    except Exception as e:
        debug_log(f"force_recall_message异常: {e}")
        pending_recall_messages.add(message_id) # 添加到待重试列表
        return f"消息ID {message_id} 撤回错误: {str(e)}"
    
    finally:
        # 无论成功、失败还是超时，都应该从 pending_futures 字典中清理对应的 Future
        # 但是，对于 TimeoutError，我们希望 OneBot 延迟返回的 echo 依然能被处理，
        # 所以只有当 Future 已经被 set_result 后才安全地移除。
        pending_futures.pop(unique_echo, None)


# MODIFIED: recall_messages and recall_group_messages now use force_recall_message
async def recall_messages(ws: WebSocket, group_id: Optional[int], count: int) -> str:
    if group_id is None:
        debug_log("私聊不支持撤回")
        return "私聊不支持撤回功能"
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    query = "SELECT message_id FROM messages WHERE group_id = ? AND recalled = 0 ORDER BY id DESC LIMIT ?"
    cursor.execute(query, (group_id, count))
    messages_to_recall_rows = cursor.fetchall()
    conn.close()

    if not messages_to_recall_rows:
        debug_log(f"群 {group_id} 中没有未撤回的消息")
        return "群内没有可供撤回的消息"

    # 提取 message_id 列表并反转，以便从最旧的消息开始撤回
    message_ids = [row[0] for row in messages_to_recall_rows]
    
    results = []
    # 并发执行撤回操作，并收集结果
    tasks = [force_recall_message(ws, msg_id) for msg_id in reversed(message_ids) if isinstance(msg_id, int)]
    if tasks:
        results = await asyncio.gather(*tasks)
    
    return "\n".join(results) if results else "没有有效的消息ID可以撤回"

# MODIFIED: recall_group_messages also uses force_recall_message
async def recall_group_messages(ws: WebSocket, group_id: int) -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    query = "SELECT message_id FROM messages WHERE group_id = ? AND recalled = 0 ORDER BY id DESC"
    cursor.execute(query, (group_id,))
    messages_to_recall_rows = cursor.fetchall()
    conn.close()

    if not messages_to_recall_rows:
        debug_log(f"群 {group_id} 中没有未撤回的消息")
        return "群内没有未撤回的消息"

    message_ids = [row[0] for row in messages_to_recall_rows]

    results = []
    tasks = [force_recall_message(ws, msg_id) for msg_id in reversed(message_ids) if isinstance(msg_id, int)]
    if tasks:
        results = await asyncio.gather(*tasks)
    
    return "\n".join(results) if results else "没有有效的消息ID可以撤回"


# MODIFIED: retry_failed_recalls also uses force_recall_message
async def retry_failed_recalls(ws: WebSocket):
    while True:
        await asyncio.sleep(60)  # 每60秒重试一次
        if not pending_recall_messages:
            continue
        
        # 只查询仍存在于 pending_recall_messages 中的消息
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in pending_recall_messages)
        query = f"SELECT message_id, group_id, user_id, raw_message, created_at FROM messages WHERE message_id IN ({placeholders}) AND recalled = 0"
        messages_to_retry_list = list(pending_recall_messages) # 转换为列表以便传参
        cursor.execute(query, messages_to_retry_list)
        failed_messages = cursor.fetchall() # (message_id, group_id, user_id, raw_message, created_at)
        conn.close()

        debug_log(f"开始重试 {len(failed_messages)} 条失败的消息...")
        # 遍历这些消息，尝试撤回
        for message_id, group_id, user_id, raw_message, created_at in failed_messages:
            if ws.client_state != WebSocketState.CONNECTED:
                debug_log("WebSocket已断开，无法重试撤回")
                continue # 跳过当前循环，等待下次重试

            # 检查重试次数
            retry_counts[message_id] = retry_counts.get(message_id, 0) + 1
            if retry_counts[message_id] > MAX_RETRY_ATTEMPTS:
                debug_log(f"消息 {message_id} 已达最大重试次数 {MAX_RETRY_ATTEMPTS}，移除")
                pending_recall_messages.discard(message_id) # 从待重试集合中移除
                retry_counts.pop(message_id, None) # 清除重试计数
                continue # 跳过当前消息

            # 调用 force_recall_message 来处理重试，它会负责等待响应和更新状态
            debug_log(f"重试撤回消息 {message_id} (群 {group_id}, 用户 {user_id}, 第 {retry_counts[message_id]} 次)")
            await force_recall_message(ws, message_id)
            # await asyncio.sleep(0.5) # 适当延迟，避免洪水


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
        
        # 只显示最近的20条消息，防止消息过多导致群聊内容过长
        cst_tz = datetime.timezone(datetime.timedelta(hours=8)) # 定义CST时区（UTC+8）
        for i, message_data in enumerate(messages[:20], 1): 
            group_id = message_data[0]
            user_id = message_data[1]
            message_id = message_data[2]
            raw_message = message_data[3]
            recalled = message_data[4] if "recalled" in columns else 0
            created_at = message_data[5] if "created_at" in columns else "未知"
            
            created_at_display = "未知"
            if created_at != "未知":
                try:
                    # 假设数据库存储的是ISO格式的UTC时间字符串
                    dt_utc = datetime.datetime.fromisoformat(created_at).replace(tzinfo=datetime.timezone.utc)
                    created_at_cst = dt_utc.astimezone(cst_tz)
                    created_at_display = created_at_cst.strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    debug_log(f"查询数据库：无法解析时间字符串: {created_at}")
                    created_at_display = created_at # 解析失败时显示原始字符串
            
            group_str = f"群号: {group_id}" if group_id else "私聊"
            status = "已撤回" if recalled else "未撤回"
            result.append(
                f"消息 {i}:\n"
                f"{group_str}\n"
                f"用户ID: {user_id}\n"
                f"消息ID: {message_id}\n"
                f"状态: {status}\n"
                f"创建时间: {created_at_display}\n"
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

# MODIFIED: auto_recall_message now uses force_recall_message
async def auto_recall_message(ws: WebSocket, message_id: int, delay: int = 30):
    await asyncio.sleep(delay)
    # 调用 force_recall_message 来执行自动撤回，它会处理所有的响应和数据库更新逻辑
    debug_log(f"执行自动撤回 (延迟 {delay}s): 消息ID {message_id}")
    await force_recall_message(ws, message_id)


# MODIFIED: process_message function
# Returns: tuple[str, bool, Optional[int]] -> (reply_content, needs_auto_recall_reply, quoted_message_id_to_recall)
async def process_message(raw_message_input: str, group_id: Optional[int], user_id: int, message_id: Optional[int], ws: WebSocket, self_id: int) -> tuple[str, bool, Optional[int]]:
    debug_log(f"process_message called: raw_message_input='{raw_message_input.strip()}', group_id={group_id}, user_id={user_id}, message_id={message_id}")
    global group_timers

    # 统一获取一个纯净的消息文本，用于所有基于文本的命令匹配和解析
    cleaned_msg = remove_cq_codes(raw_message_input).strip()
    debug_log(f"process_message cleaned_msg for parsing: '{cleaned_msg}'")

    # [MODIFIED]: 如果消息是机器人自身发送的，直接跳过后续处理，避免无限转链
    # 机器人自身发送的链接转换消息不应该再次被处理
    if user_id == self_id:
        debug_log(f"跳过机器人自身消息的链接转换处理: {cleaned_msg}")
        return "", False, None # 机器人自己的消息不进行链接转换，也不需要回复 (除了_handle_message_event中特别处理的)


    # is_command 的判断逻辑
    # 增加对 "撤回id" 指令的识别
    is_command = (
        cleaned_msg == "查数据库" or 
        cleaned_msg.startswith("撤回") or # 包含 "撤回id"
        cleaned_msg.startswith("定时") or 
        cleaned_msg == "指令" or 
        cleaned_msg == "清理数据库" or
        cleaned_msg == "清理全部已撤回" or
        (cleaned_msg.startswith("清理") and "天" in cleaned_msg) or
        cleaned_msg == "数据库统计" or
        # 对于包含 CQ 码的命令，需要检查 raw_message_input，同时也要看 cleaned_msg 的文本内容
        (raw_message_input.startswith("[CQ:at,qq=") and ("撤回" in cleaned_msg or "查数据库" in cleaned_msg or "指令" in cleaned_msg or "清理数据库" in cleaned_msg or "清理全部已撤回" in cleaned_msg or "数据库统计" in cleaned_msg or ("清理" in cleaned_msg and "天" in cleaned_msg))) or
        (raw_message_input.startswith("[CQ:reply,id=") and "撤回" in cleaned_msg) # `cleaned_msg` 确保 "撤回" 是文本内容
    )
    debug_log(f"is_command判断 (cleaned_msg='{cleaned_msg}'): {is_command}")

    # 初始化返回值
    reply_content = ""
    needs_auto_recall_reply = False 
    quoted_msg_id_to_recall: Optional[int] = None # 用于传递被引用消息的ID

    # 先处理清理相关指令
    cleanup_result = await add_cleanup_commands_to_process_message(cleaned_msg)
    if cleanup_result is not None:
        reply_content, needs_auto_recall_reply = cleanup_result
        return reply_content, needs_auto_recall_reply, None # 清理指令不需要额外撤回被引用消息
    
    # 处理引用撤回指令
    reply_match = re.search(r'\[CQ:reply,id=(\d+)\]', raw_message_input)
    if reply_match and "撤回" in cleaned_msg: # `cleaned_msg` 确保 "撤回" 是文本内容
        quoted_msg_id_to_recall = int(reply_match.group(1))
        reply_content = f"好的，我将尝试撤回您引用的消息 (ID: {quoted_msg_id_to_recall})。"
        needs_auto_recall_reply = True 
        return reply_content, needs_auto_recall_reply, quoted_msg_id_to_recall # 返回被引用消息的ID
    
    # 其他指令处理逻辑
    if is_command: # user_id == self_id 已经在最前面过滤掉了，所以这里只有用户指令
        
        # 新增：撤回id xxx 指令
        recall_id_match = re.search(r'撤回id\s*(\d+)', cleaned_msg)
        if recall_id_match:
            try:
                target_message_id = int(recall_id_match.group(1))
                debug_log(f"收到撤回id指令：尝试撤回消息ID {target_message_id}")
                reply_content = await force_recall_message(ws, target_message_id)
                needs_auto_recall_reply = True
                return reply_content, needs_auto_recall_reply, None
            except ValueError:
                reply_content = "撤回id指令格式错误，正确格式：撤回id <消息ID>"
                needs_auto_recall_reply = True
                return reply_content, needs_auto_recall_reply, None

        # @某人 撤回 指令 (FIXED LOGIC HERE)
        at_match = re.search(r'\[CQ:at,qq=(\d+)', raw_message_input) # 找到原始消息中的 @CQ 码
        if at_match and ("撤回" in cleaned_msg): # 确保 @后面有“撤回”这个文本
            at_qq = int(at_match.group(1))
            
            # 从原始消息中提取 CQ 码之后的部分，并进行清理，以获取纯净的命令部分
            # `raw_message_input[at_match.end():]` 获取 @CQ 码之后的原始字符串
            command_part_after_at_raw = raw_message_input[at_match.end():]
            command_part_after_at_cleaned = remove_cq_codes(command_part_after_at_raw).strip()
            
            recall_count = None
            if "撤回全部" in command_part_after_at_cleaned:
                recall_count = None # 表示撤回所有
            else:
                n_match = re.search(r'撤回\s*(\d+)', command_part_after_at_cleaned)
                if n_match:
                    recall_count = int(n_match.group(1))
                # FIX: 这里是关键修改点，如果只有“撤回”没有数字，则视为撤回全部
                elif "撤回" in command_part_after_at_cleaned: 
                    recall_count = None # 默认为撤回全部，与原逻辑一致

            # 如果解析后发现不是有效的撤回命令，则不处理
            # 只有当 cleaned_msg 包含“撤回”，但 command_part_after_at_cleaned 无法解析为有效撤回数字时，
            # 并且也不是“撤回全部”的情况，才认为不是指令。
            if (recall_count is None and "撤回全部" not in command_part_after_at_cleaned and 
                "撤回" not in command_part_after_at_cleaned):
                 return "", False, None 

            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            
            # 查询该用户在群聊中未撤回的消息
            query_messages = "SELECT message_id FROM messages WHERE group_id = ? AND user_id = ? AND recalled = 0 ORDER BY id DESC"
            params = (group_id, at_qq)
            if recall_count is not None:
                query_messages += " LIMIT ?"
                params += (recall_count,)

            cursor.execute(query_messages, params)
            messages_to_recall_rows = cursor.fetchall()
            conn.close()

            if not messages_to_recall_rows:
                reply_content = f"未找到用户 {at_qq} 的未撤回消息"
            else:
                message_ids = [row[0] for row in messages_to_recall_rows]
                tasks = [force_recall_message(ws, mid) for mid in reversed(message_ids) if isinstance(mid, int)]
                results = await asyncio.gather(*tasks)
                reply_content = f"尝试撤回用户 {at_qq} 的消息:\n" + "\n".join(results)
            needs_auto_recall_reply = True # 回复消息也应被撤回
            return reply_content, needs_auto_recall_reply, None

        if cleaned_msg == "查数据库" or (raw_message_input.startswith("[CQ:at,qq=") and "查数据库" in cleaned_msg):
            reply_content = await query_database()
            needs_auto_recall_reply = True
            return reply_content, needs_auto_recall_reply, None

        if cleaned_msg.startswith("定时"):
            if group_id is None:
                return "私聊不支持定时撤回功能", True, None
            
            if cleaned_msg.endswith("关"):
                if group_id in group_timers and group_timers[group_id].get('enabled'):
                    group_timers[group_id]['enabled'] = False
                    if group_timers[group_id].get('task'):
                        group_timers[group_id]['task'].cancel()
                        group_timers[group_id]['task'] = None
                    debug_log(f"群 {group_id} 定时撤回已禁用")
                    reply_content = f"群 {group_id} 定时撤回功能已关闭"
                else:
                    debug_log(f"群 {group_id} 定时撤回已是关闭状态")
                    reply_content = f"群 {group_id} 定时撤回功能已是关闭状态"
                needs_auto_recall_reply = True
                return reply_content, needs_auto_recall_reply, None
            else:
                parts = cleaned_msg.split() # Use cleaned_msg here
                if len(parts) != 2:
                    return "定时指令格式错误，正确格式：定时 5 或 定时关", True, None
                try:
                    interval = int(parts[1])
                    if interval <= 0:
                        return "定时间隔必须大于 0", True, None
                    
                    if group_id not in group_timers:
                        group_timers[group_id] = {'enabled': True, 'interval': interval, 'task': None}
                    else:
                        group_timers[group_id]['enabled'] = True
                        group_timers[group_id]['interval'] = interval
                        if group_timers[group_id].get('task'):
                            group_timers[group_id]['task'].cancel()
                            group_timers[group_id]['task'] = None # 清除旧任务引用

                    group_timers[group_id]['task'] = asyncio.create_task(timer_recall_group(ws, group_id))
                    debug_log(f"群 {group_id} 定时撤回已启用，每 {interval} 分钟撤回")
                    reply_content = f"群 {group_id} 定时撤回功能已开启，每 {interval} 分钟撤回群内消息"
                    needs_auto_recall_reply = True
                    return reply_content, needs_auto_recall_reply, None
                except ValueError:
                    return "定时指令格式错误，正确格式：定时 5 或 定时关", True, None

        if cleaned_msg == "指令" or (raw_message_input.startswith("[CQ:at,qq=") and "指令" in cleaned_msg):
            reply_content = await query_commands()
            needs_auto_recall_reply = True
            return reply_content, needs_auto_recall_reply, None

        if cleaned_msg == "撤回全部" or (raw_message_input.startswith("[CQ:at,qq=") and "撤回全部" in cleaned_msg):
            reply_content = await recall_group_messages(ws, group_id)
            needs_auto_recall_reply = True
            return reply_content, needs_auto_recall_reply, None

        if cleaned_msg.startswith("撤回") and "撤回全部" not in cleaned_msg: # Use cleaned_msg here
            parts = cleaned_msg.split()
            count = 0
            # 这里的逻辑只处理纯文本的“撤回 N”或“撤回”
            if len(parts) == 1: # 只有“撤回”
                count = None # 默认撤回全部
            else: # “撤回 N”
                try:
                    count = int(parts[1])
                except (IndexError, ValueError):
                    reply_content = "撤回指令格式错误，正确格式：撤回 5 或 撤回全部"
                    needs_auto_recall_reply = True
                    return reply_content, needs_auto_recall_reply, None
            
            if count is None: # 表示撤回全部
                reply_content = await recall_group_messages(ws, group_id)
            else:
                reply_content = await recall_messages(ws, group_id, count)
            
            needs_auto_recall_reply = True
            return reply_content, needs_auto_recall_reply, None
    
    # 如果不是指令，则尝试进行链接转换 (现在这个else只会处理来自用户的消息，因为机器人自己的消息已经被前面的if user_id == self_id过滤了)
    else:
        # Link conversion always operates on the full message, but cleaned for text content.
        text_for_conversion = cleaned_msg # The already cleaned version
        cq_extracted_urls = extract_from_cq_json(raw_message_input) # Extract from raw input for CQ:json
        combined_text = text_for_conversion + " " + cq_extracted_urls
        
        results = []
        processed_titles = set()
        
        for match in TAOBAO_REGEX.finditer(combined_text):
            token = match.group(0)
            if match.group(1): token = match.group(1)
            elif match.group(2): token = match.group(2)
            elif match.group(3): token = match.group(3)
            elif match.group(4): token = match.group(4)
            elif match.group(5): token = match.group(5)
            elif match.group(6): token = match.group(6)
            elif match.group(7): token = match.group(7)
            
            result = await convert_tkl(token, processed_titles)
            if result: results.append(result)
        
        for match in JD_REGEX.finditer(combined_text):
            token = match.group(0)
            result = await convert_jd_link(token, processed_titles)
            if result: results.append(result)
        
        if results:
            reply_content = "\n".join(results)
            # MODIFIED: 转链消息机器人不需要撤回，此处将 needs_auto_recall_reply 设置为 False
            needs_auto_recall_reply = False 
            return reply_content, needs_auto_recall_reply, None
    
    # If no command or link conversion, return empty
    return "", False, None

# NEW: _handle_message_event function
async def _handle_message_event(event: Dict, websocket: WebSocket):
    """
    处理 OneBot 的 'message' 事件。
    此函数封装了原 custom_ws_adapter 中处理消息的全部逻辑，
    并在一个独立的 asyncio 任务中运行，以避免阻塞主 WebSocket 接收循环。
    """
    self_id = event.get("self_id")
    user_id = event.get("user_id")
    group_id = event.get("group_id")
    raw_message = event.get("raw_message", "")
    message_id = event.get("message_id") # 原始消息的 message_id

    # [MODIFIED]: 1. 保存原始消息到数据库 ( moved here from process_message )
    # 只有当消息来自私聊或监控群聊时才保存和进一步处理
    if group_id is None or group_id in WATCHED_GROUP_IDS:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO messages (group_id, user_id, message_id, raw_message) VALUES (?, ?, ?, ?)",
                (group_id, user_id, message_id, raw_message)
            )
            conn.commit()
            debug_log(f"【{"私聊" if group_id is None else "群聊"}】保存消息到数据库: user_id={user_id}, message_id={message_id}, raw_message={raw_message}")
        except Exception as e:
            conn.rollback()
            debug_log(f"保存消息到数据库失败: {str(e)}")
        finally:
            conn.close()
    else:
        debug_log(f"群 {group_id} 不在 WATCHED_GROUP_IDS 中，跳过保存和处理")
        return # 如果不是私聊或监控群，直接返回，不进行后续处理

    # 2. 调用 process_message 处理消息并获取回复内容及是否需要自动撤回的标志
    reply_content, needs_auto_recall_reply, quoted_msg_id_to_recall = "", False, None
    try:
        # process_message 现在会在一开始就过滤机器人自身消息的链接转换
        reply_content, needs_auto_recall_reply, quoted_msg_id_to_recall = await process_message(raw_message, group_id, user_id, message_id, websocket, self_id)
    except Exception as e:
        debug_log(f"处理消息 {message_id} 错误: {str(e)}")
        reply_content = f"处理消息失败: {str(e)}"
        needs_auto_recall_reply = True # 报错信息也应被撤回

    # 3. 如果是群聊中的命令消息，调度撤回用户发送的该命令消息本身 (延迟5秒)
    # 只有当 process_message 返回内容且指定需要自动撤回时，才认为是需要处理的用户指令。
    # 且仅针对监控群聊中的指令消息进行此操作。
    # 并且，确保这个消息不是机器人自己发的（用户指令）。
    if reply_content and needs_auto_recall_reply and message_id and group_id in WATCHED_GROUP_IDS and user_id != self_id:
        debug_log(f"用户指令消息 {message_id} 已安排5秒后自动撤回")
        asyncio.create_task(auto_recall_message(websocket, message_id, delay=5))

    # 4. 如果有回复内容，则发送回复
    sent_message_id: Optional[int] = None # 机器人发送的回复消息的ID
    if reply_content:
        reply_action = {
            "action": "send_private_msg" if event.get("message_type") == "private" else "send_group_msg",
            "params": {"user_id": user_id, "message": reply_content} if event.get("message_type") == "private" else {"group_id": group_id, "message": reply_content},
            "echo": f"reply_to_original_{message_id}_{datetime.datetime.now().timestamp()}" # 确保回复的echo唯一
        }
        reply_json = json.dumps(reply_action)
        
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_text(reply_json)
            debug_log(f"发送回复: {reply_json}")
            
            # 使用 Future 等待 OneBot 对此回复发送的确认
            reply_echo = reply_action["echo"]
            loop = asyncio.get_running_loop()
            reply_future = loop.create_future()
            pending_futures[reply_echo] = reply_future # 将 Future 存储起来

            try:
                # 等待 OneBot 的回复发送确认，带超时
                response = await asyncio.wait_for(reply_future, timeout=10.0)
                
                if response.get("status") == "ok" and response.get("data"):
                    sent_message_id = response["data"].get("message_id") # 获取机器人发送的回复消息的 message_id
                    if sent_message_id:
                        # 检查是否为指令回复消息，如果是则不写入数据库（根据您的原逻辑）
                        is_instruction_reply_content = (
                            reply_content.startswith("数据库清理完成") or
                            reply_content.startswith("数据库统计信息") or
                            reply_content.startswith("支持的指令") or
                            (reply_content.startswith("群") and "定时撤回功能" in reply_content) or
                            reply_content.startswith("撤回完成") or
                            reply_content.startswith("消息ID") or
                            reply_content.startswith("未找到该用户的未撤回消息") or
                            reply_content.startswith("私聊不支持") or
                            reply_content.startswith("群内没有未撤回的消息") or
                            reply_content.startswith("WebSocket连接断开") or
                            reply_content.startswith("定时指令格式错误") or
                            reply_content.startswith("定时间隔必须大于") or
                            reply_content.startswith("撤回指令格式错误") or
                            reply_content.startswith("清理指令格式错误") or
                            reply_content.startswith("查询数据库失败") or
                            reply_content.startswith("获取数据库统计失败") or
                            reply_content.startswith("数据库清理失败") or
                            reply_content.startswith("处理消息失败") or
                            reply_content.startswith("暂无定义的指令") or
                            reply_content.startswith("数据库中没有消息记录") or
                            reply_content.startswith("数据库中没有消息可撤回")
                        )
                        
                        # [MODIFIED]: 只有当是监控群聊，并且不是指令回复时才保存机器人自己的消息（即转链结果）
                        # 避免在私聊或非监控群保存机器人回复，也避免保存指令回复。
                        if group_id in WATCHED_GROUP_IDS and not is_instruction_reply_content:
                            conn = sqlite3.connect(DB_FILE)
                            cursor = conn.cursor()
                            try:
                                cursor.execute(
                                    "INSERT INTO messages (group_id, user_id, message_id, raw_message) VALUES (?, ?, ?, ?)",
                                    (group_id, self_id, sent_message_id, reply_content)
                                )
                                conn.commit()
                                debug_log(f"保存机器人回复到数据库: message_id={sent_message_id}, raw_message={reply_content}")
                            except Exception as e:
                                conn.rollback()
                                debug_log(f"保存机器人回复到数据库失败: {str(e)}")
                            finally:
                                conn.close()
                        else:
                            debug_log(f"跳过保存机器人回复消息到数据库 (非监控群或为指令回复): message_id={sent_message_id}, raw_message={reply_content}")
                        
                        # 5. 调度撤回机器人回复消息 (延迟5秒，如果需要)
                        if needs_auto_recall_reply:
                            delay_seconds = 5 if is_instruction_reply_content else 10 # 指令回复5秒，其他10秒
                            asyncio.create_task(auto_recall_message(websocket, sent_message_id, delay=delay_seconds))
                            debug_log(f"机器人回复消息 {sent_message_id} 已安排 {delay_seconds}秒 后自动撤回")
                        else:
                            # MODIFIED: 明确日志指出机器人回复不需撤回
                            debug_log(f"机器人回复消息 {sent_message_id} (内容: {reply_content[:20]}...) 不需要自动撤回。")

            except asyncio.TimeoutError:
                debug_log(f"等待机器人回复消息 {message_id} (echo: {reply_echo}) 的发送确认时超时")
            except Exception as e:
                debug_log(f"处理机器人回复确认时出错: {str(e)}")
            finally:
                pending_futures.pop(reply_echo, None) # 清理 Future 字典
        else:
            debug_log("WebSocket已断开，无法发送回复")

    # 6. 调度撤回被引用消息 (在机器人回复处理结束后)
    # Only if quoted_msg_id_to_recall is from the same group/private chat that is being watched/handled.
    if quoted_msg_id_to_recall is not None and (group_id is None or group_id in WATCHED_GROUP_IDS):
        debug_log(f"调度撤回被引用消息 {quoted_msg_id_to_recall} (在机器人回复处理后)")
        asyncio.create_task(force_recall_message(websocket, quoted_msg_id_to_recall))


# MODIFIED: custom_ws_adapter (Main WebSocket Listener)
async def custom_ws_adapter(websocket: WebSocket):
    global has_printed_watched_groups
    await websocket.accept()
    debug_log("WebSocket连接已创建")
    
    if not has_printed_watched_groups:
        print(f"当前监控群聊: {', '.join(map(str, WATCHED_GROUP_IDS))}")
        has_printed_watched_groups = True

    # 启动重试失败撤回的后台任务
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

            # --- 主要事件分发器 ---

            # Notice 事件 (例如消息撤回通知) - 保持不变
            if event.get("post_type") == "notice":
                notice_type = event.get("notice_type")
                if notice_type in ["group_recall", "friend_recall"]:
                    message_id_to_recall = event.get("message_id")
                    group_id = event.get("group_id") # OneBot 可能会提供 group_id
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    cursor.execute("UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id_to_recall,))
                    affected_rows = cursor.rowcount
                    if affected_rows > 0:
                        debug_log(f"处理撤回通知: 标记消息 {message_id_to_recall} 为已撤回 " + (f"来自群 {group_id}" if group_id else "【私聊】"))
                        # 如果是等待重试的消息被成功撤回，则从待重试集合中移除
                        if message_id_to_recall in pending_recall_messages:
                            pending_recall_messages.remove(message_id_to_recall)
                    else:
                        # 如果数据库中没有此消息记录，则插入一条已撤回的记录
                        debug_log(f"撤回通知: 消息 {message_id_to_recall} 未在数据库中找到，插入已撤回记录")
                        cursor.execute(
                            "INSERT OR IGNORE INTO messages (group_id, user_id, message_id, raw_message, recalled) VALUES (?, ?, ?, ?, ?)",
                            (group_id, event.get("user_id", 0), message_id_to_recall, "[已撤回]", 1)
                        )
                    conn.commit()
                    conn.close()
                continue # 处理完 notice 继续接收下一个消息

            # Meta Event - 保持不变
            if event.get("post_type") == "meta_event":
                continue # 处理完 meta_event 继续接收下一个消息

            # Message Event - 派发到新的异步任务处理
            if event.get("post_type") == "message" and event.get("message_type") in ["private", "group"]:
                # 将消息事件的处理逻辑封装到 _handle_message_event 中，并作为新的 Task 启动
                # 这样主循环不会阻塞，可以继续接收其他 WebSocket 消息（包括 echo 响应）
                asyncio.create_task(_handle_message_event(event, websocket))
                continue # 派发后立即接收下一个消息

            # Echo Event (OneBot 对之前请求的响应)
            if "echo" in event:
                echo = event["echo"]
                
                # 优先处理与 pending_futures 相关的 echo (例如 force_recall_message 的响应)
                if echo in pending_futures:
                    future = pending_futures.pop(echo) # 从字典中移除，避免重复处理
                    if not future.done(): # 如果 Future 还没有被设置结果，则设置
                        future.set_result(event)
                    debug_log(f"设置Future结果 for echo: {echo}")
                
                # 兼容处理原有的 pending_requests (可能用于旧的自动撤回或重试逻辑的延迟响应)
                # 这一块在 `force_recall_message` 统一后，理论上不应该由 `pending_requests` 触发了，
                # 但为了完全保留原有代码逻辑，保留此分支。
                elif echo in pending_requests:
                    message_id = pending_requests.pop(echo) # 从待处理请求中移除
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
                        # 如果消息成功撤回，则从待重试列表中移除
                        if message_id in pending_recall_messages:
                            pending_recall_messages.remove(message_id)
                        debug_log(f"处理延迟响应: 成功撤回并标记消息 {message_id}")
                    else:
                        debug_log(f"处理延迟响应: 撤回消息 {message_id} 失败: {event.get('message', '未知错误')}，完整响应: {json.dumps(event)}")
                        pending_recall_messages.add(message_id) # 失败则添加到待重试列表
                    conn.close()
                else:
                    debug_log(f"收到非预期或已处理的echo: {echo}, 事件: {event}") # 调试未知echo
            else:
                debug_log(f"收到非消息类型事件，且不含echo: {event.get('post_type')}") # 调试其他未知事件

    except Exception as e:
        debug_log(f"WebSocket错误: {str(e)}")
    finally:
        retry_task.cancel() # 取消重试任务
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close()
            debug_log("WebSocket连接已关闭")

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    async def run_websocket():
        while True:
            try:
                # 注册 WebSocket 路由
                app.add_api_websocket_route("/onebot/v11/ws", custom_ws_adapter)
                debug_log("WebSocket路由已添加")
                break # 成功添加路由，跳出重试循环
            except Exception as e:
                debug_log(f"WebSocket初始化失败: {str(e)}，2秒后重试")
                await asyncio.sleep(2)

    # 启动定时清理任务（如果启用）
    await start_cleanup_scheduler()

    # 并发运行 WebSocket 监听器和所有插件的初始化函数
    await asyncio.gather(
        run_websocket(), # 运行 WebSocket 监听器
        *(
            init() if asyncio.iscoroutinefunction(init) else asyncio.to_thread(init)
            for plugin in PluginManager.plugins.values()
            if (init := getattr(plugin, "init", None))
        )
    )
    debug_log("应用生命周期开始")
    yield # 应用在此处运行
    debug_log("应用生命周期结束")

def debug_log_full_api(base_url: str, params: dict):
    full_url = f"{base_url}?{'&'.join(f'{k}={quote(str(v))}' for k, v in params.items())}"
    debug_log(f"[API MATCHED] 完整API URL: {full_url}")

if __name__ == "__main__":
    debug_log("启动 FastBot 应用")
    # FastBot.build 负责初始化 FastAPI 并运行应用程序
    FastBot.build(plugins=["./plugins"], lifespan=lifespan).run(host="0.0.0.0", port=5670)
