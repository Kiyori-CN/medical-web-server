# 功能与接口参考

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
- 题号目录只保留“题号 / 题型 / 年份 / 随机”四个排序按钮，不再显示独立方向按钮或解释性标题；排序分段与上方四个学习模式在总宽、列宽、高度、字号、内边距和圆角上完全一致。当前题号、题型、年份按钮右侧只用 `↑` / `↓` 表示正序/倒序，不显示方向文字，随机按钮右侧始终留空。前三项可重复点击，分别在正序/倒序、医学标准/逆标准、新到旧/旧到新之间切换；未知年份始终稳定排在末尾；
- 每次点击“随机”都会立即生成新的完整题号排列并直接显示在目录中；开始刷题时浏览器把当前预览作为 `occurrence_order` 发送，服务端仅在 `order_mode=random` 时接受它，并校验其为当前所选题集无重复、无缺失的完整排列；
- 目录选择会冻结到 `practice_session_questions`。恢复、上一题/下一题、左右滑动和题号抽屉沿同一顺序运行；题卡与抽屉仍显示原 collection 题号，并另示当前顺序进度；点击具体题号从该题在所选顺序中的位置开始，主开始按钮覆盖所选完整顺序；
- 四种文件刷题模式：
  - 练习模式：既可逐题手动提交并立即查看解析，也可保留各题选择，在右上角整卷交卷时一次提交所有未单独提交的答案；
  - 快刷模式：客观题选对先瞬时显示绿色反馈再自动进入下一题，选错停留展开解析；填空/主观题提供手动提交，也可随时结束并保留未答题；
  - 测试模式：客观题选择后自动保存并前进，填空/主观题使用“保存并下一题”；答完最后一题仍保持测试中，必须显式交卷后才解锁对错、答案和分数；
  - 背题模式：无需选择和提交，直接展示答案、考点与解析，并可明确完成本次背题记录；
