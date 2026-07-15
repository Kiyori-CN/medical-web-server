# medical-web-server

面向医学考试的本地题库平台，提供题库浏览、精确文件刷题、背题、测试、收藏、错题、个人笔记、原题评论和关键词/语义搜题。原始 Excel、Word 文件只作为构建输入；网站运行时使用本地 SQLite、FTS5 和可选的 NumPy 向量矩阵，不依赖第三方登录、远程业务数据库或联网题库接口。

## 已实现能力

- 按考试、学科、系统、章节和年份的学习顺序浏览题库；
- 底部五个主页面不显示重复的红色栏目标题；收藏和错题等二级页面保留单一红色栏目名；
- 点击左上角“医考知库”始终返回题库根目录首页；
- 题库支持“目录 / 分类”两种浏览样式；分类模式的考试与题库类型使用固定宽度、自动换行的多行标签，每组可独立展开或收起，学科使用纵向折叠列表并按需展开章节和最终原始文件；目录与分类模式中的最终题库名称均直接左对齐，不显示“卷”图标；
- 题库顶层目录统一命名为“专硕考研、学硕考研、执业医师、三基考试”，不在目录名中附加考试说明；
- 刷题页左上角回退返回当前文件的题号目录详情，而不是题库列表；
- 题库文件卡片和页面可见来源路径隐藏 `.xlsx` 后缀，卡片同时隐藏重复的 `XLSX` 格式标签，原始路径仍完整保留；
- 保留原文件相对路径、扩展名、原题号和 occurrence 版本；
- A1、A2、A3/A4、B、X 等题型分组题号目录；
- 四种文件刷题模式：
  - 练习模式：选择后手动提交，提交后显示对错与解析；
  - 快刷模式：选对先瞬时显示绿色反馈，再自动进入下一题；选错立即显示红色并停留展开答案解析；
  - 测试模式：选后自动下一题，交卷前服务端不返回对错、答案或实时分数；
  - 背题模式：无需选择和提交，直接展示答案、考点与解析；
