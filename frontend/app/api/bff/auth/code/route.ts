import { flat } from "@/lib/server/bff";
export const dynamic = "force-dynamic";
/** 发短信验证码（POST，body: {phone, purpose}）。默认 mock 通道下不真发，验证码写服务端日志。 */
export const POST = flat("api", "auth", "code");
