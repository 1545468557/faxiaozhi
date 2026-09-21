import { v3 } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

/** POST /api/bff/v3/chat → v3 `POST /api/chat`（SSE 流式） */
export const POST = v3.flat("api", "chat");