- 手机端全屏沉浸式刷题、左右滑动切题、可下拉收起的题号抽屉；刷题顶栏的计时与交卷使用固定列，不随切题动画或题目长度位移；
- 未提交选项灰色、错误选项红色、正确选项绿色的明确状态反馈；
- 下一题始终可用，允许跳过未答题并保留当前草稿；
- 答案、考点还原和标准解析使用浅绿、浅金、浅蓝三套满宽分段配色；
- 夜间模式继续保留三种浅色解析卡片，卡片内标题、答案摘要、考点和解析正文固定使用深色字，不能继承页面的浅色字体；
- “标准答案 / 你的答案”对照与完整的手机题号抽屉关闭逻辑；
- 计时、未答交卷、整卷复盘和刷新恢复；
- 题号目录可确认重置当前账号在当前文件的刷题状态，历史试卷、收藏、错题和评论保持不变；
- 本地账号、管理员/普通用户权限、用户修改密码和会话撤销；“系统管理”只在管理员的“我的 → 常用功能”首位显示；
- 顶栏提供持久化的日间/夜间主题切换与信封消息入口；消息中心用一行四项标签切换系统通知、私信对话、回复与@、收到赞同；
- “我的”页底部提供退出登录；设置页按“账号与安全 → 隐私设置 → 通知设置”排列，密码修改集中在账号与安全，个人资料字段、粉丝/关注公开性和私信许可集中在隐私设置，四类消息的顶部即时提醒可独立开关；本地头像由浏览器裁剪压缩后再由服务端统一转码为 512×512 WebP；
- 错题、收藏、笔记、评论和点赞五个学习入口只在题库顶部出现；每个二级页都有明确返回题库按钮，进入原题后按“原题 → 学习列表 → 题库”逐级返回，刷新后仍保留来源；
- 个人笔记绑定当前账号与具体原题，不进入题库、FTS 或向量检索；评论、笔记和点赞列表支持分批加载；
- “我的”页显示头像、昵称、IP 属地、学校和紧凑的动态/关注/粉丝数据，统计项只显示数字与文字、不显示图标；粉丝与关注进入同一个双标签页面，可按用户名搜索、分批加载并进入任意用户的公开主页；下方常用功能与其他功能统一使用四列图标网格；头像或昵称进入公开个人资料页，本人可编辑头像与资料，其他用户只能关注或发私信；
- 题库采用紧凑的“今日统计 + 五个学习入口”固定顶部区域，更新时间紧邻“今日统计”标题右侧；进入目录模式的子文件夹后仍保持显示；今日统计只展示当前账号的答题数据；AI 页面提供账号隔离的会话、重命名、删除、非流式问答、题库增强检索和来源引用，论坛保留后续扩展路由，移动端底部导航固定为题库、搜题、AI、论坛、我的；
- 收藏可反复切换，收藏与错题沿原路径归档，错题目录可直接删除单题；
- 收藏或错题文件删到最后一题后不会继续显示无效的开始按钮；点赞页可直接取消赞同；
- 答案解析解锁后按“标准解析 → 私有笔记 → 原题评论”显示；评论支持赞同、反对、回复和数量统计，评论作者头像/昵称可进入公开个人资料，管理员可在评论管理中筛选并删除全站评论；
- SQLite FTS5 关键词搜索，以及可关闭的 semantic / hybrid 搜索；
- 搜索模式按“关键词、语义、混合”排列，作为设置面板首行的固定宽度彩色勾选标签；
- 搜题结果可分别控制题干（含选项）、答案、考点还原和标准解析的显示，选择会保存在当前浏览器；开启答案时正确选项同步显示绿色；
- 搜题结果的选项、考点还原和标准解析正文使用黑色字色；关键词模式会将命中词以珊瑚红高亮显示；
- 搜索模式、搜索题型、搜索范围和显示内容统一使用带左侧勾选框的固定宽度标签；只有被勾选的标签使用对应彩色，未勾选标签始终保持白色中性背景；搜索题型、搜索范围和显示内容首项均为默认唯一选中的“全部”标签，选择具体标签后再显示具体勾选状态；
- 四组标签按位置复用同一配色顺序，搜索设置支持一键重置以及整体收起与展开；
- 手机端切换搜索标签只更新筛选状态，不会主动聚焦搜索框或弹出输入法；
- 搜题可多选实际来源题型组合筛选（A1、A2、A3/A4、B、X 等细分标签与普通题型均来自 catalog）；筛选值与结果徽标使用同一题型标签，确保只勾选某一题型时不会混入其他题型；重置默认全选所有题型，不选题型时仍不限制范围；
- 管理员入口统一命名为“系统管理”；账号管理下方提供评论管理。
- 模型管理将嵌入模型和大语言模型分开配置、测试和保存；输入未保存的 API 密钥后可临时查询上游模型列表并直接选择，临时密钥不会写入设置库。
- 管理员可确认后手动构建、续建、增量更新或删除向量索引；后台任务跨页面持续运行并保存进度。
- 语义搜索使用管理员配置的嵌入模型构建 question-level 向量索引，完成校验后原子激活，不引入独立向量数据库。
- 每次进入搜题页都会刷新向量状态，索引激活后无需重新登录即可启用语义与混合标签。
- 题库文件夹、题号目录和刷题会话使用可恢复的 hash 路由；浏览器返回与页面回退按“题目 → 题号目录 → 当前文件夹 → 上级页面”逐级返回，收藏与错题会话回到各自来源。
- 目录与分类浏览方式互相切换时保留现有内容，等目标视图准备完成后一次性替换，不闪现中间加载提示；分类模式切换考试、题库类型或展开学科时保留当前页面滚动位置，不因异步重绘跳回页面顶部。

## 真实题库

当前本地 catalog 已完成构建和校验：

- 2,127 个源文件和精确文件题集；
- 123,564 条原题记录；
- 64,078 道规范题和 67,650 个内容版本；
- 1,443 个病例题组、3,732 个题组成员；
- 4 个保留来源的内容冲突；
- 64,078 条 FTS 记录，SQLite 完整性 `ok`，外键错误 0。

激活构建位于：

```text
knowledge-base/builds/catalog_90e97d0b8fc13933ccb14b32
```

真实原始数据、catalog、向量、账号状态、日志和密钥均被 Git 忽略。

## 架构原则

