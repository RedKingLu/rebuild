"""R17.5-FOLLOWUP：BYOK 持久凭证自动绑定（修复"前端加 Key 仅进内存、重启即失效"）。

病根：前端"添加 Key"经 set_credential/add_user_provider 仅注入进程内存（volatile，重启丢），
而 CredentialService 持久化的加密凭证从不绑到 provider.credential_ref → 加密凭证成孤儿、
解析退回易变 LLM_API_KEY 兜底 → "刚加能用、重启失效"。

修复：ProviderRegistry._build_provider 在 yaml 无 credential_ref 时，按 provider_ref==
provider_id 自动绑定最新 active DB 加密凭证 → _resolve_key 优先用可跨重启解密的持久 Key。

asyncio_mode=auto。
"""
import pytest

from app.providers.provider_registry import ProviderRegistry
from app.services.credential_service import CredentialService
from app.schemas.credential import CredentialCreate
from app.core.database import get_session


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    # BYOK 加密/解密需要稳定 master key（本测试内固定）
    monkeypatch.setenv("REBUILD_MASTER_KEY", "0123456789abcdef0123456789abcdef")


def _mk_credential(provider_id: str, key: str = "sk-autolink-test-key") -> str:
    db = get_session()
    try:
        cred = CredentialService(db).create(CredentialCreate(
            name=f"provider:{provider_id}", provider_ref=provider_id,
            plaintext_key=key, key_source="user", tenant_id="default"))
        return cred.credential_id
    finally:
        db.close()


def test_autolink_binds_db_credential_to_provider():
    """DB 有 provider_ref 匹配的 active 凭证 → 全新注册表加载自动绑定 credential_ref。

    注：key_source 取决于是否同时存在 env Key（env 命中时 cred_status 由 env 置 configured，
    key_source=env；但 credential_ref 仍被绑定，且 _resolve_key 优先走 credential_ref 解密）。
    本测试断言的核心契约 = credential_ref 被绑定（持久修复的关键），不依赖 env 是否存在。"""
    cid = _mk_credential("deepseek-official")
    reg = ProviderRegistry()
    reg.load()
    p = reg.get_provider("deepseek-official")
    assert p is not None
    assert p.credential_ref == cid                 # 自动绑定持久凭证（核心契约）
    assert p.credential_status == "configured"


def test_autolink_picks_most_recent_active():
    """多条同 provider_ref active 凭证 → 绑定最新一条（created_at desc）。"""
    _mk_credential("agnes-ai", "sk-old")
    newer = _mk_credential("agnes-ai", "sk-new")
    reg = ProviderRegistry()
    reg.load()
    p = reg.get_provider("agnes-ai")
    assert p is not None and p.credential_ref == newer


def test_no_db_credential_load_does_not_crash():
    """无匹配 DB 凭证 → 加载不崩（best-effort），credential_ref 保持 yaml 值（通常空）。"""
    reg = ProviderRegistry()
    reg.load()  # 不应抛错
    p = reg.get_provider("maas-icompify")
    assert p is not None
    # 无 DB 凭证且无 env → credential_ref 空（回退 env 解析路径，不因缺凭证崩溃）