- 手机端全屏沉浸式刷题、左右滑动切题、可下拉收起的题号抽屉；刷题顶栏的计时与交卷使用固定列，不随切题动画或题目长度位移；
- 未提交选项灰色、错误选项红色、正确选项绿色的明确状态反馈；
- 下一题始终可用，允许跳过未答题并保留当前草稿；
- 答案、考点还原和标准解析使用浅绿、浅金、浅蓝三套满宽分段配色；
- 夜间模式继续保留三种浅色解析卡片，卡片内标题、答案摘要、考点和解析正文固定使用深色字，不能继承页面的浅色字体；
- “标准答案 / 你的答案”对照与完整的手机题号抽屉关闭逻辑；
- 计时、待交卷草稿题号状态、未答交卷、原子整卷提交、整卷复盘和刷新恢复；
- “我的 → 练习记录”按全部、进行中、已完成、已归档分页展示当前账号的历史试卷；记录可无损归档并随时恢复，已归档记录可经不可恢复确认后单个永久删除或全部清空。永久删除只移除练习会话与逐题明细，不影响收藏、错题、笔记和学习进度；归档不改变交卷状态、答案或进度，归档活动会话恢复前不能作答；进行中记录可从首个未答题继续，已完成记录可直接复盘，返回时保留原筛选标签；进行中的测试试卷在归档前后都由服务端隐藏成绩；
- 题号目录页头把“重置练习”放在当前模式的“开始练习/快刷/测试/背题”左侧；确认后只重置当前账号在当前文件的刷题状态，历史试卷、收藏、错题和评论保持不变；
- 本地账号、管理员/普通用户权限、用户修改密码和会话撤销；“系统管理”只在管理员的“我的 → 常用功能”首位显示；
- 顶栏提供持久化的日间/夜间主题切换与信封消息入口；消息中心用一行四项标签切换系统通知、私信对话、回复与@、收到赞同；
- “我的”页底部提供退出登录；设置页按“账号与安全 → 隐私设置 → 通知设置”排列，密码修改集中在账号与安全，个人资料字段、粉丝/关注公开性和私信许可集中在隐私设置，四类消息的顶部即时提醒可独立开关；本地头像由浏览器裁剪压缩后再由服务端统一转码为 512×512 WebP；
- 错题、收藏、笔记、评论和点赞五个学习入口只在题库顶部出现；每个二级页都有明确返回题库按钮，进入原题后按“原题 → 学习列表 → 题库”逐级返回，刷新后仍保留来源；
- 个人笔记绑定当前账号与具体原题，不进入题库、FTS 或向量检索；评论、笔记和点赞列表支持分批加载；
- “我的”页显示头像、昵称、IP 属地、学校和紧凑的动态/关注/粉丝数据，统计项只显示数字与文字、不显示图标；粉丝与关注进入同一个双标签页面，可按用户名搜索、分批加载并进入任意用户的公开主页；下方常用功能与其他功能统一使用四列图标网格；头像或昵称进入公开个人资料页，本人可编辑头像与资料，其他用户只能关注或发私信；
- 题库采用紧凑的“今日统计 + 五个学习入口”固定顶部区域，更新时间紧邻“今日统计”标题右侧；进入目录模式的子文件夹后仍保持显示；今日统计按上海自然日展示当前账号的答题数据、每日题数/时长目标、连续学习天数和近 7 日趋势；
- AI 页面提供账号隔离的会话、新建、重命名、删除、真正的 SSE 流式问答、题库增强检索、来源引用、回答复制、继续追问、停止和原位重新生成。答案解析解锁后，刷题页在原题上打开桌面右侧/手机底部 AI 抽屉，同一 occurrence 恢复最近绑定会话，打开不自动调用模型，关闭后精确保留题号、作答草稿和滚动位置；
- 论坛不显示宣传性页头，使用搜索、分区和“最近活跃 / 最新发布 / 回复最多”组成任务式筛选区；筛选写入 hash，进入详情再返回时完整恢复。论坛同时提供发帖、主题详情、楼中楼回复、`@用户名` 提及、赞同/反对和作者编辑删除权限，系统管理提供管理员论坛筛选、隐藏、恢复、软删除和不可变审计记录；作者头像/昵称进入可关注和私信的公开主页，论坛动态与收到的赞同进入个人主页，回复、提及及新赞同复用现有消息中心；移动端底部导航固定为题库、搜题、AI、论坛、我的；
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
- 管理员入口统一命名为“系统管理”，管理页不重复显示宣传性页头；首屏只加载当前栏目，切换栏目后按需读取并缓存，统一展示加载、刷新、错误恢复和过期状态。桌面导航按运行配置、内容用户、智能能力分组，手机端改为不横向溢出的多列导航；账号、评论和论坛管理均支持服务端筛选/分页、逐项操作、当前页全选和最多 100 项安全批量操作。
- 账号可单个或批量永久删除：单删必须输入完整用户名，批量必须输入带数量的确认短语；服务端在一个事务内清除登录凭据、练习与学习状态、评论/反应、关注/私信/通知、AI 会话、论坛内容和反馈，并兼容清理遗留好友关系。当前登录管理员和最后一位有效管理员始终受保护。题目评论支持单个/批量物理删除；论坛保留隐藏、恢复、软删除，并另设带原因和输入确认的永久删除。
- 网站设置可编辑站点名称、品牌短字、副标题、网页说明、登录页眉题/主标题/介绍和三条能力文案，右侧实时预览并显示字符计数与保存状态；登录背景可上传、替换或恢复默认，服务端只接受安全图片并统一转码。新安装默认文案面向通用题库，不把医学领域写死到框架品牌中。
- 系统管理新增“题库管理”，管理员可把当前激活 catalog 按 occurrence 流式导出为 JSONL 或 CSV；导出保留原文件路径、题号、题型、选项、答案、考点与解析，不包含账号、笔记、评论或其他用户状态。登录用户可从“我的 → 下载数据”导出自己的收藏、错题、笔记、答题进度、练习会话和逐题作答记录。
- 题库管理同时提供隔离的导入预检：管理员可上传 XLSX、UTF-8 CSV、JSONL 或 JSON，自动识别常见字段、手动调整题干/题型/答案/考点/解析/结构化选项映射，并查看有效行、错误行和标准化样例。XLSX 以只读模式遍历多工作表，预览和错误保留“工作表!行号”定位；预检文件只进入 `state/import-staging/`，不会构建或激活 catalog。
- 模型管理将嵌入模型和大语言模型分开配置、测试和保存；管理员只填写基础网址，服务端规范化已有完整端点并自动派生 `/models`、`/embeddings`、`/chat/completions`，页面实时预览实际请求地址；输入未保存的 API 密钥后可临时查询上游模型列表并直接选择，临时密钥不会写入设置库。
- 管理员可确认后手动构建、续建、增量更新或删除向量索引；后台任务跨页面持续运行并保存进度。
- 语义搜索使用管理员配置的嵌入模型构建 question-level 向量索引，完成校验后原子激活，不引入独立向量数据库。
- 每次进入搜题页都会刷新向量状态，索引激活后无需重新登录即可启用语义与混合标签。
- 题库文件夹、题号目录和刷题会话使用可恢复的 hash 路由；浏览器返回与页面回退按“题目 → 题号目录 → 当前文件夹 → 上级页面”逐级返回，收藏与错题会话回到各自来源。
- 目录与分类浏览方式互相切换时保留现有内容，等目标视图准备完成后一次性替换，不闪现中间加载提示；分类模式切换考试、题库类型或展开学科时保留当前页面滚动位置，不因异步重绘跳回页面顶部。

