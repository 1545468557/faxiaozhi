import { flat } from "@/lib/server/bff";
export const dynamic = "force-dynamic";
/** 邀请码注册（POST）。下发的 HttpOnly Cookie 由代理原样透传。 */
export const POST = flat("api", "auth", "register");
