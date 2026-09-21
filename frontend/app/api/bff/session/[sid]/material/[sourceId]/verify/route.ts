import { route } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

export const POST = route<{ sid: string; sourceId: string }>(p => ['api', 'session', p.sid, 'material', p.sourceId, 'verify']);
