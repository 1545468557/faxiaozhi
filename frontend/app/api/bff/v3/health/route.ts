import { v3 } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

/** GET /api/bff/v3/health → v3 `GET /api/health`（探活） */
export const GET = v3.flat("api", "health");
