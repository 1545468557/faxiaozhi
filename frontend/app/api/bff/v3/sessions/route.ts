import { v3 } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

/** GET /api/bff/v3/sessions → v3 `GET /api/sessions`（会话列表） */
export const GET = v3.flat("api", "sessions");
