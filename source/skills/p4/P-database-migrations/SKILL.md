---
name: P-database-migrations
description: P4执行 — 信创数据库迁移(Oracle→达梦/GaussDB、MSSQL→openGauss)：schema/对象/数据迁移、expand-contract、分批与校验
metadata:
  series: P
  phase: P4
  category: execution_skill
  status: platform_runtime
  source: ECC skills/database-migrations (MIT, https://github.com/affaan-m/ECC)
  license: MIT
---

# P-database-migrations（信创数据库迁移执行）

## 适用阶段与触发条件
- 阶段：P4 执行（迁移设计已在 P3 排程，含验收判据与回滚预案）。
- 触发：执行 schema 变更、数据迁移、存储过程/触发器/序列转换，或零停机切换。
- 不适用：仅做兼容性评估（P2）；纯应用层 ORM 代码改写（用 P-backend-patterns）。

## 输入
- 源库连接（只读）与对象清单：表、视图、存储过程、触发器、序列、约束、索引。
- 目标库：达梦DM / openGauss / GaussDB / MySQL，连接与权限。
- P3 验收判据卡、回滚脚本、分批策略、停机窗口。

## 执行步骤
1. **类型与方言映射**：用确定性转换工具生成目标 DDL。常见映射：Oracle `NUMBER`→达梦 `NUMBER`/GaussDB `numeric`；`VARCHAR2`→`varchar`；`DATE`→`timestamp`；MSSQL `NVARCHAR`→openGauss `varchar`、`IDENTITY`→序列/`GENERATED`、`GETDATE()`→`now()`。映射表入 Artifact，边界 case 标注。
2. **对象转换（语义部分）**：存储过程/函数/触发器/包从 PL/SQL、T-SQL 改写为目标方言（达梦兼容 Oracle 语法但非全等；GaussDB/openGauss 用 PL/pgSQL）。需语义理解处经 ModelGateway 辅助，产出后必须用 eval 验证逻辑等价，不直接信任模型输出。
3. **expand-contract 演进**：变更分三相——expand（加新结构、双写/兼容旧）→ migrate（回填/迁数据、切读）→ contract（移除旧结构）。每相可独立上线与回滚，避免大爆炸式切换。
4. **分批迁移避免锁表**：大表按主键区间或时间分片，分批 `INSERT ... SELECT`/批量导出导入；控制批大小与事务粒度，避开业务高峰，监控锁与长事务。
5. **校验比对**：每批迁移后做三重校验——行数一致、列级校验和（如分组聚合 hash）一致、关键表抽样逐行对比。差异必须归零或可解释。
6. **只读源库**：全程不写源库；切换前源库保持可回退。
7. **可回滚执行**：每步执行前确认回滚脚本就绪；高风险步骤经 Gate 后再执行。

## 输出 / 产物（Artifact / Evidence）
- 目标 DDL 脚本、对象转换脚本、数据迁移脚本、回滚脚本 — Artifact。
- 类型/方言映射表（含边界 case 处理）— Artifact。
- 校验报告：行数对比、校验和对比、抽样对比结果 — Evidence（实测）。
- 迁移执行日志（批次、耗时、锁等待）— Evidence。

## 质量门 / 验收标准
- 行数、校验和、抽样三项校验全部通过或差异可解释闭环。
- 每步均有可执行回滚脚本，且回滚演练通过。
- 存储过程/触发器/序列在目标库逻辑等价（eval 验证）。
- 迁移过程未对源库产生写操作；目标服务在麒麟/统信 OS 启动冒烟通过。

## 信创迁移要点
- 达梦对 Oracle 语法兼容度高但非 100%（如某些系统包、`ROWNUM` 分页与 hint 差异），逐一验证不臆断。
- openGauss/GaussDB 走 PG 系，`MERGE`、序列、大小写折叠、`||` 拼接、空串与 NULL 语义与 Oracle/MSSQL 不同，须专门覆盖。
- 字符集与排序规则（如 GBK 旧库→UTF-8）需显式处理，避免中文乱码与排序差异。
- 国产 DB 驱动（达梦 JDBC/.NET、GaussDB 驱动）连接串与隔离级别差异，迁移脚本与应用配置同步更新。

## 反例 / 禁止
- 禁止一次性整库切换无 expand-contract 与回滚。
- 禁止不分批直接迁移大表导致长时间锁表。
- 禁止仅凭行数一致即判定迁移成功（须含校验和/抽样）。
- 禁止对源库执行任何写/DDL。
- 禁止直接采信模型转换的存储过程而不做逻辑等价 eval。

## 与平台集成
- 语义转换的模型调用经 ModelGateway，不直连 Provider；模型输出非 Evidence，须 eval 验证。
- 生产库写入、不可逆 DDL、切换动作为 L4-L5，必须用户 Gate 后执行。
- 脚本/映射表/校验报告挂 Project/Run/Stage/Node，登记 Registry，产生 Trace/Audit。
- 自校验通过不替代 D-级 Acceptance；交付前由验收环节复核。

## 参考
- ECC skills/database-migrations（MIT）— expand-contract、零停机、避锁/避数据丢失、多 DB/ORM。
- 文档/04-模型与资源/06-确定性转换资源接入规范.md（机械转换优先）。
- R-数据库施工规范（migration 可回滚、不存明文 Key）；03-Agent与Skill规范 §6/§7。