- `question_id` 是规范题、搜题和向量单位；
- `collection_id` 对应一个具体源文件；
- `occurrence_id` 是原文件展示、顺序、会话和判分单位；
- 练习、收藏和错题始终沿原文件路径组织；
- 向量按规范题构建，不按文件重复嵌入；
- 4 核 4G 服务器不运行独立向量数据库，使用约 250 MiB 的 `float32` mmap 矩阵；
- 关键词检索和刷题不以外部模型可用为前提。

## 仓库结构

```text
app/backend/medical_app/  账号、catalog、搜索、刷题、设置和向量内核
app/backend/main.py       Flask 入口
public/                   响应式单页前端
scripts/                  catalog、应用状态和向量构建工具
tests/                    脱敏 fixture 与自动化测试
docs/                     数据、开发、设置和部署规范
data/                     用户本地原始题库，只读且已忽略
knowledge-base/           catalog 与向量制品，已忽略
state/                    账号、头像、练习、笔记、评论、设置与密钥，已忽略
```

## 本地开发

开发环境会启用 Flask 自动重载。若本地服务是以生产环境变量或外部进程管理器启动，修改后端 Python 文件后需重启进程并刷新页面，确保使用当前源码；发布前端资源时同步更新 `index.html` 中的提交哈希参数，避免浏览器继续使用旧的 JS/CSS。

```powershell
cd D:\10_Project\medical-web-server
git status --short --branch

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

.\.venv\Scripts\python.exe scripts\build_catalog.py --activate
.\.venv\Scripts\python.exe scripts\init_app.py

$env:FLASK_ENV = "development"
$env:SECURE_COOKIES = "false"
.\.venv\Scripts\python.exe app\backend\main.py
```

访问 `http://127.0.0.1:5001/`。全新状态库首次打开时进入一次性管理员初始化页；项目不提供默认管理员密码，也不在源码中硬编码生产账号。

## 语义搜索

默认嵌入模型：

```text
Qwen/Qwen3-Embedding-0.6B
1024 dimensions
```

管理员必须先轮换任何曾在聊天、截图或日志中出现过的密钥，再通过 HTTPS 管理设置页录入。配置和连接测试通过后执行：

```powershell
.\.venv\Scripts\python.exe scripts\build_vectors.py --activate
```

索引单位是一道规范题，嵌入文本只包含题型、题干和有序选项，不包含答案、考点和解析。

## 主要 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | 服务、catalog、账号与向量状态 |
| `GET` | `/api/stats` | 题库统计 |
| `POST` | `/api/search` | keyword / semantic / hybrid 搜题，支持 `search_fields` 字段范围与 `question_types` 实际题型数组过滤 |
| `GET` | `/api/library` | 原目录浏览 |
| `GET` | `/api/library/tree` | 按指定父目录返回医学顺序的只读子树，用于分类折叠导航 |
| `GET` | `/api/practice/collections/<id>/outline` | 文件题号与进度 |
| `POST` | `/api/practice/collections/<id>/progress/reset` | 重置当前账号在当前文件的刷题状态 |
| `POST` | `/api/practice/sessions` | 创建四种模式的文件会话 |
| `GET` | `/api/practice/sessions/<id>` | 恢复会话 |
| `POST` | `/api/practice/sessions/<id>/answers` | 提交当前答案 |
| `POST` | `/api/practice/sessions/<id>/finish` | 允许未答交卷 |
| `GET` | `/api/study/favorites/library` | 收藏目录 |
| `POST` | `/api/study/favorites/toggle` | 收藏/取消收藏 |
| `GET` | `/api/study/wrong/library` | 错题目录 |
| `DELETE` | `/api/study/<kind>/<item_key>` | 删除收藏或错题 |
| `GET/POST` | `/api/study/comments` | 查看或发表原题评论 |
| `GET` | `/api/study/comments/mine` | 当前账号发表的评论 |
| `PATCH` | `/api/study/comments/<comment_id>` | 作者编辑自己的评论 |
| `DELETE` | `/api/study/comments/<comment_id>` | 作者或管理员删除评论 |
| `GET/PUT/DELETE` | `/api/study/notes/<occurrence_id>` | 读取、保存或删除当前账号的原题笔记 |
| `GET` | `/api/study/notes` | 当前账号的全部笔记 |
| `GET` | `/api/study/today-summary` | 当前账号今日统计与五个学习入口数量 |
| `POST` | `/api/study/comments/<comment_id>/reaction` | 赞同/反对评论并切换状态 |
| `GET` | `/api/study/comments/likes/mine` | 当前账号赞同过的评论 |
| `GET` | `/api/profiles/<user_id>` | 公开个人资料、动态和关注统计 |
| `GET` | `/api/profiles/<user_id>/connections` | 粉丝/关注列表、用户名搜索与当前账号关注状态 |
| `POST/DELETE` | `/api/profiles/<user_id>/follow` | 关注或取消关注用户 |
| `GET/POST` | `/api/messages` | 消息分类读取与私信发送 |
| `GET` | `/api/messages/alerts` | 读取新消息顶部提醒所需的轻量事件列表 |
| `PUT` | `/api/auth/profile` | 保存用户名、头像和个人资料 |
| `PUT` | `/api/auth/preferences` | 保存隐私与四类消息提醒偏好 |
| `POST` | `/api/auth/avatar` | 上传已裁剪头像并安全重编码 |
| `GET` | `/api/admin/comments` | 管理员查看和筛选全站评论 |
| `POST` | `/api/auth/password` | 登录用户修改密码并撤销既有会话 |
| `GET` | `/api/settings` | 无秘密的公共设置 |
| `GET/PUT` | `/api/admin/settings` | 管理员设置 |
| `POST` | `/api/admin/settings/models` | 使用保存或临时密钥查询上游模型 |
| `GET/POST/DELETE` | `/api/admin/vectors` | 查看状态、构建/续建或删除向量索引 |
| `GET/POST` | `/api/ai/conversations` | 当前账号的 AI 会话列表与新建 |
| `PATCH/DELETE` | `/api/ai/conversations/<id>` | 重命名或删除当前账号会话 |
| `GET/POST` | `/api/ai/conversations/<id>/messages` | 读取消息或发送题库增强问答 |
| `GET/PATCH` | `/api/admin/users` | 管理角色、状态与密码 |

