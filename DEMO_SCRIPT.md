# Demo Script — Design CRM System
预计时长：4–5 分钟

---

## 准备工作（录屏前）
- 启动后端：`uvicorn app:app --reload`
- 运行 seed：`python seed.py`
- 浏览器开两个窗口（或 Chrome + 隐身模式）
  - 窗口 A：登录 alice（customer）
  - 窗口 B：登录 carol（salesperson）
- 密码统一：`demo1234`

---

## 开场白（~30 秒）

> "Hi, 我来演示一下我们设计公司 CRM 系统的当前进展。
> 这个系统的目标是把客户沟通、订单管理和生产协调整合到一个平台里，
> 并且接入 AI 助手，让它能根据上下文——比如对话角色和历史订单——给出有针对性的回答。
> 今天我会演示已经实现的核心功能。"

---

## Part 1：角色系统（~40 秒）

**操作**：打开注册页面，指向角色下拉框

> "首先是角色系统。系统支持四种角色：Customer、Salesperson、Production 和 Admin。
> 不同角色登录后看到的界面是不同的——
> 这是 alice，她是一个 Customer，"

**操作**：用 alice 登录，指向侧边栏的角色标签

> "你可以看到侧边栏用户名旁边有一个蓝色的 Customer 标签。
> 角色信息会编码进 JWT token，后端的每个接口都会根据角色做权限控制。"

---

## Part 2：多房间 + Room 类型（~40 秒）

**操作**：指向左侧 room 列表，点进 `alice-carol`

> "左边是 Room 列表。系统支持三种 Room 类型：
> General 普通聊天、Customer-Sales 客户销售对话、Sales-Production 销售生产协调。"

**操作**：指向聊天 header 里的蓝色 `Customer ↔ Sales` 标签

> "这个房间是 alice 和销售 carol 之间的对话 room，
> 你可以看到 header 上有 'Customer ↔ Sales' 的标识。
> 这个 room 类型信息会传给 AI，让 AI 知道自己在什么场景下回答问题。"

---

## Part 3：订单面板（~50 秒）

**操作**：指向 alice 侧边栏底部的 Orders 面板

> "侧边栏下方是订单面板。对于 alice 这个 Customer，
> 她只能看到自己的订单——这里有几条历史订单，
> 可以看到材料、尺寸、数量和状态。"

**操作**：切换到窗口 B，用 carol（salesperson）登录

> "现在切换到 carol 这个 Salesperson 的视角。"

**操作**：指向 carol 的 Orders 面板

> "同样是订单面板，carol 能看到所有客户的订单，
> 包括客户名、订单状态。销售可以在这里追踪所有进行中的单子。
> 订单状态分五档：Draft、Pending、In Production、Completed 和 Cancelled，
> 用不同颜色区分。"

---

## Part 4：上下文感知 AI（~60 秒）

**操作**：在 alice（customer）的 `alice-carol` 房间里输入：
`我之前的乙烯卷材订单现在是什么状态？`

> "现在来演示 AI 助手的上下文感知能力。
> alice 作为 customer，在客户-销售 room 里问她的订单状态。"

**操作**：等 AI 回复，指向回复内容

> "AI 回复了——它知道这是一个客户在问自己的订单，
> 所以回答方式比较友好，而且会参考系统里的历史订单数据给出信息。"

**操作**：切换到 carol 的 `sales-production` 房间，输入：
`UV 打印的最大尺寸和单价是多少？`

> "现在换到 Sales-Production 房间，carol 作为销售问产能相关的问题。"

**操作**：等 AI 回复，指向回复

> "你可以对比一下两个回复的语气和内容——
> 在 sales-production 房间里，AI 的回答更偏向技术参数和报价细节，
> 因为它知道这里是销售和生产团队之间的协调房间。
> 这就是我们说的上下文感知。"

---

## Part 5：聊天功能演示（~40 秒）

**操作**：carol 给 alice 发一条消息，同时让 alice 窗口可见

**操作**：指向 alice 窗口里 alice-carol room 旁边的蓝色数字

> "当 alice 在别的地方时，carol 发来消息，
> 她的 room 旁边会出现未读计数。"

**操作**：carol 发一条 `@alice 你的订单已经确认了！` 的消息

**操作**：指向 alice 窗口里变红的 badge

> "如果消息里包含 @提及，badge 会变红，
> 同时如果页面不在焦点，还会弹出系统通知。"

**操作**：alice 的窗口里开始打字，不要发送

**操作**：指向 carol 窗口底部的打字提示

> "carol 这边能实时看到 alice 正在输入的提示。"

**操作**：hover 一条消息，点 `＋` 按钮，加一个表情回应

> "消息可以加表情回应，支持六种 emoji，实时同步给所有人。"

---

## 收尾（~20 秒）

> "以上就是目前系统的主要功能：角色系统、多类型 Room、订单管理面板、
> 上下文感知 AI、以及一系列实时聊天功能。
> 接下来我们会继续完善 RAG 的检索能力、
> 以及生产状态更新自动同步到客户端的功能。谢谢！"

---

## 备用 Q&A 问题

| 可能被问到 | 怎么回答 |
|---|---|
| AI 的知识库是什么？ | 目前是把最近订单和产能数据拼进 system prompt，下一步会接 LangChain+FAISS 做向量检索 |
| 角色怎么防止乱改？ | 角色存在数据库里，JWT 只是缓存；后端每个接口都用 `current_user.role` 做权限判断 |
| 为什么用 WebSocket 而不是轮询？ | 打字提示、在线状态、实时消息这些都需要低延迟双向通信，轮询做不到 |
| 数据库用的什么？ | MySQL + SQLAlchemy async，ORM 管理所有模型 |
