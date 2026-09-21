import { v3 } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

/** GET /api/bff/v3/bootstrap → v3 `GET /api/bootstrap`（能力与离线状态） */
export const GET = v3.flat("api", "bootstrap");
