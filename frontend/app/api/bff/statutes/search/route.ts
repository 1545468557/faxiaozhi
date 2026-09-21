import { flat } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

/** 法规检索（本地法规库）。查询串原样转发：q / status / limit */
export const GET = flat("api", "statutes", "search");
