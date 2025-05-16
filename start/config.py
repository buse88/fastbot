# config.py

#日志输出
DEBUG_MODE = False
WATCHED_GROUP_IDS = [123,456] #监听到qq群

# 淘宝客API配置（请替换为实际值）
APP_KEY = ""
SID = ""
PID = ""
RELATION_ID  = ""

# 京东-折京客
JD_APPID = ""
JD_APPKEY = ""
JD_UNION_ID = ""
JD_POSITION_ID = ""
 
# 京东精品库API配置
# JD_APPID = ""
# JD_APPKEY = ""
# JD_UNION_ID = ""
# JD_POSITION_ID = ""

# 支持的指令及其说明
COMMANDS = {
    "撤回 n": "撤回指定群最近 n 条消息，例如 '撤回 5'\n",
    "撤回全部": "撤回数据库中所有消息\n",
    "查数据库": "查询数据库中所有消息记录\n",
    "指令": "显示所有支持的指令及其说明\n",
    "定时 n": "定时 n：启动定时撤回，设置每隔 n 分钟自动撤回数据库中的所有消息。定时关：关闭定时撤回功能 默认状态为关闭\n"
}
