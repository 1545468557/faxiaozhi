import { route } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

export const GET = route<{ name: string }>(p => ['exports', p.name]);
