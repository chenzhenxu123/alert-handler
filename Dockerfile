# 第一阶段：构建环境
FROM python:3.9-slim as builder

WORKDIR /app
COPY requirements.txt .

# 安装构建依赖和Python包
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev  && \
    pip install --user --no-cache-dir -r requirements.txt

# 第二阶段：运行环境
FROM python:3.9-slim

WORKDIR /app

# 从构建阶段复制已安装的包
COPY --from=builder /root/.local /root/.local
COPY *.py ./

# 设置环境变量
ENV PATH=/root/.local/bin:$PATH

# 暴露端口
EXPOSE 5000

# 使用 CMD 指令运行命令，将输出发送到标准输出
CMD ["sh", "-c", "nohup python3.9 webhook.py "]