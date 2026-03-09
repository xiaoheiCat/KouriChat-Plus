# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN sed -i 's@deb.debian.org@mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list.d/debian.sources \
 && sed -i 's@security.debian.org@mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# 升级 pip 并配置清华源
RUN python3 -m pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple --upgrade pip \
 && pip config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

# 【分层缓存关键】先只复制依赖文件，安装依赖
# 这样只要 requirements.txt 不变，此层就会命中缓存，无需重新安装
COPY requirements.txt .
RUN python3 -m pip install -r requirements.txt

# 再复制其余项目文件（代码变动不会使依赖层缓存失效）
COPY . .

# 创建必要目录
RUN mkdir -p /app/data/config /app/data/avatars

# 暴露端口：
#   7860 - WebUI (run_config_web.py)
#   8081 - 企业微信 Webhook 回调
EXPOSE 7860 8081
