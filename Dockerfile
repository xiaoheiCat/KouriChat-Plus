# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（如需要可自行加）
RUN sed -i 's@deb.debian.org@mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list.d/debian.sources \
 && sed -i 's@security.debian.org@mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# 复制当前项目
COPY . .

# 使用清华源升级 pip
RUN python3 -m pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple --upgrade pip

# 安装依赖（跳过 Windows 专属包）
RUN pip config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
 && grep -iv "wxautold\|uiautomation\|pyautogui" requirements.txt > /tmp/requirements_linux.txt \
 && python3 -m pip install -r /tmp/requirements_linux.txt

# 创建必要目录
RUN mkdir -p /app/data/config /app/data/avatars

# 暴露 WebUI 端口
EXPOSE 7860
