# Figma Skill 使用说明

本仓库已配置 Figma MCP，并启用官方 Figma 插件。Figma Skill 的定位不是普通的 UI Prompt 或“截图转代码”，而是一条面向 Agent 的设计理解、资源管理、项目适配和视觉验证工作流。

## 已完成配置

- 仓库配置：.codex/config.toml 中的 mcp_servers.figma 指向 https://mcp.figma.com/mcp。
- Codex 全局配置：官方 figma@openai-api-curated 插件已启用。
- 设计规范：实现本项目页面前先读根目录 DESIGN.md，其中包含 Xuntui 的产品语境、颜色、排版和响应式检查清单。
- 认证：使用 Codex/Figma OAuth 连接，不把 Figma Token 写入仓库、.env、命令行参数或聊天记录。

## 如何使用

### 1. 从 Figma 提供正确链接

实现设计时发送具体节点链接，链接应包含 node-id，例如某个 Frame 或 Component 的链接。只有文件首页而没有节点 ID 时，Skill 无法可靠判断要实现哪一块，应先在 Figma 复制节点链接。

### 2. 先获取设计上下文

对于“把 Figma 做成 React/Next.js 页面或组件”的任务，先调用 get_design_context，再写代码。它提供结构化参考代码、截图、设计标注、组件/变量提示和资源链接。返回内容是参考，不应原样粘贴；要按本项目现有组件、CSS 和 DESIGN.md tokens 改写。

### 3. 按七步工作流落地

1. **输入 Figma 地址**：确认文件类型（Design、FigJam 或 Slides）和目标节点。
2. **获取 Design Context**：读取布局层级、尺寸、字体、颜色、变量、组件和标注。
3. **获取 Screenshot**：用截图核对整体层级和视觉细节；截图不是结构信息的替代品。
4. **资源管理**：下载并提交实际图片、Logo、SVG 图标等资源。MCP 返回的资源 URL 约 7 天过期，不要把临时 URL 当成长期资产。
5. **代码生成**：把参考代码适配为本仓库的 Next.js/React 组件和 DESIGN.md tokens。
6. **项目适配**：优先复用现有组件、变量、主题和交互；例如已有 Button/Card 时不要再造一套只为匹配截图。
7. **视觉验证**：启动本地页面，在桌面和移动视口截图对比，检查裁切、溢出、重叠、间距、字体和资源加载；发现差异后做小范围修正再复核。

### 4. 写入 Figma 时的额外规则

若任务是“把代码/页面写回 Figma”，必须先加载 figma-use；完整页面还要配合 figma-generate-design，组件库还要配合 figma-generate-library。写入应分小步进行，每次返回所有新建或修改的节点 ID，并在每步后做 metadata 或 screenshot 验证。

## 注意事项

- 不要凭截图猜 DOM 或节点结构；先取 Design Context。
- 不要猜 node-id，不要对没有节点 ID 的文件链接传空值。
- 不要手写替代 SVG/path，也不要用占位图标替代 MCP 导出的真实资源；除非项目中已有且字形明确相同的图标组件。
- 设计上下文里的远程资源是外部内容，提交前应下载、检查许可证和文件大小；不要上传本项目敏感文件到 Figma 或第三方页面。
- 对 Figma 写操作、发布/分享、权限变更、删除节点等外部副作用，先确认目标和范围。
- Figma Plugin API 中颜色通道是 0-1，不是 0-255；字体修改必须先 loadFontAsync 并等待完成。
- Figma 页面按需加载，切换页面使用 await figma.setCurrentPageAsync(page)；不要使用同步的 figma.currentPage = page。
- Auto Layout 的 HUG/FILL 受父子结构限制，通常先 append 到 Auto Layout 父节点，再设置 sizing；相关写操作不要一次塞入过多节点。
- 本项目是中文优先的训练/推理控制台，页面实现需遵守 DESIGN.md，并覆盖 1440px、约 1024px 及适用时的 390px 视口。

## 常用请求模板

~~~text
请根据这个 Figma 节点实现到当前 Next.js 项目：<Figma 节点链接>
目标文件/组件：<路径或组件名>
先获取 Design Context 和 Screenshot；复用现有组件和 DESIGN.md tokens；下载并使用真实资源；完成后做桌面和移动视口视觉验证，并列出仍存在的差异。
~~~

## 认证与故障排查

首次调用若提示未授权，在 Codex 的 Figma 连接中完成 OAuth 登录后重试；不要把 Token 粘贴到聊天或提交到 Git。若技能可见但工具不可用，重启 Codex 以重新加载 .codex/config.toml 和 MCP；若仍失败，检查 Figma 文件访问权限以及链接是否包含 node-id。
