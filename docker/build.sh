#!/bin/bash

# 创建临时目录用于下载
echo "正在从GitHub下载start文件夹..."
rm -rf ./start
mkdir -p ./start

# 下载整个start文件夹
git clone https://github.com/abc/start.git ./start

# 确保目录存在
if [ ! -f "./start/main.py" ] || [ ! -f "./start/config.py" ]; then
    echo "错误：下载的start文件夹中缺少必要文件！"
    exit 1
fi

# 构建Docker镜像
echo "正在构建Docker镜像..."
docker build -t python-app:latest .

# 检查构建是否成功
if [ $? -eq 0 ]; then
    echo "Docker镜像构建成功！"
    
    # 运行容器
    echo "是否要立即运行容器？(y/n)"
    read answer
    
    if [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
        echo "正在启动容器..."
        docker run -it -p 5670:5670 \
            -v $(pwd)/start:/app \
            --name python-app python-app:latest
    else
        echo "如需手动启动容器，请运行以下命令:"
        echo "docker run -it -p 5670:5670 \\
            -v $(pwd)/start:/app \\
            --name python-app python-app:latest"
    fi
else
    echo "构建失败，请检查错误信息。"
fi 
