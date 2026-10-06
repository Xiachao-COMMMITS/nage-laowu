# -*- coding: utf-8 -*-
"""那个老吴 · 对战服务器（单端口：静态资源 + WebSocket 联机）

同一个端口同时提供：
    - HTTP  静态文件 (public/)
    - WS    房间对战（权威结算：HP / 冷却 / 格挡）

本地启动:      python server.py
自定义端口:    PORT=8080 python server.py
    （多数 PaaS 会注入 PORT，这里会自动使用）

本地开发: 手机与电脑连同一 WiFi 时，扫码或访问 http://<IP>:<PORT>
          部署到公网（云服务器 / 隧道）后，好友无需同一 WiFi，打开同一链接即可联机。
"""
import json
import mimetypes
import os
import random
import secrets
import socket
import string
import threading
import time
from urllib.parse import unquote, urlparse
from pathlib import Path

try:
    import qrcode          # 本地扫码用；云端可不装
except ImportError:
    qrcode = None

from websockets.datastructures import Headers
from websockets.http11 import Response
from websockets.sync.server import serve

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
PORT = int(os.environ.get("PORT") or os.environ.get("HTTP_PORT") or 8000)

# Windows 自带的 mimetypes 表里没有 .webp，补上，避免图片被当成 octet-stream
mimetypes.add_type("image/webp", ".webp")

# 猫属性（与前端 public/index.html 中 CATS 保持一致）
CATS = {
    "cat1": {"dmg": 10, "cd": 1.2},   # 长条橘猫：均衡
    "cat3": {"dmg": 14, "cd": 1.7},   # 炸毛橘猫：重击
}

LOCK = threading.Lock()
ROOMS = {}  # code -> Room

# 手机切到别的 App（比如去微信发房间号）时，浏览器会挂起甚至掐断 WebSocket。
# 这里给房间一个宽限期：掉线后不立刻销毁，方便重连回来，也避免好友此时加入失败。
GRACE_SECONDS = 300


class Room:
    def __init__(self, code):
        self.code = code
        self.conns = [None, None]      # ws 连接，按座位
        self.tokens = [None, None]     # 重连令牌，按座位
        self.cats = [None, None]
        self.hp = [100, 100]
        self.defending = [False, False]
        self.last_atk = [0.0, 0.0]
        self.started = False
        self.over = False
        self.expire = None             # 房间内一个人都没了之后的回收时间（time.monotonic）

    def broadcast(self, obj):
        msg = json.dumps(obj)
        for c in self.conns:
            if c is not None:
                try:
                    c.send(msg)
                except Exception:
                    pass

    def alive(self):
        """房间里还连着几个人。"""
        return sum(1 for c in self.conns if c is not None)

    def touch(self):
        """刷新回收时间：房里有人 -> 永不过期；一个人都没有 -> 5 分钟后回收。"""
        self.expire = None if self.alive() else time.monotonic() + GRACE_SECONDS

    def seat_of(self, ws):
        for i, c in enumerate(self.conns):
            if c is ws:
                return i
        return -1

    def seat_of_token(self, token):
        for i, tk in enumerate(self.tokens):
            if tk and tk == token:
                return i
        return -1


def make_code():
    while True:
        code = "".join(random.choices(string.digits, k=4))
        if code not in ROOMS:
            return code


def make_token():
    return secrets.token_urlsafe(12)


def handle_msg(ws, data):
    t = data.get("t")

    if t == "ping":                       # 保活：手机端定期发，防止连接被运营商/浏览器掐掉
        ws.send(json.dumps({"t": "pong"}))
        return

    if t == "create":
        # 只占一个 4 位号码，不把人放进房间：
        # 创建者也要自己去「加入房间」输同一个号码才能进去
        code = make_code()
        room = Room(code)
        room.touch()                   # 还没人进来：从这一刻起保留 5 分钟
        with LOCK:
            ROOMS[code] = room
        ws.send(json.dumps({"t": "created", "room": code}))
        return

    if t == "join":
        code = str(data.get("room", "")).strip()
        cat = data.get("cat") if data.get("cat") in CATS else "cat1"
        token = make_token()
        seat = -1
        with LOCK:
            room = ROOMS.get(code)
            if room is not None:
                # 谁先来谁坐空位（0 号或 1 号），满两个人就开局
                seat = next((i for i in (0, 1) if room.conns[i] is None), -1)
                if seat >= 0:
                    room.conns[seat] = ws
                    room.tokens[seat] = token
                    room.cats[seat] = cat
                    room.started = all(c is not None for c in room.conns)
                    room.touch()       # 有人进来了 -> 永不过期
        if room is None:
            ws.send(json.dumps({"t": "error", "msg": "房间不存在或已过期"}))
            return
        if seat < 0:
            ws.send(json.dumps({"t": "error", "msg": "房间已满"}))
            return
        ws._room = code
        ws.send(json.dumps({"t": "joined", "room": code, "seat": seat, "token": token}))
        if room.started:
            room.broadcast({"t": "start", "cats": room.cats})
        return

    if t == "rejoin":
        # 手机切走再切回（去微信发房间号）时重连：凭令牌回到原座位，房间不丢
        code = str(data.get("room", "")).strip()
        token = str(data.get("token", ""))
        with LOCK:
            room = ROOMS.get(code)
        if not room:
            ws.send(json.dumps({"t": "rejoin_failed", "msg": "房间已过期，请重新建房"}))
            return
        seat = room.seat_of_token(token)
        if seat < 0:
            ws.send(json.dumps({"t": "rejoin_failed", "msg": "房间已过期，请重新建房"}))
            return
        room.conns[seat] = ws
        room.touch()                   # 重连回来 -> 永不过期
        ws._room = code
        ws.send(json.dumps({
            "t": "rejoined", "room": code, "seat": seat,
            "cats": room.cats, "started": room.started, "hp": room.hp,
        }))
        return

    if t == "leave":
        # 玩家主动退出房间（结算后返回选模式/选猫），通知对手并回收房间
        code = getattr(ws, "_room", None)
        if code:
            with LOCK:
                room = ROOMS.pop(code, None)
            if room:
                for c in room.conns:
                    if c and c is not ws:
                        c._room = None
                        try:
                            c.send(json.dumps({"t": "opp_left"}))
                        except Exception:
                            pass
            ws._room = None
        return

    # 以下需要已在房间内
    code = getattr(ws, "_room", None)
    with LOCK:
        room = ROOMS.get(code)
    if not room or room.over:
        return
    seat = room.seat_of(ws)
    if seat < 0:
        return
    opp = 1 - seat

    if t == "attack":
        now = time.monotonic()
        spec = CATS.get(room.cats[seat], CATS["cat1"])
        if now - room.last_atk[seat] < spec["cd"]:
            return  # 冷却中，忽略
        room.last_atk[seat] = now
        # 转发动作给对手（表现层），并做权威结算
        blocked = room.defending[opp]
        dmg = 0 if blocked else spec["dmg"]
        if dmg:
            room.hp[opp] = max(0, room.hp[opp] - dmg)
        room.broadcast({
            "t": "event", "action": "attack", "seat": seat,
            "blocked": blocked, "dmg": dmg, "hp": room.hp,
        })
        if room.hp[opp] <= 0:
            room.over = True
            room.broadcast({"t": "gameover", "winner": seat})
        return

    if t == "defend":
        on = bool(data.get("on"))
        if room.defending[seat] != on:
            room.defending[seat] = on
            room.broadcast({"t": "event", "action": "defend", "seat": seat, "on": on})
        return


