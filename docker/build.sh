#!/bin/bash

# 该版本安装的是fastbot-20251.28版非原作者最新版，最新版有部分问题已经修改完放在我仓库fastbot文件夹
# 设置Docker内存限制（默认为2GB）
DOCKER_MEMORY_LIMIT="2g"
DOCKER_CPU_LIMIT="2"

# 检查并安装必要的工具
check_and_install_tool() {
    if ! command -v $1 &> /dev/null; then
        echo "正在安装 $1..."
        apt-get update && apt-get install -y $1 || yum install -y $1
    fi
}

check_and_install_tool curl

# 创建临时目录用于下载
echo "正在从GitHub下载start文件夹..."
rm -rf ./start
mkdir -p ./start

# 直接下载start文件夹中的主要文件
echo "正在下载必要文件..."
curl -L https://raw.githubusercontent.com/buse88/fastbot/refs/heads/main/start/main.py -o ./start/main.py
curl -L https://raw.githubusercontent.com/buse88/fastbot/refs/heads/main/start/config.py -o ./start/config.py

# 检查是否成功下载了文件
if [ ! -f "./start/main.py" ] || [ ! -f "./start/config.py" ]; then
    echo "错误：下载文件失败！请检查网络连接或代理设置。"
    exit 1
fi

echo "文件下载成功！"

# 构建Docker镜像
echo "正在构建Docker镜像..."
docker build --memory="${DOCKER_MEMORY_LIMIT}" \
             -t python-app:latest .

# 检查构建是否成功
if [ $? -eq 0 ]; then
    echo "Docker镜像构建成功！"
    
    # 运行容器
    echo "是否要立即运行容器？(y/n)"
    read answer
    
    if [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
        echo "正在启动容器..."
        docker run -it -p 5670:5670 \
            -m "${DOCKER_MEMORY_LIMIT}" \
            --cpus="${DOCKER_CPU_LIMIT}" \
            -v $(pwd)/start:/app \
            --name python-app python-app:latest
    else
        echo "如需手动启动容器，请运行以下命令:"
        echo "docker run -it -p 5670:5670 \\
            -m ${DOCKER_MEMORY_LIMIT} \\
            --cpus=${DOCKER_CPU_LIMIT} \\
            -v $(pwd)/start:/app \\
            --name python-app python-app:latest"
    fi
else
    echo "构建失败，请检查错误信息。"
fi 
