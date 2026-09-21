import { v3 } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

/** GET /api/bff/v3/chat/{sid} → v3 `GET /api/chat/{sid}`（整条时间线） */
export const GET = v3.route<{ sid: string }>(p => ["api", "chat", p.sid]);
