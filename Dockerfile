# 那个老吴 · 对战服务器容器
# 构建: docker build -t laowu .
# 运行: docker run -d -p 8000:8000 --name laowu laowu
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY public ./public

ENV PORT=8000
EXPOSE 8000

CMD ["python", "server.py"]
