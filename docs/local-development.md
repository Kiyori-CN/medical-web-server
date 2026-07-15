# 本地开发

## 权威流程

`D:\10_Project\medical-web-server` 是唯一开发工作区。源码、文档、数据构建、测试和浏览器验收都在本地完成；GitHub `main` 是唯一发布源；服务器只做部署镜像、生产运行和最终验收。

## 开始任务

```powershell
cd D:\10_Project\medical-web-server
git status --short --branch
git fetch origin --prune
git merge --ff-only origin/main
```

存在未提交修改时必须保留并避免覆盖。禁止清理 `data/`、`knowledge-base/` 或 `state/`。

## Python 环境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

本地秘密只放在被忽略的环境变量、设置数据库或 `.env.local`。不得把真实 API 密钥写入 `.env.example`。

## 构建数据

```powershell
.\.venv\Scripts\python.exe scripts\build_catalog.py --activate
.\.venv\Scripts\python.exe scripts\init_app.py
```

`build_catalog.py` 只读扫描 `data/` 并写入新的不可变 build。只有完整性、外键、统计和报告全部通过后，`--activate` 才更新 `knowledge-base/current.json`。

`init_app.py` 初始化：

- `state/app.sqlite3` 的账号、头像、会话、文件刷题状态、收藏、错题、私有笔记和评论表；
- `state/settings.sqlite3` 的网站与模型设置；
- `state/settings.key` 的独立加密 key。

全新状态库不创建默认账号。首次打开网站时通过一次性页面创建首位管理员。

## 启动

```powershell
$env:FLASK_ENV = "development"
$env:SECURE_COOKIES = "false"
.\.venv\Scripts\python.exe app\backend\main.py
```

本地地址：`http://127.0.0.1:5001/`。

## 语义向量

先在管理员设置页保存并测试已轮换的嵌入配置，再执行：

```powershell
.\.venv\Scripts\python.exe scripts\build_vectors.py --activate
```

脚本从加密设置库读取密钥，按批次 flush 矩阵并原子更新进度。改变 catalog、API URL、模型或维度会生成新的 vector build。stdout、manifest 和 Git 中不得出现密钥。

管理员界面的“模型管理”将嵌入模型和大语言模型分开保存。API 密钥输入后会在短暂停顿时调用临时模型发现接口，未点击保存也可以选择上游模型；临时密钥不会写入设置库。头像上传先在浏览器完成正方形裁剪和 WebP 压缩，服务端再统一转码并写入 `user_avatars`。

向量生命周期由 `/api/admin/vectors` 管理：构建请求启动独立 `medical_app.vector_task` 进程，状态写入 `state/vector-operation.json`，构建中断可续建；同配置 catalog 更新时按 `question_id` 增量复用旧向量。删除接口必须收到确认字段，只清除激活指针与向量制品。

## 开发粒度

每项改动应形成完整可验证闭环，例如：

- 一个导入或质量规则；
- 一个 API 与其安全测试；
- 一种刷题模式；
- 一组响应式 UI 改进；
- 一项部署或文档规范。

功能、API、路径、安全边界或部署方式变化时，同步修改 README、PROJECT、AGENTS、对应 docs 和项目技能。

## 自动化验证

```powershell
git status --short --branch
git diff --check
python -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\main.py
python -m compileall -q app\backend\medical_app scripts
node --check public\app.js
python -m pytest
```

测试只使用脱敏小型 fixture。真实题库不能复制到 `tests/`。

## 浏览器验收

使用本地真实 catalog，至少覆盖：