def on_close(ws):
    code = getattr(ws, "_room", None)
    if not code:
        return
    with LOCK:
        room = ROOMS.get(code)
        if not room:
            return
        seat = room.seat_of(ws)
        if seat < 0:
            return
        room.conns[seat] = None
        room.touch()      # 还有人 -> 永不过期；全走光了 -> 从这一刻起保留 5 分钟
        started, over = room.started, room.over

    # 对局中掉线：先等一下，对方可能只是切出去回个消息马上就回来，别急着判负
    if started and not over:
        threading.Thread(target=_notify_if_lost, args=(code, seat), daemon=True).start()


def _notify_if_lost(code, seat):
    time.sleep(10)
    with LOCK:
        room = ROOMS.get(code)
        if room is None or room.conns[seat] is not None or room.over:
            return
    room.broadcast({"t": "opp_left"})


def reap_rooms():
    """后台回收房间。

    注意这里是「结构性」保证：只有**一个人都没有**、且过了 5 分钟宽限期的房间才会被删。
    房里只要还有 1 个人（或 2 个人），这个条件不成立，永远跳过。
    """
    while True:
        time.sleep(5)
        now = time.monotonic()
        with LOCK:
            dead = [c for c, r in ROOMS.items()
                    if r.alive() == 0 and r.expire is not None and now > r.expire]
            for c in dead:
                ROOMS.pop(c, None)


def ws_handler(ws):
    try:
        for raw in ws:
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            try:
                handle_msg(ws, data)
            except Exception as e:
                print("[ws] error:", e)
    finally:
        on_close(ws)


def _http_response(status, reason, body, ctype="text/plain; charset=utf-8", cache="no-cache"):
    headers = Headers()
    headers["Content-Type"] = ctype
    headers["Content-Length"] = str(len(body))
    headers["Cache-Control"] = cache
    return Response(status, reason, headers, body)


def serve_static(request):
    """把 HTTP 请求映射到 public/ 下的文件。"""
    path = unquote(urlparse(request.path).path)
    if path.endswith("/"):
        path += "index.html"
    target = (PUBLIC / path.lstrip("/")).resolve()
    try:
        target.relative_to(PUBLIC.resolve())
    except ValueError:
        return _http_response(403, "Forbidden", b"403 Forbidden")
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        return _http_response(404, "Not Found", b"404 Not Found")
    ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
        ctype += "; charset=utf-8"
    # 图片/音频/JSON 带 ?v= 版本号，可长缓存；HTML 不缓存，保证每次拿到最新版
    cache = "no-cache" if target.suffix.lower() == ".html" else "public, max-age=604800"
    return _http_response(200, "OK", target.read_bytes(), ctype, cache)


def process_request(connection, request):
    """WebSocket 握手放行，其余请求当作普通 HTTP 静态资源处理。"""
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    return serve_static(request)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ip = lan_ip()
    url = f"http://{ip}:{PORT}"

    print("=" * 46)
    print("  那个老吴 · 对战服务器已启动")
    print(f"  本机访问:   http://localhost:{PORT}")
    print(f"  手机访问:   {url}")
    print(f"  端口:       {PORT}（静态资源 + 联机 WebSocket 同一端口）")
    print("=" * 46)
    if qrcode is not None:
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
        print("\n若手机无法访问，请检查防火墙是否放行 Python。\n")

    threading.Thread(target=reap_rooms, daemon=True).start()

    with serve(ws_handler, "0.0.0.0", PORT, process_request=process_request) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
