import { flat } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

/** 列出导出目录里的文件（只读，「我的」页用）。下载单个文件仍走 /api/bff/exports/{name} */
export const GET = flat("api", "exports");
