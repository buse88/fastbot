FROM python:3.12-slim

WORKDIR /app

# 完全替换sources.list，使用Debian 12 (Bookworm)的源
RUN rm -f /etc/apt/sources.list.d/* && \
    echo "deb https://mirrors.ustc.edu.cn/debian/ bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb https://mirrors.ustc.edu.cn/debian/ bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.ustc.edu.cn/debian/ bookworm-backports main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.ustc.edu.cn/debian-security/ bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list

# 更新并安装必要软件包，包括时区数据
RUN apt-get update || (echo "APT更新失败，使用备用方法" && \
    apt-get -o Acquire::AllowInsecureRepositories=true update) && \
    apt-get install -y git curl tzdata && \
    # 设置时区为上海（中国）
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 配置pip使用清华源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn \
    && pip install --upgrade pip

# 下载 requirements.txt
RUN curl -L https://gdw.8080k.eu.org/https://raw.githubusercontent.com/buse88/fastbot/refs/heads/main/docker/requirements.txt -o requirements.txt

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 创建挂载目录
RUN mkdir -p /app/config

# 创建 entrypoint 脚本
RUN echo '#!/bin/sh' > /usr/local/bin/entrypoint.sh && \
    echo 'cd /app' >> /usr/local/bin/entrypoint.sh && \
    echo 'python -c "import sys, os; sys.path.append(os.getcwd()); from config import WATCHED_GROUP_IDS; exit(1) if WATCHED_GROUP_IDS == [0, 111] else None" || echo "请根据提示修改config.py中的WATCHED_GROUP_IDS参数"' >> /usr/local/bin/entrypoint.sh && \
    echo 'exec python main.py' >> /usr/local/bin/entrypoint.sh && \
    chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 5670

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

CMD ["python", "main.py"]
