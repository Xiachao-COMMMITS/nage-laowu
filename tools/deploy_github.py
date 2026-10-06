# -*- coding: utf-8 -*-
"""一键把「那个老吴」推送到 GitHub。

用法:
    set GH_TOKEN=你的令牌            # PowerShell: $env:GH_TOKEN="xxx"
    python tools/deploy_github.py    # 可选: python tools/deploy_github.py 仓库名

会做三件事:
    1. 在令牌所属账号下创建公开仓库（已存在则跳过）
    2. 推送 main 分支：全部源码
    3. 推送 gh-pages 分支：仅 public/ 静态站点（供 GitHub Pages 托管）

推送完成后到仓库 Settings -> Pages，Source 选 “Deploy from a branch”，
分支选 gh-pages / (root) 保存即可。
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
REPO = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("REPO_NAME") or "nage-laowu")
DEFAULT_BRANCH = "main"
PAGES_BRANCH = "gh-pages"

# 不入库的开发产物文件名特征
SKIP_NAMES = ("preview_cat1.jpg", "preview_cat2.jpg", "preview_cat3.jpg")


def api(method, path, body=None):
    url = "https://api.github.com" + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "laowu-deploy")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        raw = r.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def get_ref_sha(owner, repo, branch):
    """返回分支当前 commit sha；分支/仓库为空则返回 None。"""
    try:
        return api("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}")["object"]["sha"]
    except urllib.error.HTTPError:
        return None


def push_branch(owner, repo, branch, files, message):
    """用 Git Data API 一次性提交整个目录树（files: [(仓库路径, 本地Path)]）。"""
    tree = []
    for repo_path, local in files:
        content = base64.b64encode(local.read_bytes()).decode("ascii")
        sha = api("POST", f"/repos/{owner}/{repo}/git/blobs",
                  {"content": content, "encoding": "base64"})["sha"]
        tree.append({"path": repo_path, "mode": "100644", "type": "blob", "sha": sha})

    parent = get_ref_sha(owner, repo, branch)
    # 不带 base_tree：整棵树直接替换，保证仓库内容与本地目录一致，不会残留上一次的旧文件
    new_tree = api("POST", f"/repos/{owner}/{repo}/git/trees", {"tree": tree})["sha"]

    commit = api("POST", f"/repos/{owner}/{repo}/git/commits",
                 {"message": message, "tree": new_tree,
                  "parents": [parent] if parent else []})["sha"]

    if parent:
        api("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}", {"sha": commit})
    else:
        api("POST", f"/repos/{owner}/{repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": commit})
    return commit


def ensure_initialized(owner, repo, branch):
    """空仓库调 Git Data API 会返回 409，先放一个占位提交把默认分支建出来。"""
    if get_ref_sha(owner, repo, branch):
        return
    api("PUT", f"/repos/{owner}/{repo}/contents/.gitkeep", {
        "message": "chore: 初始化空仓库",
        "content": base64.b64encode(b"").decode("ascii"),
    })
    print("✅ 已初始化默认分支（空仓库需要先有一个提交）")


def collect_public():
    """public/ 下的静态资源（排除开发预览图）。"""
    out = []
    pub = ROOT / "public"
    for p in sorted(pub.rglob("*")):
        if p.is_file() and p.name not in SKIP_NAMES and p.name != ".DS_Store":
            out.append((p.relative_to(pub).as_posix(), p))
    return out


def main():
    if not TOKEN:
        print("❌ 未检测到 GH_TOKEN 环境变量，请先设置后再运行。")
        return 1

    me = api("GET", "/user")
    owner = me["login"]
    print(f"👉 账号: {owner}   仓库: {REPO}")

    # 1) 建仓（已存在会返回 422）
    try:
        api("POST", "/user/repos", {
            "name": REPO, "private": False, "auto_init": False,
            "description": "那个老吴 · 猫猫互骂对战 H5 小游戏",
        })
        print("✅ 已创建公开仓库")
    except urllib.error.HTTPError as e:
        if e.code == 422:
            print("ℹ️ 仓库已存在，跳过创建")
        else:
            raise

    public = collect_public()
    print(f"📦 静态资源 {len(public)} 个文件")

    ensure_initialized(owner, REPO, DEFAULT_BRANCH)

    # 2) main 分支：源码（保持 public/ 前缀，与本地目录结构一致）
    source = [(f"public/{rel}", p) for rel, p in public]
    for name in ("server.py", "requirements.txt", "README.md", "Dockerfile", ".gitignore"):
        p = ROOT / name
        if p.exists():
            source.append((name, p))
    for p in sorted((ROOT / "tools").glob("*.py")):
        source.append((f"tools/{p.name}", p))

    push_branch(owner, REPO, DEFAULT_BRANCH, source, "chore: 初始化 那个老吴 对战小游戏")
    print(f"✅ 已推送 {DEFAULT_BRANCH} 分支（{len(source)} 个文件）")

    # 3) gh-pages 分支：站点（去掉 public/ 前缀，根目录直接放 index.html）
    site = [(rel, p) for rel, p in public]
    push_branch(owner, REPO, PAGES_BRANCH, site, "deploy: 发布静态站点")
    print(f"✅ 已推送 {PAGES_BRANCH} 分支（{len(site)} 个文件）")

    print("\n" + "=" * 52)
    print("下一步（手动一次即可）：")
    print(f"  打开 https://github.com/{owner}/{REPO}/settings/pages")
    print("  Source 选 『Deploy from a branch』")
    print(f"  Branch 选 『{PAGES_BRANCH}』 目录选 『/(root)』 → Save")
    print(f"\n  部署完成后访问：https://{owner.lower()}.github.io/{REPO}/")
    print("=" * 52)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
