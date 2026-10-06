# 那个老吴 · 猫猫互骂对战

一款 H5 小游戏：玩家操纵一只猫，用「叫声」攻击对手，**歪头可格挡**（歪头期间不能攻击），谁的血先归零谁输。
支持**人机对战**与**好友联机**，手机浏览器打开即玩，可跨网络联机。

- 前端：单文件 `public/index.html`（Canvas 渲染 + Web Audio 音效，无任何框架和构建步骤）
- 后端：`server.py`，**单端口**同时提供静态资源与房间制 WebSocket 联机（权威结算 HP / 冷却 / 格挡）

## 玩法

| 操作 | 效果 |
|------|------|
| 攻击 | 发出叫声，命中扣对方血；不同猫攻击力 / 冷却不同 |
| 歪头 | 格挡对方这一次攻击；歪头期间自己不能攻击 |
| 胜负 | 先把对方血量打到 0 的一方获胜 |

| 角色 | 攻击力 | 冷却 |
|------|--------|------|
| 长条橘猫 | 10 | 1.2s |
| 炸毛橘猫 | 14 | 1.7s |

## 目录结构

```
public/
  index.html            游戏本体（HTML + CSS + JS 单文件）
  assets/
    bg.webp             背景
    laowu.mp3           攻击音效
    nayige.mp3          歪头音效
    cats/               猫的素材：{cat}_body / _head / _full.webp + {cat}.json（尺寸与旋转轴心）
server.py               对战服务器：单端口 = 静态文件 + WebSocket 房间
requirements.txt        Python 依赖
Dockerfile              容器化部署（可选）
tools/
  extract_cats.py       素材预处理：去白底 + 头身分层
  optimize_assets.py    资源压缩：缩放 + 转 WebP，并同步 json 里的坐标
  deploy_github.py      一键推送到 GitHub（main 源码分支 + gh-pages 静态分支）
```

## 本地运行

```bash
pip install -r requirements.txt
python server.py
```

打开 http://localhost:8000 即可。

- 同一 WiFi 下，手机访问终端里打印的 `http://<电脑IP>:8000`（终端会同时输出二维码）
- 只玩单机的话，直接打开 `public/index.html` 也行，不需要启动服务端

## 好友联机怎么玩

**不要求在同一 WiFi**，跨城市、跨运营商都行，只要双方都能打开同一个公网链接。

1. 两个人都打开同一个链接
2. 都选「好友联机」→ 各自选猫
3. 任意一人点「**生成房间号**」，拿到一个 4 位号码（这一步只是拿号码，人不进房间）
4. 把号码发给对方
5. **两个人都**点「加入房间」→ 输入**同一个号码** → 点「加入房间」
6. 第二个人提交后，两边自动开局

房间规则：

| 房间状态 | 存活时间 |
|----------|----------|
| 0 人（只生成了号码） | 5 分钟，超时作废需重新生成 |
| 1 人 | 永不过期 |
| 2 人 | 永不过期 |

## 部署

### 方式一：本机 + 隧道（最快，零成本）

适合自己玩或小范围分享。本机跑服务，用隧道把 8000 端口暴露到公网。

**Cloudflare 临时隧道**（免账号，但地址每次重启都会变）：

```bash
cloudflared tunnel --url http://localhost:8000
```

**ngrok 固定域名**（免费账号会送一个固定的 dev 域名，重启不变）：

```bash
ngrok config add-authtoken <你的authtoken>
ngrok http 8000
```

两种方式的共同限制：**电脑必须保持开机联网，进程一停链接就失效**。

### 方式二：云服务器（推荐长期使用）

`server.py` 把静态资源和 WebSocket 放在同一个端口，所以只需要暴露**一个**端口。

以腾讯云轻量应用服务器 / 阿里云 ECS 为例：

