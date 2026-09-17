"""场景包 API 路由（R20-3）—— 只读两个端点，不提供 CRUD。

场景包的写操作走文件系统（用户直接编辑 `source/skills/scenarios/<scenario>/` 下的 md/yaml），
不经 API：这是 R20-3-02「用户可自由修改与自定义场景包」的实现方式，也使"新增场景 = 新建目录、
零代码改动"成立（R20-3-01）。故本模块刻意只有 GET。

无 DB 依赖：场景包不在数据库注册（走 DB 注册会使新增一个场景需要改 Enum + 迁移 + seed 三处
`.py`，直接违反 R20-3-01）。
"""

from fastapi import APIRouter, HTTPException

from app.services.scenario_loader import list_scenarios, resolve_scenario_pack

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get("")
def get_scenarios() -> dict:
    """列出可选中的场景包 + 非法目录登记 + 发现状态。

    每次真扫目录（无缓存），故用户新建/改写场景包后本端点立刻可见。
    """
    return list_scenarios()


@router.get("/{scenario_id}")
def get_scenario(scenario_id: str) -> dict:
    """返回单个场景包的 manifest（含注入用的三份文本内容与厚度度量）。

    id 形状非法 / 包不存在时，loader 会回落兜底包并在 `fallback` 上报 (code, message)。
    此处把"回落"翻译为 404 —— 调用方问的是某个具体场景，返回兜底包内容会掩盖它不存在的事实。
    错误信息只回 code + 场景标识，不回落盘路径细节。
    """
    pack = resolve_scenario_pack(scenario_id)
    fallback = pack.get("fallback")
    if fallback:
        raise HTTPException(
            status_code=404,
            detail={"code": fallback.get("code", ""), "scenario_id": scenario_id,
                    "message": "场景包不可用或标识不合法"},
        )
    return pack
