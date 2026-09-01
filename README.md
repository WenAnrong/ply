# ply 服务器面板

## 系统要求

已测试系统：debian13 

需要确保系统里 `python >= 3.10`。

安装脚本自动安装下面的东西

```bash
sudo apt install tmux python3-venv
```

## 开发配置

创建 `.flaskenv` 文件，内容如下：
```bash
FLASK_CONFIG=development  # 使用开发环境配置
FLASK_DEBUG=True          # 启动debug模式
```

然后调试时使用 `flask run` 启动服务.