## 已记录的数据构建规模

以下为仓库既有构建记录，不代表样例演示规模或本轮重新实测结果：

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
| `POST` | `/api/practice/sessions` | 创建四种模式的文件会话；`order_mode` 支持 `sequential`、`position_desc`、`type`、`type_desc`、`year_desc`、`year_asc`、`random`；随机预览可携带完整 `occurrence_order`，`start_exact` 区分完整开始与指定原题号开始 |
| `GET` | `/api/practice/sessions/<id>` | 恢复会话 |
| `POST` | `/api/practice/sessions/<id>/answers` | 提交当前答案 |
| `POST` | `/api/practice/sessions/<id>/finish` | 可携带未单独提交的 `answers` 数组原子交卷，并允许保留空白题 |
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
| `GET/PUT` | `/api/study/goals` | 读取或更新当前账号每日题数、学习时长目标 |
| `GET` | `/api/study/sessions` | 当前账号练习记录，支持状态筛选、分页、恢复与复盘摘要 |
| `PUT` | `/api/study/sessions/<id>/archive` | 可逆归档或恢复当前账号练习记录，不改变会话内容与状态 |
| `DELETE` | `/api/study/sessions/<id>` | 永久删除当前账号的一份已归档练习及逐题明细 |
| `DELETE` | `/api/study/sessions?status=archived` | 使用固定确认字段清空当前账号全部已归档练习 |
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
| `DELETE` | `/api/admin/comments/<id>` | 管理员经固定确认永久删除一条评论及关联状态 |
| `DELETE` | `/api/admin/comments/bulk` | 管理员经固定确认原子删除最多 100 条所选评论 |
| `POST` | `/api/auth/password` | 登录用户修改密码并撤销既有会话 |
| `GET` | `/api/site` | 登录前可读的站点外观与注册开关，不包含模型或秘密 |
| `GET` | `/api/site/login-background` | 读取内容哈希缓存的自定义登录背景 WebP |
| `GET` | `/api/settings` | 登录用户的无秘密检索、站点和模型状态 |
| `GET/PUT` | `/api/admin/settings` | 管理员设置 |
| `POST/DELETE` | `/api/admin/settings/login-background` | 管理员上传安全转码背景或恢复默认背景 |
| `POST` | `/api/admin/settings/models` | 使用保存或临时密钥查询上游模型 |
| `GET/POST/DELETE` | `/api/admin/vectors` | 查看状态、构建/续建或删除向量索引 |
| `GET/POST` | `/api/ai/conversations` | 当前账号的 AI 会话列表与新建 |
| `PATCH/DELETE` | `/api/ai/conversations/<id>` | 重命名或删除当前账号会话 |
| `GET/POST` | `/api/ai/conversations/<id>/messages` | 读取消息或发送题库增强问答 |
| `POST` | `/api/ai/conversations/<id>/messages/stream` | 以 SSE 逐段发送题库增强回答，完成后原子保存本轮 user/assistant |
| `POST` | `/api/ai/conversations/<id>/regenerate` | 原位重新生成当前账号会话的最后一条 AI 回答 |
| `POST` | `/api/ai/conversations/<id>/regenerate/stream` | 以 SSE 原位重新生成最后回答，保留 assistant 消息 ID |
| `GET` | `/api/forum/sections` | 论坛分区与主题数量 |
| `GET/POST` | `/api/forum/topics` | 分页搜索主题或发布主题 |
| `GET/PATCH/DELETE` | `/api/forum/topics/<id>` | 主题详情、作者/管理员编辑与删除 |
| `POST` | `/api/forum/topics/<id>/replies` | 发表主题回复，可传 `parent_reply_id` 回复具体楼层并在正文中 `@用户名` |
| `PATCH/DELETE` | `/api/forum/replies/<id>` | 作者/管理员编辑或删除回复 |
| `PUT/DELETE` | `/api/forum/topics/<id>/reaction`、`/api/forum/replies/<id>/reaction` | 设置或取消当前账号对论坛内容的赞同/反对 |
| `GET` | `/api/admin/forum/content` | 管理员分页筛选主题/回复并读取最近审核记录 |
| `PATCH` | `/api/admin/forum/content/<type>/<id>` | 管理员带原因隐藏、恢复或软删除论坛内容 |
| `PATCH` | `/api/admin/forum/content/bulk` | 管理员原子审核最多 100 项内容并逐项写审计记录 |
| `DELETE` | `/api/admin/forum/content/<type>/<id>` | 管理员带原因和固定确认物理清除主题或回复 |
| `DELETE` | `/api/admin/forum/content/bulk` | 管理员原子永久删除最多 100 项论坛内容 |
| `GET` | `/api/admin/catalog/export` | 管理员流式导出当前 catalog，支持 `jsonl`、`csv` 与可选 `collection_id` |
| `GET` | `/api/study/export` | 当前登录用户流式导出自己的学习数据，支持 `jsonl`、`csv` |
| `GET/POST` | `/api/admin/catalog/imports` | 列出导入预检暂存或上传 XLSX/CSV/JSONL/JSON |
| `POST/DELETE` | `/api/admin/catalog/imports/<id>/analyze` / `/api/admin/catalog/imports/<id>` | 按字段映射重新分析或删除暂存 |
| `GET/POST/PATCH` | `/api/admin/users` | 筛选/分页账号并创建或单项管理角色、状态与密码 |
| `PATCH` | `/api/admin/users/bulk` | 批量启用、停用或调整角色，保护当前及最后有效管理员 |
| `DELETE` | `/api/admin/users/<id>` | 输入目标用户名后永久删除账号及全部关联个人数据 |
| `DELETE` | `/api/admin/users/bulk` | 固定确认后原子永久删除最多 100 个账号及关联数据 |

