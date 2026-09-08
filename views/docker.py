"""
Docker 管理模块
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint
from flask import flash
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from flask_login import login_required

docker_bp = Blueprint("docker", __name__)

logger = logging.getLogger(__name__)

# Docker 守护进程配置文件
DOCKER_DAEMON_JSON = "/etc/docker/daemon.json"

# ---- 镜像更新检测（只读，不改动任何运行状态）----
_REMOTE_INSPECT_TIMEOUT = (
    10  # 秒：单次远端 registry digest 查询超时，registry 不可达时不能拖慢页面
)
_UPDATE_MAX_WORKERS = (
    5  # 并行查询远端 digest 的线程数（同引用会去重，通常只需查 1~3 个）
)

# ---- Docker 安装状态 / 容器列表 / 镜像列表 短 TTL 缓存 ----
# 这些页面每次刷新都会跑 sudo docker ps / inspect / compose 探测等子进程，
# 用 3 秒缓存避免短时间内重复刷新时白跑；变更操作后调用 _docker_cache_invalidate() 失效。
_DOCKER_CACHE_TTL = 3.0  # 秒
_DOCKER_CACHE_LOCK = threading.Lock()
_DOCKER_STATE_CACHE = {"ts": 0.0, "value": None}
_DOCKER_CONTAINERS_CACHE = {"ts": 0.0, "value": None}
_DOCKER_IMAGES_CACHE = {"ts": 0.0, "value": None}


def _cache_get(cache):
    """命中且未过期的缓存返回其值，否则返回 None。"""
    now = time.time()
    with _DOCKER_CACHE_LOCK:
        if cache["value"] is not None and now - cache["ts"] < _DOCKER_CACHE_TTL:
            return cache["value"]
    return None


def _cache_set(cache, value):
    """写入缓存并刷新时间戳。"""
    with _DOCKER_CACHE_LOCK:
        cache["value"] = value
        cache["ts"] = time.time()


def _docker_cache_invalidate():
    """清空全部缓存：容器/镜像/daemon 状态发生变更后调用，确保下个页面取到最新。"""
    with _DOCKER_CACHE_LOCK:
        for cache in (
            _DOCKER_STATE_CACHE,
            _DOCKER_CONTAINERS_CACHE,
            _DOCKER_IMAGES_CACHE,
        ):
            cache["value"] = None
            cache["ts"] = 0.0


def _read_daemon_json():
    """读取 daemon.json，返回 (展示文本, 文件是否存在, 错误信息)。

    文件可能是 root 0600（应用用户不可直接读），统一经 sudo cat 读取。
    """
    if not os.path.exists(DOCKER_DAEMON_JSON):
        return "{}", False, None
    try:
        # 经 sudo 读取，避免权限不足时误报
        r = _sudo(["cat", DOCKER_DAEMON_JSON])
        if r.returncode != 0:
            return "{}", True, f"读取失败：{r.stderr.strip() or '权限不足'}"
        raw = r.stdout
        # 解析后重新格式化，方便编辑
        parsed = json.loads(raw)
        return json.dumps(parsed, ensure_ascii=False, indent=2), True, None
    except json.JSONDecodeError as e:
        # 文件存在但不是合法 JSON：仍返回原文，让用户修复
        return raw, True, f"文件不是合法 JSON：{e}"
    except Exception as e:
        return "{}", True, f"读取失败：{e}"


def _sudo(args, stdin=None, cwd=None, timeout=None):
    """以服务用户通过免密 sudo 执行系统命令，避免卡在密码输入。"""
    try:
        return subprocess.run(
            ["sudo", "-n", *args],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # 超时按失败处理：registry 不可达/网络卡住时绝不能挂住请求线程
        return subprocess.CompletedProcess(
            args, returncode=124, stdout="", stderr="命令执行超时"
        )


# Docker 未安装时给出的友好提示，引导用户查阅 README
_DOCKER_NOT_INSTALLED_MSG = (
    "Docker 未安装。请先安装 Docker 与 Docker Compose v2 插件"
    "（docker compose），具体步骤见项目 README.md 的「Docker 安装」章节。"
)
_DOCKER_COMPOSE_MISSING_MSG = (
    "已检测到 Docker，但缺少 Docker Compose v2 插件（docker compose）。"
    "请参考项目 README.md 的「Docker 安装」章节安装 Compose v2 插件。"
)


def _docker_install_state():
    """检测 Docker 与 Docker Compose 是否安装。

    返回 (docker_ok, compose_ok, message)。
    任一缺失时 message 为友好的引导提示，否则为 None。
    """
    docker_check = _sudo(["docker", "--version"])
    if docker_check.returncode != 0:
        return False, False, _DOCKER_NOT_INSTALLED_MSG
    compose_check = _sudo(["docker", "compose", "version"])
    if compose_check.returncode != 0:
        return True, False, _DOCKER_COMPOSE_MISSING_MSG
    return True, True, None


def _friendly_docker_error(stderr):
    """把 docker 命令的 stderr 转成用户友好的提示。"""
    err = (stderr or "").strip()
    if not err:
        return "无法读取 Docker 信息（请确认 Docker 是否可用）"
    if "command not found" in err or "No such file or directory" in err:
        return _DOCKER_NOT_INSTALLED_MSG
    return err


def _list_images():
    """列出本地 Docker 镜像，返回 (列表, 错误信息)。"""
    r = _sudo(
        [
            "docker",
            "images",
            "--format",
            "{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}",
        ]
    )
    if r.returncode != 0:
        return [], _friendly_docker_error(r.stderr)
    images = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            images.append({"name": parts[0], "id": parts[1], "size": parts[2]})
    return images, None


def _container_mem_stats():
    """采样运行中容器的实时内存占用。

    返回 {容器名: {"mem": 已用内存字符串, "perc": 占比字符串}}。
    docker stats 只统计运行中的容器；失败或无运行容器时返回 {}。
    """
    r = _sudo(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}",
        ]
    )
    result = {}
    if r.returncode != 0:
        return result
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        # MemUsage 形如 "357.4MiB / 7.817GiB"，卡片只展示已用部分
        used = parts[1].split(" / ")[0].strip()
        perc = parts[2].strip() if len(parts) >= 3 else ""
        result[parts[0]] = {"mem": used, "perc": perc}
    return result


def _list_containers():
    """列出容器，区分 compose 项目与普通容器。

    返回 (compose_projects, normal_containers, 错误信息)。
    每个容器含 id/name/image/status/ports/mem/mem_perc；compose 容器额外带 project/dir/config_files。
    内存字段仅在容器运行时有值（未运行容器为空字符串）。
    """
    r = _sudo(
        [
            "docker",
            "ps",
            "-a",
            "--format",
            "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}",
        ]
    )
    if r.returncode != 0:
        return (
            [],
            [],
            _friendly_docker_error(r.stderr),
        )

    rows = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 5:
            rows.append(
                {
                    "id": parts[0],
                    "name": parts[1],
                    "image": parts[2],
                    "status": parts[3],
                    "ports": parts[4],
                }
            )

    if not rows:
        return [], [], None

    # 读取每个容器的 compose 标签（以容器名匹配，避免长短 ID 不一致）
    labels = {}
    ids = [row["id"] for row in rows]
    inspect = _sudo(
        [
            "docker",
            "inspect",
            *ids,
            "--format",
            '{{.Name}}\t{{index .Config.Labels "com.docker.compose.project"}}\t{{index .Config.Labels "com.docker.compose.project.working_dir"}}\t{{index .Config.Labels "com.docker.compose.project.config_files"}}',
        ]
    )
    if inspect.returncode == 0:
        for line in inspect.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                labels[parts[0].lstrip("/")] = {
                    "project": parts[1],
                    "dir": parts[2],
                    "config_files": parts[3],
                }

    # 采样运行中容器的实时内存占用，合并进每个容器行（未运行容器留空）
    mem_map = _container_mem_stats()
    for row in rows:
        info = mem_map.get(row["name"]) or {}
        row["mem"] = info.get("mem", "")
        row["mem_perc"] = info.get("perc", "")

    projects = {}
    normal = []
    for row in rows:
        meta = labels.get(row["name"])
        if meta and meta["project"]:
            proj = meta["project"]
            projects.setdefault(
                proj,
                {
                    "name": proj,
                    "dir": meta["dir"],
                    "config_files": meta["config_files"],
                    "containers": [],
                },
            )
            projects[proj]["containers"].append(row)
        else:
            normal.append(row)

    for p in projects.values():
        p["running"] = sum(1 for c in p["containers"] if c["status"].startswith("Up"))
        p["total"] = len(p["containers"])
    return list(projects.values()), normal, None


def _cached_install_state():
    """_docker_install_state() 的 TTL 缓存版本（仅供读取展示）。"""
    cached = _cache_get(_DOCKER_STATE_CACHE)
    if cached is not None:
        return cached
    result = _docker_install_state()
    _cache_set(_DOCKER_STATE_CACHE, result)
    return result


def _cached_list_containers():
    """_list_containers() 的 TTL 缓存版本（仅供读取展示）。"""
    cached = _cache_get(_DOCKER_CONTAINERS_CACHE)
    if cached is not None:
        return cached
    result = _list_containers()
    _cache_set(_DOCKER_CONTAINERS_CACHE, result)
    return result


def _cached_list_images():
    """_list_images() 的 TTL 缓存版本（仅供读取展示）。"""
    cached = _cache_get(_DOCKER_IMAGES_CACHE)
    if cached is not None:
        return cached
    result = _list_images()
    _cache_set(_DOCKER_IMAGES_CACHE, result)
    return result


def _compose_args(project, action):
    """构造针对某个 compose 项目的 docker compose 命令参数。

    返回 (args, cwd)。优先用 config_files（可多个，逗号分隔）；缺失时退回使用 working_dir。
    """
    args = ["docker", "compose"]
    raw = (project.get("config_files") or "").strip()
    files = [f.strip() for f in re.split(r"[,]+", raw) if f.strip()]
    cwd = None
    if files:
        for f in files:
            args += ["-f", f]
    elif project.get("dir"):
        cwd = project["dir"]
    args.append(action)
    if action == "up":
        args.append("-d")
    return args, cwd


def _find_compose_project(name):
    """按项目名查找 compose 项目，找不到返回 None。"""
    projects, _, _ = _list_containers()
    for p in projects:
        if p["name"] == name:
            return p
    return None


# ============ 镜像更新检测（只读） ============


def _plausible_ref(image_ref):
    """把容器启动时的镜像引用整理成可查询 registry 的 repo:tag；无法定位返回 None。

    应传 .Config.Image（容器创建时的引用，pull 新镜像不会改写它）：一旦用户
    pull 了新版本，旧镜像失去 tag 变成悬空，docker ps 的镜像列会变成
    <none>/sha256，那种值不能当远端候选。形如 sha256:.../<none>（悬空）或
    含 @sha256:（固定 digest 启动）的引用没有可自动判断的 tag 更新通道，
    返回 None 交给调用方按「本地/无远端源」处理。
    """
    ref = (image_ref or "").strip()
    if not ref or "<none>" in ref or "@sha256:" in ref:
        return None
    if re.match(r"^sha256:[0-9a-f]{64}$", ref):
        return None
    # 没带 tag 的短引用补 :latest（docker run 后 config 一般已是 repo:latest，这里兜底）
    if ":" not in ref:
        ref += ":latest"
    return ref


def _container_image_info(container_ids):
    """批量取容器镜像信息，返回 {容器名: {"id": 实际运行镜像ID, "config_image": 启动时引用}}。

    .Image         = 容器实际运行的镜像 ID：pull 同名新镜像不会改它，作本地 digest 基准
                     （避免「本地 tag 已被 pull 指向新版而容器仍跑旧版」时误判成最新）；
    .Config.Image  = 容器创建时的镜像引用（如 nginx:latest）：同样不受 pull 影响，
                     作远端 registry 查询的候选引用。
    """
    res = {}
    ids = list(dict.fromkeys(container_ids))
    if not ids:
        return res
    r = _sudo(
        [
            "docker",
            "inspect",
            *ids,
            "--format",
            "{{.Name}}\t{{.Image}}\t{{.Config.Image}}",
        ]
    )
    if r.returncode != 0:
        return res
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            res[parts[0].lstrip("/")] = {"id": parts[1], "config_image": parts[2]}
    return res


def _image_repo_digests(image_ids):
    """批量查镜像的 RepoDigests，返回 {镜像ID: [sha256,...]}。

    RepoDigest 是该镜像从 registry 拉取时记录的固定摘要（仓库级 digest），
    与「容器当前实际跑的版本」一一对应，最适合当本地基准。
    镜像被删/部分丢失时逐个重试，查不到的跳过。
    """
    out = {}
    unique = list(dict.fromkeys(image_ids))
    if not unique:
        return out

    fmt = "{{.Id}}\t{{range .RepoDigests}}{{.}},{{end}}"

    def _merge(stdout):
        for line in stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or not parts[0].startswith("sha256:"):
                continue
            digests = []
            for item in (parts[1] or "").split(","):
                item = item.strip()
                if "@sha256:" in item:
                    digests.append(item.rsplit("@sha256:", 1)[1])
            if digests:
                out[parts[0]] = digests

    r = _sudo(["docker", "image", "inspect", *unique, "--format", fmt])
    if r.returncode == 0:
        _merge(r.stdout)
        return out
    # 个别镜像可能已无法 inspect（被删而容器残留），逐个尝试尽量取回其余结果
    for iid in unique:
        rr = _sudo(["docker", "image", "inspect", iid, "--format", fmt])
        if rr.returncode == 0 and rr.stdout.strip():
            _merge(rr.stdout)
    return out


def _image_local_ids(refs):
    """批量查镜像引用当前在本地指向的镜像 ID，返回 {ref: 镜像ID}。

    用于「容器实际跑的镜像 ID != 本地 tag 当前指向的镜像 ID」这一判据：
    用户 pull 新镜像后，tag 会指向新 ID，而容器仍跑旧 ID —— 即使旧镜像的
    RepoDigest 已被清空，也能据此判定「可更新」。ref 本地不存在/查询失败时跳过。
    """
    out = {}
    unique = list(dict.fromkeys([r for r in refs if r]))
    if not unique:
        return out

    fmt = "{{.Id}}"
    r = _sudo(["docker", "image", "inspect", *unique, "--format", fmt])
    if r.returncode == 0:
        lines = r.stdout.splitlines()
        for token, iid in zip(unique, lines):
            v = iid.strip()
            if v:
                out[token] = v
        return out
    for t in unique:
        rr = _sudo(["docker", "image", "inspect", t, "--format", fmt])
        if rr.returncode == 0 and rr.stdout.strip():
            out[t] = rr.stdout.strip()
    return out


def _first_sha_digest(obj):
    """在 manifest inspect 输出里递归找第一个 sha256 digest（兼容不同 Docker 版本结构）。

    docker manifest inspect --verbose 的顶层结构在不同 Docker 版本/镜像类型下有差异
    （有的在 Descriptor.digest，有的在别处）。这里宽松递归，避免解析失败导致恒判
    「检测失败」。
    """
    if isinstance(obj, dict):
        d = obj.get("digest")
        if isinstance(d, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", d):
            return d
        for v in obj.values():
            hit = _first_sha_digest(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _first_sha_digest(v)
            if hit:
                return hit
    return None


def _remote_digest(ref):
    """查询 ref 在远端 registry 的仓库 digest（sha256:...）。

    返回 (digest 或 None, 错误信息)。只读、不下载镜像、走 docker 现成的登录态。
    优先 buildx imagetools inspect（直接给索引 digest）；不可用时用
    docker manifest inspect --verbose 兜底（递归找 digest，兼容各版本结构）。
    """
    r = _sudo(
        ["docker", "buildx", "imagetools", "inspect", ref],
        timeout=_REMOTE_INSPECT_TIMEOUT,
    )
    if r.returncode == 0:
        m = re.search(r"Digest:\s*(sha256:[0-9a-f]{64})", r.stdout)
        if m:
            return m.group(1), None
    r2 = _sudo(
        ["docker", "manifest", "inspect", "--verbose", ref],
        timeout=_REMOTE_INSPECT_TIMEOUT,
    )
    if r2.returncode == 0 and r2.stdout.strip():
        try:
            data = json.loads(r2.stdout)
            digest = _first_sha_digest(data)
            if digest:
                return digest, None
        except (ValueError, TypeError):
            pass
    err = (r.stderr or r2.stderr or "").strip()
    return None, err or "无法查询远端 registry"


def _detect_container_updates(projects, normal_containers):
    """为每个容器行填充镜像更新检测结果（只读，不改任何容器）。

    compose 项目容器与普通容器共用同一套逻辑——本质上都是「容器实际跑的镜像
    与 registry 上该 tag 的当前 digest 是否一致」：
      - digest 一致              -> update_state=latest  update_label=最新
      - digest 不一致            -> update_state=update  update_label=可更新
      - 本地构建 / 无远端源       -> update_state=local   （不显示徽标）
      - 远端查询失败              -> update_state=unknown （不显示徽标）
    远端查询按唯一引用去重并并发执行，避免多个容器共用同一镜像时重复打 registry。
    """
    rows = []
    for p in projects:
        rows.extend(p["containers"])
    rows.extend(normal_containers)
    if not rows:
        return

    # 1) 每个容器：实际运行的镜像 ID + 创建时的镜像引用
    info_map = _container_image_info([r["id"] for r in rows])
    # 2) 这些镜像的本地 RepoDigest（作为「当前跑的版本」基准）
    repo_digests = _image_repo_digests(
        [v["id"] for v in info_map.values() if v.get("id")]
    )

    # 3) 候选引用收集（远端查询 + 本地 tag 指向 ID 都需要，按引用去重）
    memo = {}  # ref -> digest or None
    refs = []
    for r in rows:
        info = info_map.get(r["name"]) or {}
        iid = info.get("id")
        # 远端候选用 .Config.Image（启动时引用，pull 不影响）；docker ps 的镜像列
        # 在 pull 后旧镜像会变悬空，不能作为候选。
        ref = _plausible_ref(info.get("config_image"))
        if not ref:
            # 引用无法定位远端（悬空/sha256/digest 启动）：无「更新」概念
            r["update_state"] = "local"
            r["update_label"] = ""
            continue
        r["_img_id"] = iid or ""
        r["_ref"] = ref
        r["config_image"] = info.get("config_image") or ""  # 供模板显示真实 tag
        if ref not in memo:
            memo[ref] = None
            refs.append(ref)

    # 本地 tag 当前指向的镜像 ID（pull 后即指向新镜像）
    local_ids = _image_local_ids(refs)

    if refs:
        with ThreadPoolExecutor(max_workers=_UPDATE_MAX_WORKERS) as ex:
            results = ex.map(_remote_digest, refs)
        for ref, (digest, _err) in zip(refs, results):
            memo[ref] = digest

    # 4) 回填判定结果（优先级从强到弱）
    for r in rows:
        ref = r.pop("_ref", None)
        iid = r.pop("_img_id", None)
        if not ref:
            continue
        local_rd = repo_digests.get(iid) or []
        local_img = local_ids.get(ref)
        remote = memo.get(ref)

        # ① 容器实际跑的镜像 ID != 本地 tag 当前指向的镜像 ID
        #    -> 容器没跑当前 tag 的镜像（最常见：pull 后未重建），必「可更新」
        #    （不依赖 RepoDigest/网络，即使旧镜像 RepoDigest 已空也能判定）
        if local_img and iid and iid != local_img:
            r["update_state"] = "update"
            r["update_label"] = "可更新"
            continue

        # ② 容器镜像有 RepoDigest 且远端查到 -> digest 精确对比
        if local_rd and remote:
            # 归一化：remote 形如 "sha256:xxx"（带前缀），local 为不带前缀的 hex
            local_sha = (local_rd[0] or "").split(":", 1)[-1].lower()
            remote_sha = (remote or "").split(":", 1)[-1].lower()
            if local_sha and local_sha == remote_sha:
                r["update_state"] = "latest"
                r["update_label"] = "最新"
            else:
                r["update_state"] = "update"
                r["update_label"] = "可更新"
            continue

        # ③ 容器 == 本地 tag 镜像 但无 registry 摘要 -> 本地构建，无更新概念
        if local_img and not local_rd:
            r["update_state"] = "local"
            r["update_label"] = ""
            continue

        # ④ 其余：远端查询失败或信息不足 -> 明确提示，避免误以为功能没生效
        r["update_state"] = "unknown"
        r["update_label"] = "检测失败"


@docker_bp.route("/docker")
@login_required
def index():
    return render_template("docker.html", active_tab="services")


@docker_bp.route("/docker/services")
@login_required
def services():
    docker_ok, compose_ok, install_msg = _cached_install_state()
    # Docker 未安装时跳过列表（docker ps/inspect 会白跑 sudo 子进程）
    projects, normal, err = [], [], None
    if docker_ok:
        projects, normal, err = _cached_list_containers()
        # 只读「镜像更新」检测（进入服务页时执行）；失败只告警，绝不影响列表展示
        if not err:
            try:
                _detect_container_updates(projects, normal)
            except Exception as e:  # noqa: BLE001 —— 检测是加分项，不能拖垮页面
                logger.warning("镜像更新检测失败：%s", e)
    if install_msg:
        err = install_msg
    return render_template(
        "docker.html",
        active_tab="services",
        compose_projects=projects,
        normal_containers=normal,
        docker_services_error=err,
        docker_installed=docker_ok,
        docker_compose_installed=compose_ok,
    )


@docker_bp.route("/docker/services/mem")
@login_required
def services_mem():
    """返回运行中容器的实时内存值 partial，供服务页定时轮询（仿仪表盘）。

    不读容器列表缓存，直接采样 docker stats，保证每次轮询都拿到新数值。
    返回的 partial 里每个运行容器一个 <span data-mem="容器名">，由前端 JS
    定点更新可见卡片上的内存文本，避免整卡重建。
    """
    mem_values = {}
    docker_ok, _, _ = _cached_install_state()
    if docker_ok:
        mem_values = _container_mem_stats()
    return render_template("partials/docker_mem_values.html", mem_values=mem_values)


@docker_bp.route("/docker/services/start", methods=["POST"])
@login_required
def container_start():
    ref = request.form.get("ref", "").strip()
    if not ref:
        flash("未选择容器", "error")
        return redirect(url_for("docker.services"))
    r = _sudo(["docker", "start", ref])
    if r.returncode != 0:
        flash("启动失败：" + _friendly_docker_error(r.stderr), "error")
    else:
        flash(f"容器 {ref} 已启动", "success")
    _docker_cache_invalidate()
    return redirect(url_for("docker.services"))


@docker_bp.route("/docker/services/stop", methods=["POST"])
@login_required
def container_stop():
    ref = request.form.get("ref", "").strip()
    if not ref:
        flash("未选择容器", "error")
        return redirect(url_for("docker.services"))
    r = _sudo(["docker", "stop", ref])
    if r.returncode != 0:
        flash("停止失败：" + _friendly_docker_error(r.stderr), "error")
    else:
        flash(f"容器 {ref} 已停止", "success")
    _docker_cache_invalidate()
    return redirect(url_for("docker.services"))


@docker_bp.route("/docker/services/compose/up", methods=["POST"])
@login_required
def compose_up():
    name = request.form.get("project", "").strip()
    project = _find_compose_project(name)
    if not project:
        flash("未找到项目", "error")
        return redirect(url_for("docker.services"))
    args, cwd = _compose_args(project, "up")
    r = _sudo(args, cwd=cwd)
    if r.returncode != 0:
        flash("启动失败：" + _friendly_docker_error(r.stderr), "error")
    else:
        flash(f"项目 {name} 已启动", "success")
    _docker_cache_invalidate()
    return redirect(url_for("docker.services"))


@docker_bp.route("/docker/services/compose/down", methods=["POST"])
@login_required
def compose_down():
    name = request.form.get("project", "").strip()
    project = _find_compose_project(name)
    if not project:
        flash("未找到项目", "error")
        return redirect(url_for("docker.services"))
    args, cwd = _compose_args(project, "down")
    r = _sudo(args, cwd=cwd)
    if r.returncode != 0:
        flash("停止失败：" + _friendly_docker_error(r.stderr), "error")
    else:
        flash(f"项目 {name} 已停止", "success")
    _docker_cache_invalidate()
    return redirect(url_for("docker.services"))


@docker_bp.route("/docker/services/compose/stop", methods=["POST"])
@login_required
def compose_stop():
    name = request.form.get("project", "").strip()
    project = _find_compose_project(name)
    if not project:
        flash("未找到项目", "error")
        return redirect(url_for("docker.services"))
    args, cwd = _compose_args(project, "stop")
    r = _sudo(args, cwd=cwd)
    if r.returncode != 0:
        flash("停止失败：" + _friendly_docker_error(r.stderr), "error")
    else:
        flash(f"项目 {name} 已停止", "success")
    _docker_cache_invalidate()
    return redirect(url_for("docker.services"))


@docker_bp.route("/docker/services/delete", methods=["POST"])
@login_required
def container_delete():
    ref = request.form.get("ref", "").strip()
    if not ref:
        flash("未选择容器", "error")
        return redirect(url_for("docker.services"))
    r = _sudo(["docker", "rm", ref])
    if r.returncode != 0:
        flash("删除失败：" + _friendly_docker_error(r.stderr), "error")
    else:
        flash(f"容器 {ref} 已删除", "success")
    _docker_cache_invalidate()
    return redirect(url_for("docker.services"))


@docker_bp.route("/docker/images")
@login_required
def images():
    docker_ok, compose_ok, install_msg = _cached_install_state()
    # Docker 未安装时跳过镜像列表，避免白跑 sudo 子进程
    image_list, err = [], None
    if docker_ok:
        image_list, err = _cached_list_images()
    if install_msg:
        err = install_msg
    return render_template(
        "docker.html",
        active_tab="images",
        docker_images=image_list,
        docker_images_error=err,
        docker_installed=docker_ok,
        docker_compose_installed=compose_ok,
    )


@docker_bp.route("/docker/settings")
@login_required
def settings():
    docker_ok, compose_ok, _ = _cached_install_state()
    content, exists, err = _read_daemon_json()
    return render_template(
        "docker.html",
        active_tab="settings",
        docker_config=content,
        docker_config_exists=exists,
        docker_config_error=err,
        docker_config_path=DOCKER_DAEMON_JSON,
        docker_installed=docker_ok,
        docker_compose_installed=compose_ok,
    )


@docker_bp.route("/docker/settings/config", methods=["POST"])
@login_required
def save_docker_config():
    content = request.form.get("content", "")

    # 备份原文件（文件不存在时跳过）
    _sudo(["cp", DOCKER_DAEMON_JSON, DOCKER_DAEMON_JSON + ".bak"])

    # 写入新配置（通过 stdin 交给 sudo tee，以 root 写）
    payload = content + "\n"
    write = _sudo(["tee", DOCKER_DAEMON_JSON], stdin=payload)
    if write.returncode != 0:
        flash("写入失败：" + (write.stderr or "权限不足"), "error")
        return redirect(url_for("docker.settings"))

    # 缓冲/确认：先把文件刷入磁盘、稍等片刻，再读回校验，
    # 避免 dockerd 在重启瞬间读到空/被截断的 daemon.json 而启动失败。
    _sudo(["sync"])
    time.sleep(0.5)
    try:
        # 用 sudo cat 读回校验（文件可能为 0600 root，应用用户直接 open() 会读失败）
        check = _sudo(["cat", DOCKER_DAEMON_JSON])
        if check.returncode != 0:
            raise ValueError(check.stderr.strip() or "无法读取已写入的文件")
        written = check.stdout
        if not written.strip():
            raise ValueError("写入内容为空")
        json.loads(written)
    except Exception as e:
        flash("写入内容校验失败，为避免 Docker 无法启动，未重启：" + str(e), "error")
        return redirect(url_for("docker.settings"))

    # 先清除可能已耗尽的启动限流，再重启，确保能真正启动
    _sudo(["systemctl", "reset-failed", "docker"])
    restart = _sudo(["systemctl", "restart", "docker"])
    if restart.returncode != 0:
        flash("配置已保存，但 Docker 重启失败：" + (restart.stderr or ""), "error")
    else:
        flash("Docker 配置已更新并重启", "success")

    # daemon 可能已重启，容器/镜像状态已变，清掉缓存
    _docker_cache_invalidate()
    return redirect(url_for("docker.settings"))


@docker_bp.route("/docker/images/delete", methods=["POST"])
@login_required
def delete_docker_image():
    ref = request.form.get("ref", "").strip()

    if not ref:
        flash("未选择镜像", "error")
        return redirect(url_for("docker.images"))

    r = _sudo(["docker", "rmi", ref])
    if r.returncode != 0:
        flash("删除失败：" + _friendly_docker_error(r.stderr), "error")
    else:
        flash(f"镜像 {ref} 已删除", "success")
    _docker_cache_invalidate()
    return redirect(url_for("docker.images"))
