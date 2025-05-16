FROM python:3.12-slim

WORKDIR /app

# 完全替换sources.list，使用Debian 12 (Bookworm)的源
RUN rm -f /etc/apt/sources.list.d/* && \
    echo "deb https://mirrors.ustc.edu.cn/debian/ bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb https://mirrors.ustc.edu.cn/debian/ bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.ustc.edu.cn/debian/ bookworm-backports main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.ustc.edu.cn/debian-security/ bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list
    
# 更新并安装必要软件包（添加错误处理）
RUN apt-get update || (echo "APT更新失败，使用备用方法" && \
    apt-get -o Acquire::AllowInsecureRepositories=true update) && \
    apt-get install -y git curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Configure pip to use Chinese mirrors globally
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn \
    && pip install --upgrade pip

# Download requirements.txt from GitHub
RUN curl -L https://raw.githubusercontent.com/buse88/fastbot/refs/heads/main/docker/requirements.txt -o requirements.txt

# Install dependencies using Chinese mirrors
RUN pip install --no-cache-dir -r requirements.txt

# Create directories for mounted volumes
RUN mkdir -p /app/config

# Create entrypoint script to check configuration
RUN echo '#!/bin/sh' > /usr/local/bin/entrypoint.sh && \
    echo 'cd /app' >> /usr/local/bin/entrypoint.sh && \
    echo 'python -c "import sys, os; sys.path.append(os.path.dirname(os.path.realpath(__file__))); from config.config import WATCHED_GROUP_IDS; exit(1) if WATCHED_GROUP_IDS == \"000,111\" else None" || echo "请根据提示修改config.py中的WATCHED_GROUP_IDS参数"' >> /usr/local/bin/entrypoint.sh && \
    echo 'exec python main.py' >> /usr/local/bin/entrypoint.sh && \
    chmod +x /usr/local/bin/entrypoint.sh

# Expose port 5670
EXPOSE 5670

# Set the entrypoint script as the container's entry point
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# This CMD will be overridden by the entrypoint if config validation fails
CMD ["python", "main.py"] 
