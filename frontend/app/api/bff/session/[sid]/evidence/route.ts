import { route } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

export const GET = route<{ sid: string }>(p => ['api', 'session', p.sid, 'evidence']);