1. 安装 Python 3.10+，上传本项目
2. `pip install -r requirements.txt`
3. 控制台**安全组放行 8000 端口**
4. 后台常驻启动：

   ```bash
   nohup python server.py > server.log 2>&1 &
   ```

5. 验证：浏览器打开 `http://<公网IP>:8000`，能进游戏、能建房即成功

如果要用域名 + https，建议在服务器前面挂一个 Nginx 做 TLS 终结，
并把 WebSocket 的 `Upgrade` / `Connection` 头透传过去（前端已是 `wss://` 同源连接，无需改代码）。

### 方式三：GitHub Pages（只能玩单机）

```bash
python tools/deploy_github.py      # 需要先设置 GH_TOKEN 环境变量
```

推完后到仓库 **Settings → Pages**，Source 选 **Deploy from a branch**，分支选 **gh-pages** / **/(root)**。
稍等 1~2 分钟访问 `https://<用户名>.github.io/<仓库名>/`。

> GitHub Pages 只能托管静态页面，**跑不了 WebSocket**，所以这种方式**只能玩人机对战**，
> 联机需要另外部署 `server.py`（方式一或方式二）。

### 前后端分开部署时

页面和联机服务**同源**时不用改任何代码，前端会自动连 `ws(s)://<当前页面域名>`。
只有分别部署在不同机器时，才需要改 `public/index.html` 里的这一行：

```js
const WS_ENDPOINT = '';   // 例如 'ws://123.45.67.89:8000'
```

- 页面是 **http**：用 `ws://<公网IP>:<端口>`
- 页面是 **https**：必须用 `wss://...`，否则浏览器会拦截混合内容

## 实现要点

**单端口**
`server.py` 用 `websockets` 的 `process_request` 钩子：WebSocket 握手放行，
其余请求按普通 HTTP 静态资源处理，因此一个端口就够，省掉反代和额外安全组规则。

**权威结算**
伤害、冷却、格挡全部由服务端结算后广播，客户端只做表现，避免两端状态不一致。

**移动端适配**
- 音频必须在「真实用户手势」里解锁，否则 `AudioContext` 一直 suspended —— 见 `unlockAudio()`；
  iOS 上额外走 `<audio>` 元素通道，因为静音拨片会静音 Web Audio 却不影响 `<audio>`
- 手机切后台会掐断 WebSocket，因此有：连接保活 ping、切回前台自动重连、
  凭 token `rejoin` 回到原座位、房间掉落不立即销毁（宽限期）
- 背景交给 CSS 层由 GPU 合成；`requestAnimationFrame` 只在战斗进行时运行

**渲染**
猫的头和身是两张分层图，歪头时绕 `{cat}.json` 里的 `pivot` 旋转头部图层。

## 改素材

素材流水线是两步：

```bash
python tools/extract_cats.py        # 原图 -> 去白底 + 头身分层，输出 PNG + json
python tools/optimize_assets.py     # PNG -> 缩放 + 转 WebP，并同步 json 里的 w/h/pivot
```

改完记得把 `public/index.html` 里的 `ASSET_V` 版本号 +1，
配合服务端对静态资源的 7 天缓存，老用户才会自动拿到新图。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `PORT` / `HTTP_PORT` | 8000 | 服务端口（静态资源 + 联机 WebSocket 共用） |

## 常见问题

**手机上没声音？**
先点一下屏幕任意位置（这一步用于解锁音频），再检查手机媒体音量。
如果是在微信里打开的，可以试试右上角「在浏览器中打开」。

**提示「房间不存在或已过期」？**
- 号码是否输错
- 生成号码后超过 5 分钟且一直没人进入，房间会作废，重新生成一个
- 服务端重启会清空所有房间

**链接突然打不开了？**
用了临时隧道的话，电脑重启、休眠或隧道进程退出都会导致地址失效。
检查 `python` 和隧道进程是否还在运行；换成固定域名或云服务器可以避免这个问题。

## 协议

仅用于学习交流。游戏内的图片与音频素材由项目作者提供。
