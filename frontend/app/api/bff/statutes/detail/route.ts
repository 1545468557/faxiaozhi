import { proxy } from "@/lib/server/bff";

export const dynamic = "force-dynamic";

/**
 * 取一份法规的全文。
 *
 * 为什么不用 `statutes/[bbbs]/route.ts`：**vinext 的动态路由段在本项目里不工作**
 * （实测 `GET /api/bff/statutes/{bbbs}` 一律 404；`search` / `status` 这两个静态子路径正常，
 * 与阶段 3-1 记录"vinext 不支持 catch-all 路由"是同一类问题）。
 * 因此改用静态路径 + 查询参数，行为与契约不变。
 */
export async function GET(request: Request): Promise<Response> {
  const bbbs = new URL(request.url).searchParams.get("bbbs") ?? "";
  if (!bbbs) {
    return new Response(JSON.stringify({ error: { code: "empty_input", message: "缺少法规编号。" } }), {
      status: 422,
      headers: { "content-type": "application/json" },
    });
  }
  return proxy(request, ["api", "statutes", bbbs]);
}
