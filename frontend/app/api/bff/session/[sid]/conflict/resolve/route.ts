import { route } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

export const POST = route<{ sid: string }>(p => ['api', 'session', p.sid, 'conflict', 'resolve']);