- 顶层、学科、系统和年份固定顺序；
- 2026 文件 165 题及题型目录；
- 练习模式必须提交后才显示解析；
- 快刷选错停留、选对自动前进；
- 测试自动前进且交卷前无答案/对错，交卷后显示成绩；
- 背题直接显示答案解析；
- 未答交卷、刷新恢复、计时；
- 收藏、错题路径与删除；
- 当前文件刷题状态重置、确认弹窗、账号/文件隔离，以及历史试卷、收藏、错题和评论不受影响；
- 未解锁答案解析时不显示或加载评论；解锁后验证标准解析下方的评论加载、空内容和长度校验、发表、作者删除与管理员删除；
- 收藏后再次点击可取消，错题文件目录可直接删除单题；
- 未提交选项灰色，提交后错误红色、正确绿色；
- 未提交也能进入下一题，返回后草稿仍在；
- 管理员和普通用户权限；
- 首位管理员创建后桌面顶栏与手机头像左侧立即显示管理入口，普通用户隐藏；右上角头像直接进入消息中心；“我的”页与公开个人资料分别验证本人编辑、关注、私信、设置、密码修改和退出登录；
- 个人中心验证统一预设头像、本地图片裁剪上传、用户名和密码修改，以及我的评论、我的笔记、我的收藏、我的错题入口；
- 搜题、零结果和收藏搜索结果；
- 搜题结果的题干（含选项）、答案、考点还原、标准解析四个显示开关、浏览器偏好恢复、无答案请求，以及答案开启时的正确选项绿色状态；
- 搜题结果选项、考点还原和标准解析的黑色正文，以及关键词模式下题干、选项、答案、考点和解析的红色命中高亮；
- 搜索标签按分组位置复用配色顺序，以及设置面板整体重置、收起/展开和状态恢复；
- 手机端逐个切换搜索模式、题型、范围和显示内容时不聚焦搜索框、不弹出输入法；
- 搜索范围四项多选、至少一项约束、字段级关键词结果、未选择题干时自动退回关键词模式，以及与显示内容一致的固定宽度彩色标签布局；
- catalog 实际来源题型标签（含 A1/A2/A3-A4/B/X 等细分类型）完整显示，题型/范围/显示内容首项“全部”默认唯一视觉选中，具体标签多选、重置后恢复“全部”、零选择代表全部、浏览器偏好恢复，以及关键词/语义/混合三种模式下与结果徽标一致的精确 `question_types` 过滤；
- 底部五个主页面不显示红色栏目标题；收藏和错题等二级页只显示对应的红色栏目名且无重复黑色标题；
- `390×844` 沉浸式刷题、左右滑动、题号抽屉下拉关闭、自适应底栏及“题号—提交答案—收藏”顺序；连续滑动时计时和交卷按钮保持固定；
- 题库、收藏、错题的多级目录、文件题号页、刷题会话和管理页逐层验证浏览器返回与页面回退，刷新会话后回退仍返回原文件题号目录；
- `1440×900` 题目/题号双栏、管理页和搜题结果；
- 语义索引构建完成后验证 `64,078 × 1024`、float32、单位范数、manifest/catalog 兼容性、semantic/hybrid 代表查询和关键字回退；
- 无新增前端异常或服务端错误。

## 提交与推送

本地 `FLASK_ENV=development` 启动时启用 Flask 自动重载；如果使用生产环境变量或外部进程管理器运行本地服务，修改后端 Python 文件后必须重启该进程，避免继续使用旧内存代码。每次发布前端资源时同步更新 `index.html` 中的提交哈希查询参数，避免浏览器继续使用旧 JS/CSS；已有页面仍需刷新一次。

用户已授权本项目最终发布和部署时，仍应精确暂存源码与文档：

```powershell
git status --short --branch
git diff --check
git diff --stat
git add <明确文件>
git commit -m "<明确说明>"
git push origin main
git rev-parse HEAD
```

不要使用 `git add .` 无差别加入本地文件。确认 `data/`、catalog、state、日志和密钥均未进入提交。

## 服务器边界

所有服务器操作使用 Tencent Cloud wrapper。`/srv/projects/medical-web-server/repo` 只允许快进到已发布的 GitHub 精确提交；生产运行目录不能做常规源码编辑。

部署见 [deployment.md](deployment.md)。