所有写请求使用会话 Cookie 与 CSRF 校验。管理接口要求当前登录账号具有管理员角色；任何设置读取或错误响应都不得返回明文密钥。

站点外观保存在生产 `state/settings.sqlite3`。公开 `/api/site` 只返回经过长度限制的纯文本、背景版本/尺寸与注册开关；浏览器不渲染管理员输入的 HTML。登录背景不接受外链或 SVG，上传上限 8 MB，服务端校验 JPG/PNG/WebP、静态帧、最小/最大像素后移除元数据并重新编码为 WebP BLOB，图片响应使用内容哈希版本缓存。发布包排除 `state/`，因此部署不会覆盖当前品牌和背景。

三类批量管理都只接受 1–100 个无重复目标并要求管理员会话与 CSRF。账号启停/角色操作及永久删除都不能包含当前登录管理员，且事务结束后必须至少保留一位正常管理员；`DELETE /api/admin/users/bulk` 还要求 `DELETE_SELECTED_USERS`，清理全部账号关联表后才删除 `users`。评论批量删除要求 `DELETE_SELECTED_COMMENTS`，在同一事务内删除评论/反应/通知并清空子回复父引用。论坛软删除要求 `DELETE_SELECTED_FORUM_CONTENT` 并复用状态机；物理清除使用独立 DELETE 接口与 `PURGE_FORUM_CONTENT` / `PURGE_SELECTED_FORUM_CONTENT`，先清理回复、反应、通知和父引用，再删除本体并写 `purge` 审计。任一目标非法时整批回滚。

题库导出接口只读访问当前激活的 `catalog.sqlite3`，逐行输出 occurrence 以控制内存。JSONL 保留结构化选项；CSV 提供 A–H 选项列并对 `= + - @` 开头的单元格进行公式中和。响应禁止缓存和 Nginx 缓冲，普通用户调用返回 403。

个人学习数据导出接口只读取当前会话账号的 `study_goals`、`study_items`、`question_notes`、规范题/文件题进度、`practice_sessions` 和逐题作答记录，以 `record_type` 区分 JSONL 记录；会话记录保留 `archived_at` 当前归档状态。CSV 使用统一列并中和公式前缀。响应返回 `private, no-store` 与 `X-Accel-Buffering: no`，不会包含 `profile_id`、其他用户数据、catalog 内容或密钥。

