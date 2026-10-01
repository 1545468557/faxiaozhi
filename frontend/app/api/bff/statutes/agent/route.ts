import { flat } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

/** 法规检索 Agent：问题理解、追问与检索结果均由本机后端处理。 */
export const POST = flat("api", "statutes", "agent");