所有写请求使用会话 Cookie 与 CSRF 校验。管理接口要求当前登录账号具有管理员角色；任何设置读取或错误响应都不得返回明文密钥。

AI 会话和消息仅存储在 `state/app.sqlite3`，每次回答最多检索 6 条题库材料并返回来源路径、题目 ID 和 occurrence 引用。LLM 使用管理员在加密设置库中保存的配置，以稳定的非流式请求执行；未配置或上游失败时返回明确的 `ai_unavailable`，不会把密钥、供应商原始错误或私有笔记写入响应。AI 页面支持全题库检索，刷题答案解析解锁后可直接带当前 occurrence 和 collection 上下文发起“AI 解析这道题”。

## 验证

```powershell
git diff --check
python -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\main.py
python -m compileall -q app\backend\medical_app scripts
node --check public\app.js
python -m pytest
```

前端改动还必须验证 `390×844` 和 `1440×900`，并覆盖四种模式、测试模式答案边界、未答下一题、左右滑动、抽屉下拉关闭、题库进度重置、评论发表/删除、收藏切换、错题删除、刷新恢复和未答交卷。

## 发布链路

```text
本地 Windows main
  -> 自动化测试与浏览器验收
  -> GitHub main 精确提交
  -> /srv/projects/medical-web-server/repo 部署镜像
  -> projectsctl update medical-web-server <artifact.tar.gz>
  -> 生产健康与公网验收
```

- GitHub：`https://github.com/Kiyori-CN/medical-web-server.git`
- 生产站点：`https://medical.newapi.com.cn/`
- 服务：`medical-exam.service`
- 服务停止超时：`TimeoutStopSec=45s`（systemd drop-in），确保 Gunicorn 优雅退出；
- Nginx 静态层：公网首页与资源统一返回 HSTS、nosniff、DENY、Referrer-Policy 和 CSP；
- 端口：`8080`
- 运行目录：`/srv/projects/medical-web-server/app/backend`
- 题库目录：`/srv/projects/medical-web-server/knowledge-base`
- 状态目录：`/srv/projects/medical-web-server/state`

## 文档

- [本地开发](docs/local-development.md)
- [数据与索引架构](docs/data-architecture.md)
- [设置与语义搜索](docs/settings-and-semantic-search.md)
- [发布与部署](docs/deployment.md)
- [项目固定事实](PROJECT.md)
- [Agent 开发规范](AGENTS.md)
