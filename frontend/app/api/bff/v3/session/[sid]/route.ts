import { v3 } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

/** DELETE /api/bff/v3/session/{sid} → v3 `DELETE /api/session/{sid}` */
export const DELETE = v3.route<{ sid: string }>(p => ["api", "session", p.sid]);
