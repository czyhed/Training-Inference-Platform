# NIP 参考控制台尺寸与资源租约决策

日期：2026-09-04

## 背景

线上 NIP 控制台实测侧栏 216px、导航项 42px、正文 30px 34px 44px，当前平台统一样式层将部分控件压缩为 38px，造成图标和文字视觉偏小。训练、推理和评测运行时还分别维护优云清理字段，失败路径容易重复释放或把清理失败误报为完成。

## 决策

1. 在 `.workspace-reference-skin-shell` 范围内增加最终尺寸层，不影响旧页面：导航图标固定 18px，导航项 42px，按钮/输入框 40px，图标按钮触控区域至少 32px，正文最大宽度 1440px。
2. 引入 provider-neutral `ResourceLeaseManager` 作为 Fake/运行时适配边界。租约状态严格经过 `acquired -> running -> releasing -> released`，清理异常进入 `cleanup_failed`；只有 provider 明确确认后才能写入 `released`。
3. 使用 owner/task/service ID 和幂等键去重获取；watchdog 只把心跳过期的活动租约标记为 `releasing`，不直接假设远端已删除。

## 边界与数据传输

- 浏览器只读取后端返回的 `resourceState`/`cleanupState`，不接触优云或 SSH。
- 现有任务 JSON/数据库字段保持兼容；租约对象可序列化为任务的远端句柄和清理字段。
- API Key 继续只返回一次明文，状态接口不返回 secret；本轮不改已有路由结构。

## 验收与失败测试

- `backend/test_resource_lifecycle.py` 覆盖训练失败后的清理失败、释放确认、重复释放幂等和 watchdog 过期。
- `lib/reference-console-skin.test.mjs` 固化线上实测尺寸，防止后续样式覆盖回退。
- 目标测试、TypeScript、生产构建和 `git diff --check` 作为发布门禁。

## 回滚

删除本 ADR 对应的 CSS 最终尺寸层和 `backend/resource_lifecycle.py`，保留既有任务/服务字段及前序兼容改动；不删除用户数据和已持久化远端句柄。
