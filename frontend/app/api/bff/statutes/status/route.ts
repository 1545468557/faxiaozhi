import { flat } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

/** 本地法规库的规模与状态分布 */
export const GET = flat("api", "statutes", "status");
