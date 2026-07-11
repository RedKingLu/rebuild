// Community service — 仅保留 status 探测（R15-R16 返工 A2）。
// 平台内社区镜像页（CommunityPage）已下线，社区入口改为跳独立站点；
// MainNav 在跳转前调用 status() 探测社区服务可用性（不可达则诚实提示，不静默跳死链）。
import { get, unwrap } from "./client";

export const communityService = {
  async status(): Promise<{ status: string; community_available?: boolean }> {
    const env = await get<{ status: string; community_available?: boolean; data?: unknown }>(`/community/status`);
    // connector 直接把状态放在 data 中；不可达时返回 { status: "unreachable" }
    const d = unwrap(env);
    return d as { status: string; community_available?: boolean };
  },
};
