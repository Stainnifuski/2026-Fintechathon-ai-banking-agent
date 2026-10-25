# NOVA 账单洞察验收

日期：2026-10-25。

## 完成项

- 核实原有查询已经接入，补齐账单页统一提问入口、整理目录、异常分页、商户预览和13类整理/预测设置确认卡。
- 补齐最终回答中的分类依据、建议、标签、退款与互转关联、核对备注及订阅差异说明；已核对异常不再标为待核对。
- 后端确认复用现有账单服务；预览回滚、归属校验、版本冲突、15分钟过期、重复确认、取消、加密存储和多目标队列等待均验证。
- 补齐审计登记和接口清单；删除对话清理卡片；失败响应后原卡可恢复；保存后页面刷新。

## 自动化

新增 `tests/integration/test_nova_bill_insights.py` 共13个用例，全部通过。覆盖真实 SQLite 业务服务、所有13类操作、修改前后账本金额不变、工具证据生成、结构化 bills 路由和原生工具循环。模型输出使用测试替身，不调用付费模型。

扩大 NOVA 回归：`tests/integration/test_nova_*.py`、`tests/unit/test_nova*.py`、grounding、semantic_bindings、bank_boundary：**320 passed / 65 skipped / 1 failed**。失败仅为原有 `test_nova_reads.py::test_offline_business_same_tools` 最后一条断言：无转账会话时，离线复合请求仍留下 `TRANSFER_CONTEXT_REQUIRED` 工具记录。使用本轮修改前 HEAD 中的 `_chat` 与 `offline_business` 重新运行该测试，复现相同失败；没有新增失败。此项属于离线转账流程，本轮未修改。

前一轮账单+NOVA组合回归为 **206 passed / 11 skipped / 同一既有失败**。最后补齐流水默认字段、周期分页后，重点运行新增接入、账单决策 API、账单基础、审计、回答证据测试：**111 passed / 17 skipped**。跳过项为项目已有可选环境测试，包含未提供 PostgreSQL 测试地址的双数据库参数化用例；不声称完成 PostgreSQL 实例验收。

`npm --prefix frontend run build` 通过；保留既有大于500kB的产物体积提示。`compileall` 与 `git diff --check` 通过。测试设置独立内部银行令牌，未读取或展示开发者凭据。

对最后的流水默认字段和周期分页改动，另跑账单读取、增强、决策及银行边界：**40 passed / 1 deselected**；排除的仅为上述已复现的离线转账失败。前端提问排队补齐后再次构建通过。

## 浏览器

使用 Playwright CLI、本机 Chromium 和新建独立虚构数据库 `/tmp/nova-bills-demo-20261025.db`；没有写入项目实际银行数据库。测试脚本：`tests/browser/nova_bill_insights_check.js`。模型 SSE 回复明确模拟；确认卡读取、确认保存和账单刷新走真实本机 API。

11项浏览器检查通过：只发送一次到 NOVA；不再请求 `/bills/messages`；保留已应用10月报告、忽略未提交9月草稿；保留选中流水；预览未保存；本人点击后保存预算并刷新；刷新恢复已完成卡；1440px桌面、390px手机无文档横向溢出；无页面运行时异常。截图已人工检查，手机确认按钮可滚动到可视区域。刷新时现有 Axios 请求取消日志不作为页面异常。

截图保存在忽略目录 `output/playwright/`：`nova-bills-preview-desktop.png`、`nova-bills-preview-mobile.png`、`nova-bills-saved-mobile.png`。

另用 `tests/browser/nova_bill_question_queue_check.js` 阻塞首个模拟模型响应，并连续提交三个账单问题：忙碌期间不并发发送，释放后按原顺序各发送一次，三个问题均保留各自提交时的账单上下文；四项检查通过。

## 运行服务

检查时 Docker 守护进程未运行，不能重建或报告 Compose 容器已更新。浏览器验收使用临时隔离服务；不代表正式数据库或真实模型供应商联调已完成。代码由相关单元、集成及浏览器验证，正式容器生效仍需启动 Docker 后按仓库 AGENTS.md 重建。
