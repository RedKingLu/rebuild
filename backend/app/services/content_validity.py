"""内容合法性检查（D-P0-01）——拦"模型工具调用协议原文被当作代码写盘"。

## 为什么只拦协议标记，不做"像不像代码"的启发式

真实规模真跑（项目 `3df5717c-…` / run `run-eab45b`，1018 源文件）后，`output_code/` 里
有 5 个文件的内容是模型 function-call 协议原文而非代码，而写盘路径**只校验路径合法性、
零校验内容合法性**，节点仍报 `completed`。本模块补的就是这一道**确定性硬拦**。

判据设计的两条边界：

1. **只做确定性判据，不做代码质量/语义判断。** "这段文本是不是有效的 C#/Python 代码"属
   识别与理解，按 AGENTS §2.3 / D-063 必须由 LLM 推理，确定性只许用于采集与验证；把它做
   成正则启发式既会误伤又会把判断权从 LLM 手里抢走。
2. **零误伤优先于零漏检。** 误伤（正常代码被拒写）表现为"节点莫名失败"，比漏检更难定位
   （参见 `B-R20-REDACT-URL-NOUSER`：脱敏过度抹除比漏检更难被发现）。因此判据只认那些
   **不可能出现在正常源码里的哨兵串**——全角竖线 `｜`(U+FF5C) 成对包裹的协议 token，且必须
   出现在 `<` / `</` 之后。正常代码里的 `invoke` / `parameter` / `calls` 等英文单词（C# 方法
   调用、csproj/XML 节点、Razor 视图）**一律放行**，因为它们不带这个哨兵结构。

命中即由调用方拒写并 `logger.warning` 发声（公理 3），**不得**降级为"写了但打个标记"——
那会让垃圾文件继续进 `output_code/` 并被下游当作真实产出。
"""

from __future__ import annotations

import re
from typing import Optional

# ── 判据①：真跑实测形态（唯一取证来源，勿凭描述扩写）─────────────────────────
# 实测样本（`grep -rl 'DSML' 工作区/projects/*/output_code`；全量扫描：两个项目共 10 个
# 受污染 output_code 文件 + 11 个 patches 草稿）中出现的全部 6 种变体，成对全角竖线
# `｜｜` 是共同特征：
#   <｜｜DSML｜｜ calls>            </｜｜DSML｜｜ calls>
#   <｜｜DSML｜｜tool_calls>        </｜｜DSML｜｜tool_calls>
#   <｜｜DSML｜｜ invoke name="fs_read">   </｜｜DSML｜｜ invoke>
#   <｜｜DSML｜｜invoke name="list_files"> </｜｜DSML｜｜invoke>
#   <｜｜DSML｜｜ parameter name="path" string="true">  </｜｜DSML｜｜ parameter>
#   <｜｜DSML｜｜parameter name="offset" string="false"> </｜｜DSML｜｜parameter>
# 哨兵名不写死为 `DSML`：写死会在哨兵名变化时静默漏检，而"`<`/`</` 紧跟 `｜｜ident｜｜`"
# 这一结构本身已不可能出现在任何正常源码/配置/文档里。
_DOUBLE_BAR_SENTINEL_RE = re.compile(r"<\s*/?\s*｜｜[A-Za-z0-9_]{1,32}｜｜")

# ── 判据②：同族的单层全角竖线特殊 token 形态 `<｜…｜>` ───────────────────────
# 事实：本轮真跑样本中**未出现**该形态（实测只有判据①的双竖线形态）。
# 依据：返工修复计划 §4 批次 B 明确要求把 `<｜` 列为须拦的标记族（本轮 388 次调用全部走
# 同一 Provider，其特殊 token 家族即此形态）。为守住零误伤，内层限定为 ASCII
# 标识符字符 + `▁`(U+2581) 并要求闭合 `｜>`：中文文档里常见的全角竖线分隔写法
# （如 `**S-1｜加列（不跑迁移）**`）内层为 CJK 且无尖括号闭合，不会命中。
_SINGLE_BAR_SENTINEL_RE = re.compile(r"<\s*/?\s*｜[A-Za-z0-9_▁]{2,32}｜\s*>")

_PROTOCOL_MARKER_RES = (_DOUBLE_BAR_SENTINEL_RE, _SINGLE_BAR_SENTINEL_RE)

# 拒写原因码（调用方回填到 result，便于下游/前端按码识别，不靠文案匹配）。
PROTOCOL_LEAK_REASON_CODE = "protocol_text_leak"


def detect_protocol_leak(content: Optional[str]) -> Optional[str]:
    """纯函数：内容中是否含模型工具调用协议标记。

    命中返回**首个命中的标记原文**（供错误信息与日志定位，标记本身不含敏感值）；
    未命中返回 `None`。非字符串/空内容一律视为未命中（写盘前调用方已做参数校验，
    此处不代替它报错）。
    """
    if not content or not isinstance(content, str):
        return None
    for pattern in _PROTOCOL_MARKER_RES:
        match = pattern.search(content)
        if match:
            return match.group(0)
    return None
