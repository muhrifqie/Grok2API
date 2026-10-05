import { NextRequest, NextResponse } from "next/server";

// ponytail: presence check only for UX redirect — real auth is enforced by the Python backend (HMAC cookie)
export function proxy(req: NextRequest) {
  const authed = req.cookies.has("admin_session");
  const isLogin = req.nextUrl.pathname === "/login";
  if (!authed && !isLogin) return NextResponse.redirect(new URL("/login", req.url));
  if (authed && isLogin) return NextResponse.redirect(new URL("/", req.url));
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/|favicon.ico|admin/api/|v1/).*)"],
};
