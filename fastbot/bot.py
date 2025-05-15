import logging
import signal
import asyncio
from fastbot.bot import FastBot
from starlette.websockets import WebSocketDisconnect

# 配置日志级别为 DEBUG
logging.basicConfig(level=logging.DEBUG)

# 定义优雅关闭函数
async def graceful_shutdown(bot):
    print("正在关闭机器人...")
    # 如果框架提供了关闭方法，可以在这里调用
    # await bot.close()  

# 信号处理函数
def handle_exit(signum, frame, bot):
    print(f"收到退出信号 {signum}，准备优雅退出...")
    asyncio.create_task(graceful_shutdown(bot))
    # 强制终止所有异步任务
    loop = asyncio.get_running_loop()
    for task in asyncio.all_tasks(loop):
        if task != asyncio.current_task(loop):
            task.cancel()
    loop.stop()

if __name__ == "__main__":
    # 构建 FastBot 实例并加载插件
    bot = FastBot.build(plugins=["plugins.plugin_example"])
    
    # 注册 Ctrl+C 信号处理
    signal.signal(signal.SIGINT, lambda signum, frame: handle_exit(signum, frame, bot))
    
    # 运行 FastBot
    bot.run(host="0.0.0.0", port=8080)