导入预检单文件上限 16 MB、最多 50,000 行；全局 multipart 上限 18 MB。XLSX 使用标准库流式读取 OOXML，拒绝加密或重复成员、超过 2,000 个压缩成员、DTD/实体声明或 256 MB 解压体积，只读取缓存值且不加载外部链接。每份暂存使用随机 `imp_*` 标识和独立目录，最多保留 30 份且总预算 256 MB。写接口要求管理员会话与 CSRF，文件名不参与服务端路径解析；分析失败或超限时不保留半成品。当前阶段只交付预检与字段映射，不会覆盖现有 64,078 题 catalog。

AI 会话和消息仅存储在 `state/app.sqlite3`，题内会话可绑定当前 occurrence/collection；每次回答最多检索 6 条题库材料并返回来源路径、题目 ID 和 occurrence 引用。流式接口使用 `start`、`delta`、`complete`、`error` SSE 事件，并返回 `Cache-Control: private, no-store`、`X-Accel-Buffering: no` 与 `Vary: Cookie`；兼容忽略 `stream=true` 而返回普通 JSON 的 OpenAI-compatible 网关。只有上游完整结束且会话仍是准备时的最新状态，服务端才在事务中同时保存 user/assistant；停止、断连或失败不保存半截回答，重新生成保留 assistant ID，并以最后消息及旧内容/引用做并发保护。未配置或上游失败时返回安全的 `ai_unavailable`，不会把密钥、供应商原始错误或私有笔记写入响应。完整 AI 页面负责会话管理和全题库检索；刷题答案解析解锁后，在不离开原题的响应式抽屉中恢复本题会话，并提供解析、易错点、知识点拓展和同类题快捷提问。前端逐段渲染回答，停止后恢复原问题草稿，取消 reader 并释放读取锁；回答卡片可复制、继续追问或原位重新生成。

论坛回复的 `parent_reply_id` 只能指向同主题内仍可见的回复，详情返回父回复作者快照并在前端显示回复对象。回复正文最多解析 20 个去重后的有效用户名提及；停用/不存在的账号、作者自己以及已经收到楼主/父回复通知的账号不会再收到重复提及通知。

论坛管理状态与用户内容继续保存在 `state/app.sqlite3`。管理员 API 只接受 `topic` / `reply` 目标和明确原因：PATCH 以原子状态转换执行隐藏、恢复或软删除，DELETE 物理清除正文、回复、反应与相关通知；普通用户不可调用。永久删除主题会同时删除全部回复，永久删除单条回复会解除子回复的父引用。`forum_moderation_log` 兼容迁移后支持 `purge` 动作，只保留不含正文的操作者、目标、前后状态、原因和时间；账号永久删除会匿名化该账号留下的管理员身份快照。

## 验证

```powershell
git diff --check
python -m py_compile app\backend\config.py app\backend\gunicorn_config.py app\backend\main.py
python -m compileall -q app\backend\medical_app scripts
node --check public\app.js
python -m pytest
```

前端改动还必须验证 `390×844` 和 `1440×900`，并覆盖四个排序按钮自身重复点击、题型标准/逆标准、题号正倒、年份新旧、每次点击随机立即显示不同完整排列、随机预览与会话冻结顺序一致、未知年份末尾、原题号与当前进度分离、指定题号开始、四种模式、练习逐题提交与整卷草稿提交、快刷/测试填空题手动保存、测试答完最后一题仍不泄露结果、背题明确完成、未答下一题、左右滑动、题号/AI 抽屉下拉关闭、真实 SSE 逐段到达、停止不落库且恢复草稿、流式重新生成、题内 AI 状态恢复、论坛搜索/三种排序/返回筛选恢复、归档单删/清空确认、模型派生端点预览、题库进度重置、评论发表/删除、收藏切换、错题删除、刷新恢复和未答交卷。

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

- [本地开发](local-development.md)
- [数据与索引架构](data-architecture.md)
- [设置与语义搜索](settings-and-semantic-search.md)
- [发布与部署](deployment.md)
- [项目固定事实](../PROJECT.md)
- [Agent 开发规范](../AGENTS.md)

## 用户反馈

“我的 → 意见反馈”可提交 Bug、功能建议、题库内容或其他意见。反馈只写入本地 `state/app.sqlite3` 并绑定当前账号；管理员在“系统管理 → 用户反馈”中按关键词/状态筛选、查看统计，并将反馈标记为已处理或重新打开。所有写操作使用登录会话和 CSRF 校验。
