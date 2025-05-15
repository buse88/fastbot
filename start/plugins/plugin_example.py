from fastbot.event.message import PrivateMessageEvent, GroupMessageEvent
from fastbot.plugin import on
import logging

# 配置日志级别为 DEBUG
logging.basicConfig(level=logging.DEBUG)

# 监听私聊消息
@on(PrivateMessageEvent)
async def handle_private_message(event: PrivateMessageEvent):
    logging.debug(f"Received private message: {event.plaintext}")

# 监听群聊消息
@on(GroupMessageEvent)
async def handle_group_message(event: GroupMessageEvent):
    logging.debug(f"Received group message: {event.plaintext}")
